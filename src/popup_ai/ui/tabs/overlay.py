"""Overlay tab with annotation input and OBS display status."""

from collections.abc import Callable
from typing import Any

from nicegui import ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import (
    DataViewer,
    format_overlay_event,
)
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class OverlayTab:
    """Overlay tab displaying annotation input and OBS display status."""

    def __init__(
        self,
        state: UIState,
        settings: Settings,
        supervisor_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._state = state
        self._settings = settings
        self._supervisor_getter = supervisor_getter
        self._status_card: StatusCard | None = None
        self._input_viewer: DataViewer | None = None
        self._output_viewer: DataViewer | None = None
        self._slot_cards: dict[int, ui.card] = {}
        # Manual control inputs
        self._text_slot_select: ui.select | None = None
        self._text_input: ui.input | None = None
        self._send_status: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the overlay tab content."""
        stage = self._state.get_stage("overlay")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard("overlay", stage.status if stage else None)
            self._status_card.build()

            # Manual OBS Controls panel (collapsed)
            with ui.expansion("Manual OBS Controls", icon="settings_remote").classes(
                "w-full"
            ):
                with ui.column().classes("gap-4 p-2 w-full"):
                    # Send Text to Slot section
                    ui.label("Send Text to Slot").classes("font-medium")
                    with ui.row().classes("items-end gap-2 w-full"):
                        self._text_slot_select = ui.select(
                            label="Slot",
                            options=[1, 2, 3, 4],
                            value=1,
                        ).classes("w-20")

                        self._text_input = ui.input(
                            label="Text",
                            placeholder="Enter text to display...",
                        ).classes("flex-1")

                        ui.button(
                            "Send",
                            on_click=self._handle_send_text,
                            icon="send",
                        ).props("color=primary")

                    self._send_status = ui.label("").classes("text-caption")

                    ui.separator()

                    # Quick Actions section
                    ui.label("Quick Actions").classes("font-medium")
                    with ui.row().classes("gap-2 flex-wrap"):
                        for slot in range(1, self._settings.overlay.max_slots + 1):
                            ui.button(
                                f"Clear {slot}",
                                on_click=lambda s=slot: self._handle_clear_slot(s),
                            ).props("outline size=sm")

                        ui.button(
                            "Clear All",
                            on_click=self._handle_clear_all,
                            icon="delete_sweep",
                        ).props("color=negative outline")

                        ui.button(
                            "Test All Slots",
                            on_click=self._handle_test_all_slots,
                            icon="science",
                        ).props("color=secondary outline")

                    ui.separator()

                    # Connection section
                    ui.label("Connection").classes("font-medium")
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Reconnect OBS",
                            on_click=self._handle_reconnect,
                            icon="refresh",
                        ).props("outline")

                        ui.button(
                            "Ping OBS",
                            on_click=self._handle_ping,
                            icon="wifi_tethering",
                        ).props("outline")

            # OBS connection info
            with ui.card().classes("w-full"):
                ui.label("OBS Connection").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    ui.label(f"Host: {self._settings.overlay.obs_host}")
                    ui.label(f"Port: {self._settings.overlay.obs_port}")
                    self._connected_label = ui.label("Connected: No")

            # Active slots display
            with ui.card().classes("w-full"):
                ui.label("Active Slots").classes("font-medium")
                with ui.row().classes("gap-2 mt-2"):
                    for slot in range(1, self._settings.overlay.max_slots + 1):
                        slot_card = ui.card().classes(
                            "w-32 h-16 flex items-center justify-center bg-grey-2"
                        )
                        with slot_card:
                            ui.label(f"Slot {slot}").classes("text-caption text-grey")
                        self._slot_cards[slot] = slot_card

            # Input/Output viewers side by side
            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("w-1/2"):
                    self._input_viewer = DataViewer(
                        "Input: Annotations",
                        buffer=stage.input_buffer if stage else None,
                        format_fn=format_overlay_event,
                    )
                    self._input_viewer.build()

                with ui.column().classes("w-1/2"):
                    self._output_viewer = DataViewer(
                        "Output: Display Events",
                        buffer=stage.output_buffer if stage else None,
                        format_fn=format_overlay_event,
                    )
                    self._output_viewer.build()

            # Settings (collapsed)
            with ui.expansion("Overlay Settings", icon="settings").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.input(
                        "OBS Host",
                        value=self._settings.overlay.obs_host,
                        on_change=lambda e: setattr(
                            self._settings.overlay, "obs_host", e.value
                        ),
                    )
                    ui.number(
                        "OBS Port",
                        value=self._settings.overlay.obs_port,
                        on_change=lambda e: setattr(
                            self._settings.overlay, "obs_port", int(e.value)
                        ),
                    )

        return container

    async def _handle_send_text(self) -> None:
        """Handle sending text to OBS slot."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._text_slot_select or not self._text_input:
            return

        slot = int(self._text_slot_select.value)
        text = self._text_input.value

        if not text or not text.strip():
            ui.notify("Please enter text to send", type="warning")
            return

        try:
            success = await supervisor.overlay_send_text.remote(slot, text.strip())
            if success:
                if self._send_status:
                    self._send_status.set_text(f"Sent to slot {slot}: {text[:30]}...")
                ui.notify(f"Text sent to slot {slot}", type="positive")
                self._text_input.set_value("")
            else:
                ui.notify("Failed to send text (OBS not connected?)", type="warning")
                if self._send_status:
                    self._send_status.set_text("OBS not connected")

        except Exception as ex:
            ui.notify(f"Failed to send text: {ex}", type="negative")
            if self._send_status:
                self._send_status.set_text(f"Error: {ex}")

    async def _handle_clear_slot(self, slot: int) -> None:
        """Handle clearing a specific slot."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            success = await supervisor.overlay_clear_slot.remote(slot)
            if success:
                ui.notify(f"Slot {slot} cleared", type="positive")
            else:
                ui.notify("Failed to clear slot", type="warning")

        except Exception as ex:
            ui.notify(f"Failed to clear slot: {ex}", type="negative")

    async def _handle_clear_all(self) -> None:
        """Handle clearing all slots."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            success = await supervisor.overlay_clear_all.remote()
            if success:
                ui.notify("All slots cleared", type="positive")
            else:
                ui.notify("Failed to clear slots", type="warning")

        except Exception as ex:
            ui.notify(f"Failed to clear slots: {ex}", type="negative")

    async def _handle_test_all_slots(self) -> None:
        """Handle testing all slots with sample text."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            for slot in range(1, self._settings.overlay.max_slots + 1):
                await supervisor.overlay_send_text.remote(
                    slot, f"Test Slot {slot}"
                )

            ui.notify("Test text sent to all slots", type="positive")

        except Exception as ex:
            ui.notify(f"Failed to test slots: {ex}", type="negative")

    async def _handle_reconnect(self) -> None:
        """Handle OBS reconnection."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            ui.notify("Reconnecting to OBS...", type="info")
            success = await supervisor.overlay_reconnect.remote()
            if success:
                ui.notify("Connected to OBS", type="positive")
            else:
                ui.notify("Failed to connect to OBS", type="warning")

        except Exception as ex:
            ui.notify(f"Reconnection failed: {ex}", type="negative")

    async def _handle_ping(self) -> None:
        """Handle OBS connection ping."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            connected = await supervisor.overlay_ping.remote()
            if connected:
                ui.notify("OBS is connected", type="positive")
            else:
                ui.notify("OBS is not connected", type="warning")

        except Exception as ex:
            ui.notify(f"Ping failed: {ex}", type="negative")

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "annotation_received" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type in ("display", "clear") and self._output_viewer:
            self._output_viewer.add_event(event)
            # Update slot display
            slot = event.data.get("slot")
            if slot and slot in self._slot_cards:
                card = self._slot_cards[slot]
                card.clear()
                with card:
                    if event.event_type == "display":
                        term = event.data.get("term", "")[:15]
                        ui.label(term).classes("text-caption font-medium")
                        card.classes(remove="bg-grey-2", add="bg-green-1")
                    else:
                        ui.label(f"Slot {slot}").classes("text-caption text-grey")
                        card.classes(remove="bg-green-1", add="bg-grey-2")

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("overlay")
        if stage and self._status_card:
            self._status_card.update(stage.status)
            # Update OBS connection status
            if stage.status and stage.status.stats:
                connected = stage.status.stats.get("obs_connected", False)
                if hasattr(self, "_connected_label"):
                    self._connected_label.set_text(
                        f"Connected: {'Yes' if connected else 'No'}"
                    )

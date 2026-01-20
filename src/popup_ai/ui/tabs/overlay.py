"""Overlay tab with annotation testing and OBS display status."""

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
    """Overlay tab displaying annotation testing and OBS display status."""

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
        # Track discovered slots for dynamic UI
        self._discovered_slots: list[int] = []
        # Manual control inputs
        self._text_slot_select: ui.select | None = None
        self._term_input: ui.input | None = None
        self._explanation_input: ui.textarea | None = None
        self._send_status: ui.label | None = None
        # Dynamic UI containers
        self._slot_select_container: ui.element | None = None
        self._quick_actions_container: ui.element | None = None
        self._active_slots_container: ui.element | None = None
        self._queue_status_label: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the overlay tab content."""
        stage = self._state.get_stage("overlay")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard("overlay", stage.status if stage else None)
            self._status_card.build()

            # Settings (collapsed) - placed high for easy access
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
                    ui.number(
                        "Hold Duration (ms)",
                        value=self._settings.overlay.hold_duration_ms,
                        on_change=lambda e: setattr(
                            self._settings.overlay, "hold_duration_ms", int(e.value)
                        ),
                    )

            # Send Test Annotation panel
            with ui.card().classes("w-full"):
                ui.label("Send Test Annotation").classes("text-h6")
                ui.label(
                    "Inject a test annotation through the full display cycle "
                    "(shows for hold duration, then auto-clears)"
                ).classes("text-caption text-grey mb-2")

                with ui.column().classes("gap-2 w-full"):
                    with ui.row().classes("items-end gap-2 w-full"):
                        self._term_input = ui.input(
                            label="Term",
                            placeholder="e.g., API",
                        ).classes("w-32")

                        ui.button(
                            "Send Test",
                            on_click=self._handle_send_test_annotation,
                            icon="science",
                        ).props("color=primary")

                    self._explanation_input = ui.textarea(
                        label="Explanation",
                        placeholder="e.g., Application Programming Interface - allows software applications to communicate with each other",
                    ).classes("w-full").props("rows=2")

                self._send_status = ui.label("").classes("text-caption mt-2")

            # Manual OBS Controls panel (collapsed)
            with ui.expansion("Manual OBS Controls", icon="settings_remote").classes(
                "w-full"
            ):
                with ui.column().classes("gap-4 p-2 w-full"):
                    # Send Raw Text section
                    ui.label("Send Raw Text to Slot").classes("font-medium")
                    ui.label(
                        "Sends text directly (no auto-clear). Use 'Send Test Annotation' above for full cycle."
                    ).classes("text-caption text-grey")

                    with ui.row().classes("items-start gap-2 w-full"):
                        self._slot_select_container = ui.column().classes("gap-2")
                        with self._slot_select_container:
                            self._text_slot_select = ui.select(
                                label="Slot",
                                options=[],
                                value=None,
                            ).classes("w-24")

                            ui.button(
                                "Send",
                                on_click=self._handle_send_text,
                                icon="send",
                            ).props("outline")

                        self._raw_text_input = ui.textarea(
                            label="Text",
                            placeholder="Enter text to display (supports multiple lines)...",
                        ).classes("flex-1").props("rows=3")

                    ui.separator()

                    # Quick Actions section
                    ui.label("Quick Actions").classes("font-medium")
                    self._quick_actions_container = ui.row().classes("gap-2 flex-wrap")
                    with self._quick_actions_container:
                        # Will be populated dynamically
                        ui.label("No slots discovered").classes("text-caption text-grey")

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
                            "Rediscover Slots",
                            on_click=self._handle_rediscover,
                            icon="search",
                        ).props("outline")

            # OBS connection info
            with ui.card().classes("w-full"):
                ui.label("OBS Connection").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    ui.label(f"Host: {self._settings.overlay.obs_host}")
                    ui.label(f"Port: {self._settings.overlay.obs_port}")
                    self._connected_label = ui.label("Connected: No")
                    self._slots_label = ui.label("Slots: 0")
                    self._queue_status_label = ui.label("Queued: 0")

            # Active slots display
            with ui.card().classes("w-full"):
                ui.label("Active Slots").classes("font-medium")
                self._active_slots_container = ui.row().classes("gap-2 mt-2 flex-wrap")
                with self._active_slots_container:
                    ui.label("No slots discovered yet").classes("text-caption text-grey")

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

        return container

    def _rebuild_slot_ui(self, slots: list[int]) -> None:
        """Rebuild the dynamic slot-based UI elements."""
        if slots == self._discovered_slots:
            return  # No change

        self._discovered_slots = slots

        # Update slot select dropdown
        if self._text_slot_select:
            self._text_slot_select.options = slots if slots else []
            self._text_slot_select.value = slots[0] if slots else None

        # Rebuild quick actions
        if self._quick_actions_container:
            self._quick_actions_container.clear()
            with self._quick_actions_container:
                if slots:
                    for slot in slots:
                        ui.button(
                            f"Clear {slot}",
                            on_click=lambda s=slot: self._handle_clear_slot(s),
                        ).props("outline size=sm")

                    ui.button(
                        "Clear All",
                        on_click=self._handle_clear_all,
                        icon="delete_sweep",
                    ).props("color=negative outline size=sm")

                    ui.button(
                        "Test All Slots",
                        on_click=self._handle_test_all_slots,
                        icon="science",
                    ).props("color=secondary outline size=sm")
                else:
                    ui.label("No slots discovered").classes("text-caption text-grey")

        # Rebuild active slots display
        if self._active_slots_container:
            self._active_slots_container.clear()
            self._slot_cards.clear()
            with self._active_slots_container:
                if slots:
                    for slot in slots:
                        slot_card = ui.card().classes(
                            "w-32 h-16 flex items-center justify-center bg-grey-2"
                        )
                        with slot_card:
                            ui.label(f"Slot {slot}").classes("text-caption text-grey")
                        self._slot_cards[slot] = slot_card
                else:
                    ui.label("No slots discovered yet").classes("text-caption text-grey")

    async def _handle_send_test_annotation(self) -> None:
        """Handle sending a test annotation through the full cycle."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._term_input or not self._explanation_input:
            return

        term = self._term_input.value
        explanation = self._explanation_input.value

        if not term or not term.strip():
            ui.notify("Please enter a term", type="warning")
            return

        if not explanation or not explanation.strip():
            ui.notify("Please enter an explanation", type="warning")
            return

        try:
            success = await supervisor.overlay_inject_annotation.remote(
                term.strip(),
                explanation.strip(),
            )
            if success:
                if self._send_status:
                    duration_s = self._settings.overlay.hold_duration_ms / 1000
                    self._send_status.set_text(
                        f"Sent: {term} (will auto-clear in {duration_s}s)"
                    )
                ui.notify("Test annotation sent", type="positive")
                self._term_input.set_value("")
                self._explanation_input.set_value("")
            else:
                ui.notify("Failed to send annotation (pipeline not ready?)", type="warning")
                if self._send_status:
                    self._send_status.set_text("Failed to send")

        except Exception as ex:
            ui.notify(f"Failed to send annotation: {ex}", type="negative")
            if self._send_status:
                self._send_status.set_text(f"Error: {ex}")

    async def _handle_send_text(self) -> None:
        """Handle sending raw text to OBS slot."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._text_slot_select or not hasattr(self, "_raw_text_input"):
            return

        slot = self._text_slot_select.value
        if slot is None:
            ui.notify("No slot selected", type="warning")
            return

        text = self._raw_text_input.value

        if not text or not text.strip():
            ui.notify("Please enter text to send", type="warning")
            return

        try:
            success = await supervisor.overlay_send_text.remote(int(slot), text.strip())
            if success:
                ui.notify(f"Text sent to slot {slot}", type="positive")
                self._raw_text_input.set_value("")
            else:
                ui.notify("Failed to send text (OBS not connected?)", type="warning")

        except Exception as ex:
            ui.notify(f"Failed to send text: {ex}", type="negative")

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
        """Handle testing all discovered slots with sample annotations."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._discovered_slots:
            ui.notify("No slots discovered", type="warning")
            return

        try:
            # Send test annotations for each slot (they'll use the queue if needed)
            for i, slot in enumerate(self._discovered_slots):
                await supervisor.overlay_inject_annotation.remote(
                    f"Test {slot}",
                    f"This is a test annotation for slot {slot}",
                )

            ui.notify(
                f"Sent {len(self._discovered_slots)} test annotations",
                type="positive",
            )

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

    async def _handle_rediscover(self) -> None:
        """Handle rediscovering OBS slots."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            # Reconnect triggers rediscovery
            ui.notify("Rediscovering slots...", type="info")
            success = await supervisor.overlay_reconnect.remote()
            if success:
                # Get updated slots
                slots = supervisor.overlay_get_discovered_slots.remote()
                import ray
                slots = ray.get(slots)
                self._rebuild_slot_ui(slots)
                ui.notify(f"Found {len(slots)} slot(s)", type="positive")
            else:
                ui.notify("Failed to reconnect to OBS", type="warning")

        except Exception as ex:
            ui.notify(f"Rediscovery failed: {ex}", type="negative")

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "annotation_received" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type in ("display", "clear", "queued") and self._output_viewer:
            self._output_viewer.add_event(event)
            # Update slot display for display/clear events
            if event.event_type in ("display", "clear"):
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

            if stage.status and stage.status.stats:
                # Update OBS connection status
                connected = stage.status.stats.get("obs_connected", False)
                if hasattr(self, "_connected_label"):
                    self._connected_label.set_text(
                        f"Connected: {'Yes' if connected else 'No'}"
                    )

                # Update slots count
                discovered_count = stage.status.stats.get("discovered_slots", 0)
                if hasattr(self, "_slots_label"):
                    self._slots_label.set_text(f"Slots: {discovered_count}")

                # Update queue status
                queued_count = stage.status.stats.get("queued", 0)
                if self._queue_status_label:
                    self._queue_status_label.set_text(f"Queued: {queued_count}")

                # Rebuild slot UI if slot count changed
                # Note: We get actual slot list from supervisor when needed
                if discovered_count != len(self._discovered_slots):
                    if self._supervisor_getter:
                        supervisor = self._supervisor_getter()
                        if supervisor:
                            try:
                                import ray
                                slots = ray.get(
                                    supervisor.overlay_get_discovered_slots.remote()
                                )
                                self._rebuild_slot_ui(slots)
                            except Exception:
                                pass

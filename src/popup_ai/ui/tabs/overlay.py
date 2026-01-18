"""Overlay tab with annotation input and OBS display status."""

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

    def __init__(self, state: UIState, settings: Settings) -> None:
        self._state = state
        self._settings = settings
        self._status_card: StatusCard | None = None
        self._input_viewer: DataViewer | None = None
        self._output_viewer: DataViewer | None = None
        self._slot_cards: dict[int, ui.card] = {}

    def build(self) -> ui.column:
        """Build and return the overlay tab content."""
        stage = self._state.get_stage("overlay")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard("overlay", stage.status if stage else None)
            self._status_card.build()

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

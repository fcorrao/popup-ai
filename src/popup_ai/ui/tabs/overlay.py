"""Overlay tab with annotation testing and browser panel status."""

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
    """Overlay tab displaying annotation testing and browser panel status."""

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
        # Test annotation inputs
        self._term_input: ui.input | None = None
        self._explanation_input: ui.textarea | None = None
        self._send_status: ui.label | None = None
        # Panel status labels
        self._panel_1_count: ui.label | None = None
        self._panel_2_count: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the overlay tab content."""
        stage = self._state.get_stage("overlay")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard("overlay", stage.status if stage else None)
            self._status_card.build()

            # Browser Overlay URLs section
            with ui.card().classes("w-full"):
                ui.label("Browser Overlay URLs").classes("text-h6")
                ui.label(
                    "Add Browser Sources in OBS pointing to these URLs. "
                    "Panel 1 is required, Panel 2 is optional for overflow."
                ).classes("text-caption text-grey mb-2")

                base_url = f"http://localhost:{self._settings.pipeline.ui_port}/static/overlay.html"
                max_entries = self._settings.overlay.max_entries_per_panel
                is_chatlog = self._settings.overlay.chatlog_mode
                mode = "chatlog" if is_chatlog else "single"

                # Build URL params - max only applies to chatlog mode
                if is_chatlog:
                    url_params = f"panel={{panel}}&mode={mode}&max={max_entries}"
                else:
                    url_params = f"panel={{panel}}&mode={mode}"

                # Panel 1 URL
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("Panel 1:").classes("w-16 font-medium")
                    panel1_url = f"{base_url}?{url_params.format(panel=1)}"
                    ui.input(value=panel1_url).classes("flex-1").props("dense readonly")
                    ui.button(
                        icon="content_copy",
                        on_click=lambda u=panel1_url: ui.clipboard.write(u)
                    ).props("flat dense")

                # Panel 2 URL
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("Panel 2:").classes("w-16 font-medium")
                    panel2_url = f"{base_url}?{url_params.format(panel=2)}"
                    ui.input(value=panel2_url).classes("flex-1").props("dense readonly")
                    ui.button(
                        icon="content_copy",
                        on_click=lambda u=panel2_url: ui.clipboard.write(u)
                    ).props("flat dense")

                # Mode info
                with ui.row().classes("gap-4 mt-2 text-caption"):
                    mode = "chatlog" if self._settings.overlay.chatlog_mode else "single"
                    ui.label(f"Mode: {mode}").classes("text-grey")
                    ui.label(f"Max entries/panel: {max_entries}").classes("text-grey")

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
                    ui.checkbox(
                        "Chatlog Mode",
                        value=self._settings.overlay.chatlog_mode,
                        on_change=lambda e: setattr(
                            self._settings.overlay, "chatlog_mode", e.value
                        ),
                    )
                    ui.number(
                        "Max Entries per Panel",
                        value=self._settings.overlay.max_entries_per_panel,
                        min=1,
                        max=20,
                        on_change=lambda e: setattr(
                            self._settings.overlay, "max_entries_per_panel", int(e.value)
                        ),
                    )

            # Send Test Annotation panel
            with ui.card().classes("w-full"):
                ui.label("Send Test Annotation").classes("text-h6")
                ui.label(
                    "Inject a test annotation to the browser panels"
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

            # Panel Status
            with ui.card().classes("w-full"):
                ui.label("Panel Status").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    self._connected_label = ui.label("OBS: Disconnected")
                    self._panel_1_count = ui.label("Panel 1: 0 entries")
                    self._panel_2_count = ui.label("Panel 2: 0 entries")

                with ui.row().classes("gap-2 mt-2"):
                    ui.button(
                        "Reconnect OBS",
                        on_click=self._handle_reconnect,
                        icon="refresh",
                    ).props("outline")

                    ui.button(
                        "Reset Counts",
                        on_click=self._handle_clear_panels,
                        icon="restart_alt",
                    ).props("outline")

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

    async def _handle_send_test_annotation(self) -> None:
        """Handle sending a test annotation."""
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
                    self._send_status.set_text(f"Sent: {term}")
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

    async def _handle_clear_panels(self) -> None:
        """Handle resetting panel counts."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            success = await supervisor.overlay_clear_panels.remote()
            if success:
                ui.notify("Panel counts reset", type="positive")
            else:
                ui.notify("Failed to reset panels", type="warning")

        except Exception as ex:
            ui.notify(f"Failed to reset panels: {ex}", type="negative")

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "annotation_received" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type in ("display", "panels_cleared") and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("overlay")
        if stage and self._status_card:
            self._status_card.update(stage.status)

            if stage.status and stage.status.stats:
                # Update OBS connection status
                connected = stage.status.stats.get("obs_connected", False)
                if hasattr(self, "_connected_label"):
                    status = "Connected" if connected else "Disconnected"
                    self._connected_label.set_text(f"OBS: {status}")

                # Update panel counts
                panel_1 = stage.status.stats.get("panel_1_count", 0)
                panel_2 = stage.status.stats.get("panel_2_count", 0)
                if self._panel_1_count:
                    self._panel_1_count.set_text(f"Panel 1: {panel_1} entries")
                if self._panel_2_count:
                    self._panel_2_count.set_text(f"Panel 2: {panel_2} entries")

"""Annotator tab with transcript input and annotation output."""

from nicegui import ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import (
    DataViewer,
    format_annotation_event,
)
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class AnnotatorTab:
    """Annotator tab displaying transcript input and annotation output."""

    def __init__(self, state: UIState, settings: Settings) -> None:
        self._state = state
        self._settings = settings
        self._status_card: StatusCard | None = None
        self._input_viewer: DataViewer | None = None
        self._output_viewer: DataViewer | None = None

    def build(self) -> ui.column:
        """Build and return the annotator tab content."""
        stage = self._state.get_stage("annotator")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard(
                "annotator", stage.status if stage else None
            )
            self._status_card.build()

            # Input/Output viewers side by side
            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("w-1/2"):
                    self._input_viewer = DataViewer(
                        "Input: Transcripts",
                        buffer=stage.input_buffer if stage else None,
                        format_fn=format_annotation_event,
                    )
                    self._input_viewer.build()

                with ui.column().classes("w-1/2"):
                    self._output_viewer = DataViewer(
                        "Output: Annotations",
                        buffer=stage.output_buffer if stage else None,
                        format_fn=format_annotation_event,
                    )
                    self._output_viewer.build()

            # Cache stats panel
            with ui.card().classes("w-full"):
                ui.label("LLM & Cache Status").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    provider = self._settings.annotator.provider
                    model = self._settings.annotator.model
                    self._llm_label = ui.label(f"Provider: {provider}:{model}")
                    self._cache_hits_label = ui.label("Cache Hits: 0")
                    self._llm_calls_label = ui.label("LLM Calls: 0")

            # Settings (collapsed)
            with ui.expansion("Annotator Settings", icon="settings").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.input(
                        "Provider",
                        value=self._settings.annotator.provider,
                        on_change=lambda e: setattr(
                            self._settings.annotator, "provider", e.value
                        ),
                    )
                    ui.input(
                        "Model",
                        value=self._settings.annotator.model,
                        on_change=lambda e: setattr(
                            self._settings.annotator, "model", e.value
                        ),
                    )

        return container

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "transcript_received" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type == "annotation" and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("annotator")
        if stage and self._status_card:
            self._status_card.update(stage.status)
            # Update cache stats
            if stage.status and stage.status.stats:
                stats = stage.status.stats
                if hasattr(self, "_cache_hits_label"):
                    self._cache_hits_label.set_text(
                        f"Cache Hits: {stats.get('cache_hits', 0)}"
                    )
                if hasattr(self, "_llm_calls_label"):
                    self._llm_calls_label.set_text(
                        f"LLM Calls: {stats.get('llm_calls', 0)}"
                    )

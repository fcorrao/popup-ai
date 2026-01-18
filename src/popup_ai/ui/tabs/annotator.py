"""Annotator tab with transcript input and annotation output."""

from collections.abc import Callable
from typing import Any

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
        # Test panel inputs
        self._transcript_input: ui.textarea | None = None
        self._transcript_status: ui.label | None = None
        self._term_input: ui.input | None = None
        self._explanation_input: ui.textarea | None = None
        self._slot_select: ui.select | None = None
        self._annotation_status: ui.label | None = None

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

            # Test Input panel (collapsed)
            with ui.expansion("Test Input", icon="science").classes("w-full"):
                with ui.tabs().classes("w-full") as test_tabs:
                    ui.tab("inject_transcript", label="Inject Transcript", icon="text_fields")
                    ui.tab("inject_annotation", label="Inject Annotation", icon="label")

                with ui.tab_panels(test_tabs, value="inject_transcript").classes("w-full"):
                    # Transcript injection tab (test LLM annotation)
                    with ui.tab_panel("inject_transcript"):
                        with ui.column().classes("gap-2 p-2 w-full"):
                            ui.label(
                                "Enter text to test LLM annotation"
                            ).classes("text-caption text-grey")

                            self._transcript_input = ui.textarea(
                                placeholder="Enter text for the LLM to annotate..."
                            ).classes("w-full").props("rows=3")

                            with ui.row().classes("gap-2"):
                                ui.button(
                                    "Send to Annotator",
                                    on_click=self._handle_inject_transcript,
                                    icon="send",
                                ).props("color=primary")

                            self._transcript_status = ui.label("").classes("text-caption")

                    # Annotation injection tab (bypass LLM)
                    with ui.tab_panel("inject_annotation"):
                        with ui.column().classes("gap-2 p-2 w-full"):
                            ui.label(
                                "Inject annotation directly (bypasses LLM)"
                            ).classes("text-caption text-grey")

                            with ui.row().classes("gap-4 w-full"):
                                self._term_input = ui.input(
                                    label="Term",
                                    placeholder="e.g., API",
                                ).classes("flex-1")

                                self._slot_select = ui.select(
                                    label="Slot",
                                    options=[1, 2, 3, 4],
                                    value=1,
                                ).classes("w-24")

                            self._explanation_input = ui.textarea(
                                label="Explanation",
                                placeholder="e.g., Application Programming Interface...",
                            ).classes("w-full").props("rows=2")

                            with ui.row().classes("gap-2"):
                                ui.button(
                                    "Inject Annotation",
                                    on_click=self._handle_inject_annotation,
                                    icon="add",
                                ).props("color=primary")

                            self._annotation_status = ui.label("").classes("text-caption")

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

    async def _handle_inject_transcript(self) -> None:
        """Handle transcript injection for LLM annotation test."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._transcript_input:
            return

        text = self._transcript_input.value
        if not text or not text.strip():
            ui.notify("Please enter text to annotate", type="warning")
            return

        try:
            # Inject transcript to be processed by annotator LLM
            supervisor.inject_transcript.remote(text.strip())

            # Update status
            if self._transcript_status:
                self._transcript_status.set_text(f"Sent: {text[:50]}...")

            ui.notify("Transcript sent to annotator", type="positive")

            # Clear input
            self._transcript_input.set_value("")

        except Exception as ex:
            ui.notify(f"Failed to inject transcript: {ex}", type="negative")
            if self._transcript_status:
                self._transcript_status.set_text(f"Error: {ex}")

    async def _handle_inject_annotation(self) -> None:
        """Handle direct annotation injection (bypasses LLM)."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        if not self._term_input or not self._explanation_input or not self._slot_select:
            return

        term = self._term_input.value
        explanation = self._explanation_input.value
        slot = self._slot_select.value

        if not term or not term.strip():
            ui.notify("Please enter a term", type="warning")
            return

        if not explanation or not explanation.strip():
            ui.notify("Please enter an explanation", type="warning")
            return

        try:
            # Inject annotation directly (bypasses LLM)
            supervisor.inject_annotation.remote(
                term.strip(),
                explanation.strip(),
                int(slot),
                5000,  # 5 second display duration
            )

            # Update status
            if self._annotation_status:
                self._annotation_status.set_text(f"Injected: {term} -> slot {slot}")

            ui.notify(f"Annotation '{term}' injected to slot {slot}", type="positive")

            # Clear inputs
            self._term_input.set_value("")
            self._explanation_input.set_value("")

        except Exception as ex:
            ui.notify(f"Failed to inject annotation: {ex}", type="negative")
            if self._annotation_status:
                self._annotation_status.set_text(f"Error: {ex}")

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

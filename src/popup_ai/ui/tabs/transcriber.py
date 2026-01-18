"""Transcriber tab with audio input and transcript output."""

from nicegui import ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import (
    DataViewer,
    format_transcript_event,
)
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class TranscriberTab:
    """Transcriber tab displaying audio input and transcript output."""

    def __init__(self, state: UIState, settings: Settings) -> None:
        self._state = state
        self._settings = settings
        self._status_card: StatusCard | None = None
        self._input_viewer: DataViewer | None = None
        self._output_viewer: DataViewer | None = None

    def build(self) -> ui.column:
        """Build and return the transcriber tab content."""
        stage = self._state.get_stage("transcriber")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard(
                "transcriber", stage.status if stage else None
            )
            self._status_card.build()

            # Input/Output viewers side by side
            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("w-1/2"):
                    self._input_viewer = DataViewer(
                        "Input: Audio Chunks",
                        buffer=stage.input_buffer if stage else None,
                        format_fn=format_transcript_event,
                    )
                    self._input_viewer.build()

                with ui.column().classes("w-1/2"):
                    self._output_viewer = DataViewer(
                        "Output: Transcripts",
                        buffer=stage.output_buffer if stage else None,
                        format_fn=format_transcript_event,
                    )
                    self._output_viewer.build()

            # VAD status panel
            with ui.card().classes("w-full"):
                ui.label("Preprocessing Status").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    vad_status = "Enabled" if self._settings.transcriber.vad_enabled else "Disabled"
                    self._vad_label = ui.label(f"VAD: {vad_status}")
                    self._speech_label = ui.label("Speech Chunks: 0")
                    self._silence_label = ui.label("Silent Chunks: 0")

            # Settings (collapsed)
            with ui.expansion("Transcriber Settings", icon="settings").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.input(
                        "Model",
                        value=self._settings.transcriber.model,
                        on_change=lambda e: setattr(
                            self._settings.transcriber, "model", e.value
                        ),
                    )
                    ui.number(
                        "Chunk Length (s)",
                        value=self._settings.transcriber.chunk_length_s,
                        min=1.0,
                        max=30.0,
                        step=0.5,
                        on_change=lambda e: setattr(
                            self._settings.transcriber, "chunk_length_s", float(e.value)
                        ),
                    )

                    ui.label("Audio Preprocessing").classes("font-medium mt-2")
                    ui.checkbox(
                        "Enable VAD",
                        value=self._settings.transcriber.vad_enabled,
                        on_change=lambda e: setattr(
                            self._settings.transcriber, "vad_enabled", e.value
                        ),
                    )
                    ui.number(
                        "VAD Threshold",
                        value=self._settings.transcriber.vad_threshold,
                        min=0.0,
                        max=1.0,
                        step=0.1,
                        on_change=lambda e: setattr(
                            self._settings.transcriber, "vad_threshold", float(e.value)
                        ),
                    )

        return container

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "audio_received" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type == "transcript" and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("transcriber")
        if stage and self._status_card:
            self._status_card.update(stage.status)
            # Update preprocessing stats
            if stage.status and stage.status.stats:
                stats = stage.status.stats
                if hasattr(self, "_speech_label"):
                    self._speech_label.set_text(
                        f"Speech Chunks: {stats.get('chunks_with_speech', 0)}"
                    )
                if hasattr(self, "_silence_label"):
                    self._silence_label.set_text(
                        f"Silent Chunks: {stats.get('chunks_without_speech', 0)}"
                    )

"""Transcriber tab with audio input and transcript output."""

from collections.abc import Callable
from typing import Any

from nicegui import events, ui

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
        self._upload_status: ui.label | None = None
        self._transcript_input: ui.textarea | None = None
        self._inject_status: ui.label | None = None

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

            # Settings (collapsed) - placed high for easy access
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

            # Test Input panel (collapsed)
            with ui.expansion("Test Input", icon="science").classes("w-full"):
                with ui.tabs().classes("w-full") as test_tabs:
                    ui.tab("inject_audio", label="Inject Audio", icon="audiotrack")
                    ui.tab("inject_transcript", label="Inject Transcript", icon="text_fields")

                with ui.tab_panels(test_tabs, value="inject_audio").classes("w-full"):
                    # Audio injection tab
                    with ui.tab_panel("inject_audio"):
                        with ui.column().classes("gap-2 p-2"):
                            ui.label(
                                "Upload audio file to test transcription"
                            ).classes("text-caption text-grey")

                            with ui.row().classes("items-center gap-4"):
                                ui.upload(
                                    label="Select Audio File",
                                    auto_upload=True,
                                    on_upload=self._handle_audio_upload,
                                ).props('accept=".wav,.mp3,.m4a,.flac,.ogg,.webm"').classes(
                                    "max-w-xs"
                                )

                            self._upload_status = ui.label("").classes("text-caption")

                    # Transcript injection tab (bypass transcription)
                    with ui.tab_panel("inject_transcript"):
                        with ui.column().classes("gap-2 p-2 w-full"):
                            ui.label(
                                "Enter transcript text to bypass transcription"
                            ).classes("text-caption text-grey")

                            self._transcript_input = ui.textarea(
                                placeholder="Enter transcript text to inject..."
                            ).classes("w-full").props("rows=3")

                            with ui.row().classes("gap-2"):
                                ui.button(
                                    "Inject Transcript",
                                    on_click=self._handle_inject_transcript,
                                    icon="send",
                                ).props("color=primary")

                            self._inject_status = ui.label("").classes("text-caption")

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

        return container

    async def _handle_audio_upload(self, e: events.UploadEventArguments) -> None:
        """Handle audio file upload for test injection."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            # Get file info
            filename = e.name
            file_bytes = e.content.read()
            format_ext = filename.rsplit(".", 1)[-1].lower()

            # Convert to PCM
            from popup_ai.audio import convert_to_pcm, get_audio_duration_ms

            pcm_bytes, sample_rate, channels = convert_to_pcm(
                file_bytes,
                format_ext,
                target_sample_rate=self._settings.audio.sample_rate,
                target_channels=self._settings.audio.channels,
            )

            duration_ms = get_audio_duration_ms(pcm_bytes, sample_rate, channels)

            # Inject into pipeline (will go to transcriber via audio queue)
            supervisor.inject_audio.remote(pcm_bytes, sample_rate, channels)

            # Update status
            if self._upload_status:
                self._upload_status.set_text(
                    f"Injected {duration_ms / 1000:.1f}s of audio from {filename}"
                )

            ui.notify(
                f"Injected {duration_ms / 1000:.1f}s of audio for transcription",
                type="positive",
            )

        except Exception as ex:
            ui.notify(f"Failed to process audio: {ex}", type="negative")
            if self._upload_status:
                self._upload_status.set_text(f"Error: {ex}")

    async def _handle_inject_transcript(self) -> None:
        """Handle direct transcript injection."""
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
            ui.notify("Please enter transcript text", type="warning")
            return

        try:
            # Inject transcript directly (bypasses transcription)
            supervisor.inject_transcript.remote(text.strip())

            # Update status
            if self._inject_status:
                self._inject_status.set_text(f"Injected: {text[:50]}...")

            ui.notify("Transcript injected", type="positive")

            # Clear input
            self._transcript_input.set_value("")

        except Exception as ex:
            ui.notify(f"Failed to inject transcript: {ex}", type="negative")
            if self._inject_status:
                self._inject_status.set_text(f"Error: {ex}")

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

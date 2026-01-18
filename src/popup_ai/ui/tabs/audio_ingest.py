"""Audio Ingest tab with ffmpeg status and output log."""

from collections.abc import Callable
from typing import Any

from nicegui import events, ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import DataViewer, format_audio_event
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class AudioIngestTab:
    """Audio Ingest tab displaying ffmpeg status and chunk output."""

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
        self._output_viewer: DataViewer | None = None
        self._upload_status: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the audio ingest tab content."""
        stage = self._state.get_stage("audio_ingest")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard(
                "audio_ingest", stage.status if stage else None
            )
            self._status_card.build()

            # Test Audio Input panel (collapsed)
            with ui.expansion("Test Audio Input", icon="science").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.label("Upload WAV/MP3/M4A/FLAC to inject into pipeline").classes(
                        "text-caption text-grey"
                    )

                    with ui.row().classes("items-center gap-4"):
                        ui.upload(
                            label="Select Audio File",
                            auto_upload=True,
                            on_upload=self._handle_audio_upload,
                        ).props('accept=".wav,.mp3,.m4a,.flac,.ogg,.webm"').classes(
                            "max-w-xs"
                        )

                    self._upload_status = ui.label("").classes("text-caption")

            # ffmpeg info panel
            with ui.card().classes("w-full"):
                ui.label("FFmpeg Configuration").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    ui.label(f"SRT Port: {self._settings.audio.srt_port}")
                    ui.label(f"Latency: {self._settings.audio.srt_latency_ms}ms")
                    ui.label(f"Sample Rate: {self._settings.audio.sample_rate}Hz")
                    ui.label(f"Channels: {self._settings.audio.channels}")

            # Output log
            self._output_viewer = DataViewer(
                "Output: Audio Chunks",
                buffer=stage.output_buffer if stage else None,
                format_fn=format_audio_event,
            )
            self._output_viewer.build()

            # Settings (collapsed)
            with ui.expansion("Audio Settings", icon="settings").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.number(
                        "SRT Port",
                        value=self._settings.audio.srt_port,
                        on_change=lambda e: setattr(
                            self._settings.audio, "srt_port", int(e.value)
                        ),
                    )
                    ui.number(
                        "Latency (ms)",
                        value=self._settings.audio.srt_latency_ms,
                        on_change=lambda e: setattr(
                            self._settings.audio, "srt_latency_ms", int(e.value)
                        ),
                    )

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

            # Inject into pipeline
            supervisor.inject_audio.remote(pcm_bytes, sample_rate, channels)

            # Update status
            if self._upload_status:
                self._upload_status.set_text(
                    f"Injected {duration_ms / 1000:.1f}s of audio from {filename}"
                )

            ui.notify(
                f"Injected {duration_ms / 1000:.1f}s of audio", type="positive"
            )

        except Exception as ex:
            ui.notify(f"Failed to process audio: {ex}", type="negative")
            if self._upload_status:
                self._upload_status.set_text(f"Error: {ex}")

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "chunk_produced" and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("audio_ingest")
        if stage and self._status_card:
            self._status_card.update(stage.status)

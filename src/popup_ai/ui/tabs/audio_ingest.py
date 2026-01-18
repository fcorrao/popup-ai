"""Audio Ingest tab with ffmpeg status and output log."""

from nicegui import ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import DataViewer, format_audio_event
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class AudioIngestTab:
    """Audio Ingest tab displaying ffmpeg status and chunk output."""

    def __init__(self, state: UIState, settings: Settings) -> None:
        self._state = state
        self._settings = settings
        self._status_card: StatusCard | None = None
        self._output_viewer: DataViewer | None = None

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

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "chunk_produced" and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("audio_ingest")
        if stage and self._status_card:
            self._status_card.update(stage.status)

"""Media Ingest tab with ffmpeg status for audio and video capture."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from nicegui import events, ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import DataViewer
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


def format_media_event(event: UIEvent) -> str:
    """Format media ingest events (audio and video)."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "chunk_produced":
        bytes_val = event.data.get("bytes", 0)
        chunk_num = event.data.get("chunk_num", "?")
        return f"[{timestamp}] Audio #{chunk_num}: {bytes_val} bytes"
    elif event.event_type == "frame_produced":
        frame_num = event.data.get("frame_num", "?")
        width = event.data.get("width", "?")
        height = event.data.get("height", "?")
        return f"[{timestamp}] Video #{frame_num}: {width}x{height}"
    return f"[{timestamp}] {event.event_type}"


class AudioIngestTab:
    """Media Ingest tab displaying ffmpeg status for audio and video capture."""

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
        # Stats labels
        self._audio_chunks_label: ui.label | None = None
        self._audio_bytes_label: ui.label | None = None
        self._video_frames_label: ui.label | None = None
        self._video_bytes_label: ui.label | None = None
        self._video_enabled_label: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the media ingest tab content."""
        stage = self._state.get_stage("media_ingest")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard(
                "media_ingest", stage.status if stage else None
            )
            self._status_card.build()

            # Settings panels side by side
            with ui.row().classes("w-full gap-4"):
                # Audio Settings
                with ui.expansion("Audio Settings", icon="audiotrack").classes("flex-1"):
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
                        ui.number(
                            "Sample Rate (Hz)",
                            value=self._settings.audio.sample_rate,
                            on_change=lambda e: setattr(
                                self._settings.audio, "sample_rate", int(e.value)
                            ),
                        )

                # Video Settings
                with ui.expansion("Video Settings", icon="videocam").classes("flex-1"):
                    with ui.column().classes("gap-2 p-2"):
                        ui.checkbox(
                            "Video Extraction Enabled",
                            value=self._settings.video.enabled,
                            on_change=lambda e: setattr(
                                self._settings.video, "enabled", e.value
                            ),
                        )
                        ui.number(
                            "Target FPS",
                            value=self._settings.video.fps,
                            min=0.1,
                            max=10.0,
                            step=0.5,
                            on_change=lambda e: setattr(
                                self._settings.video, "fps", float(e.value)
                            ),
                        )
                        ui.number(
                            "Scale Width",
                            value=self._settings.video.scale_width,
                            min=320,
                            max=1920,
                            step=64,
                            on_change=lambda e: setattr(
                                self._settings.video, "scale_width", int(e.value)
                            ),
                        )
                        ui.select(
                            label="Pixel Format",
                            options=["gray", "rgb24"],
                            value=self._settings.video.pixel_format,
                            on_change=lambda e: setattr(
                                self._settings.video, "pixel_format", e.value
                            ),
                        )

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

            # Stats panels side by side
            with ui.row().classes("w-full gap-4"):
                # Audio stats
                with ui.card().classes("flex-1"):
                    ui.label("Audio Output").classes("font-medium")
                    with ui.column().classes("gap-1 text-caption"):
                        self._audio_chunks_label = ui.label("Chunks Sent: 0")
                        self._audio_bytes_label = ui.label("Bytes: 0")
                        ui.label(f"Sample Rate: {self._settings.audio.sample_rate}Hz")
                        ui.label(f"Channels: {self._settings.audio.channels}")

                # Video stats
                with ui.card().classes("flex-1"):
                    ui.label("Video Output").classes("font-medium")
                    with ui.column().classes("gap-1 text-caption"):
                        self._video_enabled_label = ui.label(
                            f"Enabled: {self._settings.video.enabled}"
                        )
                        self._video_frames_label = ui.label("Frames Sent: 0")
                        self._video_bytes_label = ui.label("Bytes: 0")
                        ui.label(f"Target: {self._settings.video.fps} fps")
                        ui.label(f"Scale: {self._settings.video.scale_width}px wide")

            # Output log (shows both audio and video events)
            self._output_viewer = DataViewer(
                "Output: Media Chunks",
                buffer=stage.output_buffer if stage else None,
                format_fn=format_media_event,
            )
            self._output_viewer.build()

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
            filename = e.file.name
            file_bytes = await e.file.read()
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
        if event.event_type in ("chunk_produced", "frame_produced") and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("media_ingest")
        if stage and self._status_card:
            self._status_card.update(stage.status)

            # Update stats from status
            if stage.status and stage.status.stats:
                stats = stage.status.stats
                if self._audio_chunks_label:
                    self._audio_chunks_label.set_text(
                        f"Chunks Sent: {stats.get('audio_chunks_sent', 0)}"
                    )
                if self._audio_bytes_label:
                    bytes_val = stats.get('audio_bytes_processed', 0)
                    if bytes_val > 1024 * 1024:
                        self._audio_bytes_label.set_text(f"Bytes: {bytes_val / 1024 / 1024:.1f} MB")
                    elif bytes_val > 1024:
                        self._audio_bytes_label.set_text(f"Bytes: {bytes_val / 1024:.1f} KB")
                    else:
                        self._audio_bytes_label.set_text(f"Bytes: {bytes_val}")
                if self._video_enabled_label:
                    # Show more detail about video config status
                    video_enabled = stats.get('video_enabled', False)
                    video_queue = stats.get('video_queue_available', False)
                    video_config = stats.get('video_config_enabled', False)
                    if video_enabled:
                        self._video_enabled_label.set_text("Enabled: Yes")
                    else:
                        self._video_enabled_label.set_text(
                            f"Enabled: No (queue={video_queue}, config={video_config})"
                        )
                if self._video_frames_label:
                    self._video_frames_label.set_text(
                        f"Frames Sent: {stats.get('video_frames_sent', 0)}"
                    )
                if self._video_bytes_label:
                    bytes_val = stats.get('video_bytes_processed', 0)
                    if bytes_val > 1024 * 1024:
                        self._video_bytes_label.set_text(f"Bytes: {bytes_val / 1024 / 1024:.1f} MB")
                    elif bytes_val > 1024:
                        self._video_bytes_label.set_text(f"Bytes: {bytes_val / 1024:.1f} KB")
                    else:
                        self._video_bytes_label.set_text(f"Bytes: {bytes_val}")

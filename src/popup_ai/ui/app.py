"""NiceGUI admin application."""

import asyncio
import contextlib
import logging
from typing import Any

import ray
from nicegui import app, ui

from popup_ai.config import Settings
from popup_ai.messages import ActorStatus, UIEvent

logger = logging.getLogger(__name__)


class PipelineUI:
    """Admin UI for controlling the popup-ai pipeline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.supervisor: Any = None
        self.ui_queue: Any = None
        self._status_timer: asyncio.Task | None = None
        self._event_timer: asyncio.Task | None = None
        self._actor_statuses: dict[str, ActorStatus] = {}
        self._transcripts: list[str] = []
        self._annotations: list[dict] = []

    async def init_supervisor(self) -> None:
        """Initialize the pipeline supervisor."""
        from popup_ai.actors.supervisor import PipelineSupervisor

        self.supervisor = PipelineSupervisor.remote(self.settings)
        self.ui_queue = ray.get(self.supervisor.get_ui_queue.remote())

    def build_ui(self) -> None:
        """Build the admin UI."""
        with ui.header().classes("bg-primary"):
            ui.label("popup-ai Admin").classes("text-h5")
            ui.space()
            with ui.row():
                self.start_btn = ui.button("Start Pipeline", on_click=self.start_pipeline)
                self.start_btn.props("color=positive")
                self.stop_btn = ui.button("Stop Pipeline", on_click=self.stop_pipeline)
                self.stop_btn.props("color=negative")
                self.stop_btn.set_enabled(False)

        with ui.row().classes("w-full gap-4 p-4"):
            # Left column: Actor status
            with ui.column().classes("w-1/3"):
                ui.label("Actor Status").classes("text-h6")
                self.status_container = ui.column().classes("w-full gap-2")
                self._update_status_display()

            # Middle column: Live transcript
            with ui.column().classes("w-1/3"):
                ui.label("Live Transcript").classes("text-h6")
                self.transcript_log = ui.log(max_lines=50).classes("w-full h-64")

            # Right column: Annotations
            with ui.column().classes("w-1/3"):
                ui.label("Recent Annotations").classes("text-h6")
                self.annotation_container = ui.column().classes("w-full gap-2")

        # Settings panel
        with ui.expansion("Settings", icon="settings").classes("w-full"):  # noqa: SIM117
            with ui.grid(columns=2).classes("w-full gap-4 p-4"):
                # Audio settings
                with ui.card().classes("w-full"):
                    ui.label("Audio Ingest").classes("text-subtitle1")
                    ui.number(
                        "SRT Port",
                        value=self.settings.audio.srt_port,
                        on_change=lambda e: setattr(self.settings.audio, "srt_port", int(e.value)),
                    )
                    ui.number(
                        "Latency (ms)",
                        value=self.settings.audio.srt_latency_ms,
                        on_change=lambda e: setattr(
                            self.settings.audio, "srt_latency_ms", int(e.value)
                        ),
                    )

                # Transcriber settings
                with ui.card().classes("w-full"):
                    ui.label("Transcriber").classes("text-subtitle1")
                    ui.input(
                        "Model",
                        value=self.settings.transcriber.model,
                        on_change=lambda e: setattr(self.settings.transcriber, "model", e.value),
                    )

                # Annotator settings
                with ui.card().classes("w-full"):
                    ui.label("Annotator").classes("text-subtitle1")
                    ui.input(
                        "Provider",
                        value=self.settings.annotator.provider,
                        on_change=lambda e: setattr(self.settings.annotator, "provider", e.value),
                    )
                    ui.input(
                        "Model",
                        value=self.settings.annotator.model,
                        on_change=lambda e: setattr(self.settings.annotator, "model", e.value),
                    )

                # Overlay settings
                with ui.card().classes("w-full"):
                    ui.label("OBS Overlay").classes("text-subtitle1")
                    ui.input(
                        "Host",
                        value=self.settings.overlay.obs_host,
                        on_change=lambda e: setattr(self.settings.overlay, "obs_host", e.value),
                    )
                    ui.number(
                        "Port",
                        value=self.settings.overlay.obs_port,
                        on_change=lambda e: setattr(
                            self.settings.overlay, "obs_port", int(e.value)
                        ),
                    )

    def _update_status_display(self) -> None:
        """Update the actor status display."""
        self.status_container.clear()
        with self.status_container:
            actors = ["audio_ingest", "transcriber", "annotator", "overlay"]
            for actor_name in actors:
                status = self._actor_statuses.get(actor_name)
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center"):
                        if status:
                            state = status.state
                            if state == "running":
                                ui.icon("check_circle", color="green")
                            elif state == "error":
                                ui.icon("error", color="red")
                            elif state == "starting":
                                ui.icon("pending", color="orange")
                            else:
                                ui.icon("circle", color="grey")
                        else:
                            ui.icon("circle", color="grey")
                        ui.label(actor_name.replace("_", " ").title())

                    if status and status.stats:
                        with ui.row().classes("text-caption"):
                            for key, value in list(status.stats.items())[:3]:
                                ui.label(f"{key}: {value}")

    async def start_pipeline(self) -> None:
        """Start the pipeline."""
        ui.notify("Starting pipeline...", type="info")
        self.start_btn.set_enabled(False)

        try:
            if self.supervisor is None:
                await self.init_supervisor()

            await self.supervisor.start.remote()
            self.stop_btn.set_enabled(True)
            ui.notify("Pipeline started", type="positive")

            # Start status polling
            self._status_timer = asyncio.create_task(self._poll_status())
            self._event_timer = asyncio.create_task(self._poll_events())

        except Exception as e:
            logger.exception("Failed to start pipeline")
            ui.notify(f"Failed to start: {e}", type="negative")
            self.start_btn.set_enabled(True)

    async def stop_pipeline(self) -> None:
        """Stop the pipeline."""
        ui.notify("Stopping pipeline...", type="info")
        self.stop_btn.set_enabled(False)

        try:
            if self.supervisor:
                await self.supervisor.stop.remote()

            # Cancel polling tasks
            if self._status_timer:
                self._status_timer.cancel()
            if self._event_timer:
                self._event_timer.cancel()

            self._actor_statuses.clear()
            self._update_status_display()
            self.start_btn.set_enabled(True)
            ui.notify("Pipeline stopped", type="positive")

        except Exception as e:
            logger.exception("Failed to stop pipeline")
            ui.notify(f"Failed to stop: {e}", type="negative")
            self.stop_btn.set_enabled(True)

    async def _poll_status(self) -> None:
        """Poll actor status periodically."""
        while True:
            try:
                if self.supervisor:
                    statuses = ray.get(self.supervisor.get_status.remote())
                    self._actor_statuses = statuses
                    self._update_status_display()
            except Exception as e:
                logger.debug(f"Status poll failed: {e}")

            await asyncio.sleep(2)

    async def _poll_events(self) -> None:
        """Poll UI events from actors."""
        while True:
            try:
                if self.ui_queue:
                    while True:
                        try:
                            event = self.ui_queue.get_nowait()
                            if isinstance(event, UIEvent):
                                self._handle_event(event)
                        except Exception:
                            break
            except Exception as e:
                logger.debug(f"Event poll failed: {e}")

            await asyncio.sleep(0.1)

    def _handle_event(self, event: UIEvent) -> None:
        """Handle a UI event from an actor."""
        if event.source == "transcriber" and event.event_type == "transcript":
            text = event.data.get("text", "")
            if text:
                self.transcript_log.push(text)
                self._transcripts.append(text)

        elif event.source == "annotator" and event.event_type == "annotation":
            term = event.data.get("term", "")
            explanation = event.data.get("explanation", "")
            if term:
                self._annotations.append({"term": term, "explanation": explanation})
                self._update_annotations_display()

    def _update_annotations_display(self) -> None:
        """Update the annotations display."""
        self.annotation_container.clear()
        with self.annotation_container:
            for ann in self._annotations[-10:]:  # Show last 10
                with ui.card().classes("w-full"):
                    ui.label(ann["term"]).classes("font-bold")
                    ui.label(ann["explanation"]).classes("text-caption")


def run_app(settings: Settings) -> None:
    """Run the NiceGUI admin app."""
    # Initialize Ray
    ray.init(ignore_reinit_error=True, logging_level=logging.WARNING)

    pipeline_ui = PipelineUI(settings)

    @ui.page("/")
    def index() -> None:
        pipeline_ui.build_ui()

    @app.on_shutdown
    async def shutdown() -> None:
        if pipeline_ui.supervisor:
            with contextlib.suppress(Exception):
                await pipeline_ui.stop_pipeline()
        ray.shutdown()

    ui.run(
        title="popup-ai Admin",
        host="127.0.0.1",
        port=8080,
        reload=False,
        show=True,
    )

"""NiceGUI admin application with tabbed pipeline view."""

import asyncio
import contextlib
import logging
from typing import Any

import ray
from nicegui import app, ui

from popup_ai.config import Settings
from popup_ai.messages import ActorStatus, UIEvent
from popup_ai.ui.components.pipeline_bar import PipelineBar
from popup_ai.ui.state import UIState
from popup_ai.ui.tabs import (
    AnnotatorTab,
    AudioIngestTab,
    OverlayTab,
    OverviewTab,
    TranscriberTab,
)

logger = logging.getLogger(__name__)


class PipelineUI:
    """Admin UI for controlling the popup-ai pipeline with tabbed view."""

    def __init__(
        self,
        settings: Settings,
        dashboard_url: str | None = None,
        logfire_url: str | None = None,
    ) -> None:
        self.settings = settings
        self.dashboard_url = dashboard_url
        self.logfire_url = logfire_url
        self.supervisor: Any = None
        self.ui_queue: Any = None
        self._status_timer: asyncio.Task | None = None
        self._event_timer: asyncio.Task | None = None

        # UI state management
        self._state = UIState()

        # Tab instances
        self._tabs: dict[str, Any] = {}
        self._pipeline_bar: PipelineBar | None = None
        self._tab_panels: ui.tab_panels | None = None

    async def init_supervisor(self) -> None:
        """Initialize the pipeline supervisor."""
        from popup_ai.actors.supervisor import PipelineSupervisor

        self.supervisor = PipelineSupervisor.remote(self.settings)
        self.ui_queue = ray.get(self.supervisor.get_ui_queue.remote())

    def build_ui(self) -> None:
        """Build the admin UI with tabs."""
        # Header
        with ui.header().classes("bg-primary"):
            ui.label("popup-ai Admin").classes("text-h5")

        # Main content
        with ui.column().classes("w-full p-4 gap-4"):
            # Pipeline control bar
            self._pipeline_bar = PipelineBar(
                settings=self.settings,
                on_start=self.start_pipeline,
                on_stop=self.stop_pipeline,
                dashboard_url=self.dashboard_url,
                logfire_url=self.logfire_url,
            )
            self._pipeline_bar.build()

            # Tab navigation
            with ui.tabs().classes("w-full") as tabs:
                ui.tab("overview", label="Overview", icon="dashboard")
                ui.tab("audio_ingest", label="Audio Ingest", icon="mic")
                ui.tab("transcriber", label="Transcriber", icon="record_voice_over")
                ui.tab("annotator", label="Annotator", icon="auto_awesome")
                ui.tab("overlay", label="Overlay", icon="tv")

            # Tab panels
            self._tab_panels = ui.tab_panels(tabs, value="overview").classes("w-full")
            with self._tab_panels:
                with ui.tab_panel("overview"):
                    self._tabs["overview"] = OverviewTab(self._state)
                    self._tabs["overview"].build()

                with ui.tab_panel("audio_ingest"):
                    self._tabs["audio_ingest"] = AudioIngestTab(
                        self._state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    self._tabs["audio_ingest"].build()

                with ui.tab_panel("transcriber"):
                    self._tabs["transcriber"] = TranscriberTab(
                        self._state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    self._tabs["transcriber"].build()

                with ui.tab_panel("annotator"):
                    self._tabs["annotator"] = AnnotatorTab(
                        self._state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    self._tabs["annotator"].build()

                with ui.tab_panel("overlay"):
                    self._tabs["overlay"] = OverlayTab(
                        self._state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    self._tabs["overlay"].build()

    async def start_pipeline(self) -> None:
        """Start the pipeline."""
        ui.notify("Starting pipeline...", type="info")

        try:
            if self.supervisor is None:
                await self.init_supervisor()

            await self.supervisor.start.remote()
            self._state.pipeline_running = True
            if self._pipeline_bar:
                self._pipeline_bar.set_running(True)
            ui.notify("Pipeline started", type="positive")

            # Start polling tasks
            self._status_timer = asyncio.create_task(self._poll_status())
            self._event_timer = asyncio.create_task(self._poll_events())

        except Exception as e:
            logger.exception("Failed to start pipeline")
            ui.notify(f"Failed to start: {e}", type="negative")
            if self._pipeline_bar:
                self._pipeline_bar.set_running(False)

    async def stop_pipeline(self) -> None:
        """Stop the pipeline."""
        ui.notify("Stopping pipeline...", type="info")

        try:
            if self.supervisor:
                await self.supervisor.stop.remote()

            # Cancel polling tasks
            if self._status_timer:
                self._status_timer.cancel()
            if self._event_timer:
                self._event_timer.cancel()

            self._state.pipeline_running = False
            self._state.clear()
            if self._pipeline_bar:
                self._pipeline_bar.set_running(False)
            ui.notify("Pipeline stopped", type="positive")

        except Exception as e:
            logger.exception("Failed to stop pipeline")
            ui.notify(f"Failed to stop: {e}", type="negative")

    async def _poll_status(self) -> None:
        """Poll actor status periodically."""
        while True:
            try:
                if self.supervisor:
                    statuses = await self.supervisor.get_status.remote()
                    self._state.update_statuses(statuses)
                    self._update_tabs(statuses)
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
        # Route to state manager
        self._state.handle_event(event)

        # Route to appropriate tab
        event_to_tab = {
            "chunk_produced": "audio_ingest",
            "audio_received": "transcriber",
            "transcript": "transcriber",
            "transcript_received": "annotator",
            "annotation": "annotator",
            "annotation_received": "overlay",
            "display": "overlay",
            "clear": "overlay",
        }

        tab_name = event_to_tab.get(event.event_type)
        if tab_name and tab_name in self._tabs:
            tab = self._tabs[tab_name]
            if hasattr(tab, "handle_event"):
                tab.handle_event(event)

    def _update_tabs(self, statuses: dict[str, ActorStatus]) -> None:
        """Update all tabs with new status data."""
        # Update overview tab
        if "overview" in self._tabs:
            self._tabs["overview"].update(statuses)

        # Update individual tabs
        for tab_name in ["audio_ingest", "transcriber", "annotator", "overlay"]:
            if tab_name in self._tabs:
                tab = self._tabs[tab_name]
                if hasattr(tab, "update"):
                    tab.update()


def run_app(settings: Settings) -> None:
    """Run the NiceGUI admin app."""
    # Initialize Logfire observability before Ray
    logfire_url: str | None = None
    if settings.logfire.enabled:
        from popup_ai.observability import configure_logfire

        configure_logfire(
            sample_rate=settings.logfire.sample_rate,
            environment=settings.logfire.environment,
        )
        logfire_url = settings.logfire.dashboard_url

    # Initialize Ray with dashboard and log_to_driver for visibility
    context = ray.init(
        ignore_reinit_error=True,
        logging_level=logging.INFO,
        log_to_driver=True,
        include_dashboard=True,
    )

    # Get dashboard URL
    dashboard_url = context.dashboard_url if hasattr(context, "dashboard_url") else None
    if dashboard_url and not dashboard_url.startswith("http"):
        dashboard_url = f"http://{dashboard_url}"

    pipeline_ui = PipelineUI(settings, dashboard_url=dashboard_url, logfire_url=logfire_url)

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

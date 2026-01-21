"""NiceGUI admin application with tabbed pipeline view."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import ray
from nicegui import app, ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.pipeline_bar import PipelineBar
from popup_ai.ui.state import UIState
from popup_ai.ui.tabs import (
    AnnotatorTab,
    AudioIngestTab,
    DiagnosticsTab,
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
        self._supervisor_init_lock = asyncio.Lock()

    async def init_supervisor(self) -> None:
        """Initialize the pipeline supervisor."""
        from popup_ai.actors.supervisor import PipelineSupervisor

        self.supervisor = PipelineSupervisor.remote(self.settings)
        self.ui_queue = ray.get(self.supervisor.get_ui_queue.remote())

    async def _ensure_supervisor(self) -> None:
        """Ensure the supervisor is initialized before polling."""
        if self.supervisor:
            return
        async with self._supervisor_init_lock:
            if self.supervisor:
                return
            await self.init_supervisor()

    def build_ui(self) -> None:
        """Build the admin UI with tabs.

        Each browser client gets its own UI elements and state.
        The supervisor is shared across all clients.
        """
        # Per-client state and UI elements (local variables captured by closures)
        state = UIState()
        tabs: dict[str, Any] = {}
        pipeline_bar_ref: list[PipelineBar | None] = [None]

        # Header
        with ui.header().classes("bg-primary"):
            ui.label("popup-ai Admin").classes("text-h5")

        # Main content
        with ui.column().classes("w-full p-4 gap-4"):
            # Pipeline control bar
            async def on_start():
                await self._do_start_pipeline(state, pipeline_bar_ref[0])

            async def on_stop():
                await self._do_stop_pipeline(state, pipeline_bar_ref[0])

            pipeline_bar = PipelineBar(
                settings=self.settings,
                on_start=on_start,
                on_stop=on_stop,
                dashboard_url=self.dashboard_url,
                logfire_url=self.logfire_url,
            )
            pipeline_bar.build()
            pipeline_bar_ref[0] = pipeline_bar

            # Tab navigation
            with ui.tabs().classes("w-full") as tab_nav:
                ui.tab("overview", label="Overview", icon="dashboard")
                ui.tab("audio_ingest", label="Audio Ingest", icon="mic")
                ui.tab("transcriber", label="Transcriber", icon="record_voice_over")
                ui.tab("annotator", label="Annotator", icon="auto_awesome")
                ui.tab("overlay", label="Overlay", icon="tv")
                ui.tab("diagnostics", label="Diagnostics", icon="monitoring")

            # Tab panels
            with ui.tab_panels(tab_nav, value="overview").classes("w-full"):
                with ui.tab_panel("overview"):
                    tabs["overview"] = OverviewTab(state)
                    tabs["overview"].build()

                with ui.tab_panel("audio_ingest"):
                    tabs["audio_ingest"] = AudioIngestTab(
                        state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    tabs["audio_ingest"].build()

                with ui.tab_panel("transcriber"):
                    tabs["transcriber"] = TranscriberTab(
                        state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    tabs["transcriber"].build()

                with ui.tab_panel("annotator"):
                    tabs["annotator"] = AnnotatorTab(
                        state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    tabs["annotator"].build()

                with ui.tab_panel("overlay"):
                    tabs["overlay"] = OverlayTab(
                        state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    tabs["overlay"].build()

                with ui.tab_panel("diagnostics"):
                    tabs["diagnostics"] = DiagnosticsTab(
                        state,
                        self.settings,
                        supervisor_getter=lambda: self.supervisor,
                    )
                    tabs["diagnostics"].build()

        # Per-client polling timers (capture local state/tabs in closures)
        async def poll_status():
            try:
                if not self.supervisor:
                    await self._ensure_supervisor()

                # Always fetch statuses so the UI can refresh even after reconnects.
                is_running = await self.supervisor.is_running.remote()
                if state.pipeline_running != is_running:
                    state.pipeline_running = is_running
                    if pipeline_bar_ref[0]:
                        pipeline_bar_ref[0].set_running(is_running)
                    if not is_running:
                        state.clear()

                statuses = await self.supervisor.get_status.remote()
                state.update_statuses(statuses)
                all_statuses = {
                    name: stage.status for name, stage in state.stages.items()
                }
                # Update overview tab
                if "overview" in tabs:
                    tabs["overview"].update(all_statuses)
                # Update individual tabs
                for tab_name in ["audio_ingest", "transcriber", "annotator", "overlay", "diagnostics"]:
                    if tab_name in tabs and hasattr(tabs[tab_name], "update"):
                        tabs[tab_name].update()
            except Exception as e:
                logger.debug(f"Status poll failed: {e}")

        async def poll_events():
            try:
                if not self.ui_queue:
                    await self._ensure_supervisor()

                if self.ui_queue:
                    for _ in range(100):
                        try:
                            event = self.ui_queue.get_nowait()
                            if isinstance(event, UIEvent):
                                state.handle_event(event)
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
                                    "sample": "diagnostics",
                                }
                                tab_name = event_to_tab.get(event.event_type)
                                if tab_name and tab_name in tabs:
                                    tab = tabs[tab_name]
                                    if hasattr(tab, "handle_event"):
                                        tab.handle_event(event)
                        except Exception:
                            break
            except Exception as e:
                logger.debug(f"Event poll failed: {e}")

        ui.timer(2.0, poll_status)
        ui.timer(0.1, poll_events)

    async def _do_start_pipeline(self, state: UIState, pipeline_bar: PipelineBar | None) -> None:
        """Start the pipeline."""
        ui.notify("Starting pipeline...", type="info")

        try:
            if self.supervisor is None:
                await self.init_supervisor()

            await self.supervisor.start.remote()
            state.pipeline_running = True
            if pipeline_bar:
                pipeline_bar.set_running(True)
            ui.notify("Pipeline started", type="positive")

        except Exception as e:
            logger.exception("Failed to start pipeline")
            ui.notify(f"Failed to start: {e}", type="negative")
            if pipeline_bar:
                pipeline_bar.set_running(False)

    async def _do_stop_pipeline(self, state: UIState, pipeline_bar: PipelineBar | None) -> None:
        """Stop the pipeline."""
        ui.notify("Stopping pipeline...", type="info")

        try:
            if self.supervisor:
                await self.supervisor.stop.remote()

            state.pipeline_running = False
            state.clear()
            if pipeline_bar:
                pipeline_bar.set_running(False)
            ui.notify("Pipeline stopped", type="positive")

        except Exception as e:
            logger.exception("Failed to stop pipeline")
            ui.notify(f"Failed to stop: {e}", type="negative")


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

    # Forward important env vars to Ray workers
    import os
    env_vars = {}
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CEREBRAS_API_KEY", "LOCAL_LLM_API_KEY"]:
        if key in os.environ:
            env_vars[key] = os.environ[key]

    # Initialize Ray with dashboard and log_to_driver for visibility
    context = ray.init(
        ignore_reinit_error=True,
        logging_level=logging.INFO,
        log_to_driver=True,
        include_dashboard=True,
        dashboard_host="0.0.0.0",
        runtime_env={"env_vars": env_vars} if env_vars else None,
    )

    # Get dashboard URL
    dashboard_url = context.dashboard_url if hasattr(context, "dashboard_url") else None
    if dashboard_url and not dashboard_url.startswith("http"):
        dashboard_url = f"http://{dashboard_url}"

    # Create UI instance (supervisor is shared, UI elements are per-client)
    pipeline_ui = PipelineUI(settings, dashboard_url, logfire_url)

    # Initialize supervisor upfront so it's ready when clients connect

    @app.on_startup
    async def startup():
        await pipeline_ui.init_supervisor()

    @ui.page("/")
    def index() -> None:
        pipeline_ui.build_ui()

    # Serve static files (for browser overlay HTML)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.add_static_files("/static", static_dir)

    # Configure NiceGUI
    ui.run(
        title="popup-ai Admin",
        host=settings.pipeline.ui_host,
        port=settings.pipeline.ui_port,
        reload=False,
        show=False,
    )

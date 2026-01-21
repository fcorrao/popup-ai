"""Pipeline control bar component."""

from nicegui import ui

from popup_ai.config import Settings


class PipelineBar:
    """A control bar for pipeline actor toggles and start/stop controls."""

    def __init__(
        self,
        settings: Settings,
        on_start: callable,
        on_stop: callable,
        dashboard_url: str | None = None,
        logfire_url: str | None = None,
    ) -> None:
        self.settings = settings
        self._on_start = on_start
        self._on_stop = on_stop
        self._dashboard_url = dashboard_url
        self._logfire_url = logfire_url
        self._running = False
        self._starting = False
        self._stopping = False
        self._checkboxes: dict[str, ui.checkbox] = {}
        self._start_btn: ui.button | None = None
        self._stop_btn: ui.button | None = None

    def build(self) -> ui.row:
        """Build and return the pipeline control bar."""
        bar = ui.row().classes("w-full items-center gap-4 p-2 bg-grey-2 rounded")
        with bar:
            ui.label("Pipeline:").classes("font-medium")

            # Actor toggles
            self._checkboxes["audio"] = ui.checkbox(
                "Audio",
                value=self.settings.pipeline.audio_enabled,
                on_change=lambda e: setattr(
                    self.settings.pipeline, "audio_enabled", e.value
                ),
            )
            self._checkboxes["transcriber"] = ui.checkbox(
                "Transcriber",
                value=self.settings.pipeline.transcriber_enabled,
                on_change=lambda e: setattr(
                    self.settings.pipeline, "transcriber_enabled", e.value
                ),
            )
            self._checkboxes["annotator"] = ui.checkbox(
                "Annotator",
                value=self.settings.pipeline.annotator_enabled,
                on_change=lambda e: setattr(
                    self.settings.pipeline, "annotator_enabled", e.value
                ),
            )
            self._checkboxes["overlay"] = ui.checkbox(
                "Overlay",
                value=self.settings.pipeline.overlay_enabled,
                on_change=lambda e: setattr(
                    self.settings.pipeline, "overlay_enabled", e.value
                ),
            )
            self._checkboxes["diagnostics"] = ui.checkbox(
                "Diagnostics",
                value=self.settings.diagnostics.enabled,
                on_change=lambda e: setattr(
                    self.settings.diagnostics, "enabled", e.value
                ),
            )

            ui.space()

            # Documentation link
            ui.link("Docs", "https://fcorrao.github.io/popup-ai/", new_tab=True).classes(
                "text-primary no-underline hover:underline"
            )
            ui.icon("open_in_new", size="xs").classes("text-primary")

            # Ray Dashboard link
            if self._dashboard_url:
                ui.link("Ray Dashboard", self._dashboard_url, new_tab=True).classes(
                    "text-primary no-underline hover:underline"
                )
                ui.icon("open_in_new", size="xs").classes("text-primary")

            # Logfire Dashboard link
            if self._logfire_url:
                ui.link("Logfire", self._logfire_url, new_tab=True).classes(
                    "text-primary no-underline hover:underline"
                )
                ui.icon("open_in_new", size="xs").classes("text-primary")

            # Start/Stop buttons
            self._start_btn = ui.button(
                "Start", icon="play_arrow", on_click=self._handle_start
            ).props("color=positive")
            self._stop_btn = ui.button(
                "Stop", icon="stop", on_click=self._handle_stop
            ).props("color=negative")
            self._stop_btn.set_enabled(False)

        return bar

    def set_running(self, running: bool) -> None:
        """Update the running state and toggle controls."""
        self._running = running
        if self._start_btn:
            self._start_btn.set_enabled(not running)
        if self._stop_btn:
            self._stop_btn.set_enabled(running)
        # Disable checkboxes while running
        for checkbox in self._checkboxes.values():
            checkbox.set_enabled(not running)

    async def _handle_start(self) -> None:
        """Handle start button click."""
        if self._starting or self._running:
            return
        self._starting = True
        if self._start_btn:
            self._start_btn.set_enabled(False)
            self._start_btn.props("loading")
            self._start_btn.text = "Starting..."
        # Disable checkboxes while starting
        for checkbox in self._checkboxes.values():
            checkbox.set_enabled(False)
        try:
            await self._on_start()
        finally:
            self._starting = False
            if self._start_btn:
                self._start_btn.props(remove="loading")
                self._start_btn.text = "Start"

    async def _handle_stop(self) -> None:
        """Handle stop button click."""
        if self._stopping or not self._running:
            return
        self._stopping = True
        if self._stop_btn:
            self._stop_btn.set_enabled(False)
            self._stop_btn.props("loading")
            self._stop_btn.text = "Stopping..."
        try:
            await self._on_stop()
        finally:
            self._stopping = False
            if self._stop_btn:
                self._stop_btn.props(remove="loading")
                self._stop_btn.text = "Stop"

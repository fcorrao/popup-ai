"""Overlay actor for OBS websocket integration (browser sources only)."""

import asyncio
import contextlib
import logging
import sys
import time
from io import StringIO

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import OverlayConfig
from popup_ai.messages import ActorStatus, Annotation, UIEvent
from popup_ai.observability import ensure_logfire_configured

logger = logging.getLogger(__name__)

# Suppress verbose obsws_python logging
logging.getLogger("obsws_python").setLevel(logging.WARNING)


@contextlib.contextmanager
def suppress_stderr():
    """Temporarily suppress stderr output (for noisy library exceptions)."""
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    try:
        yield
    finally:
        sys.stderr = old_stderr


@ray.remote
class OverlayActor:
    """Actor that displays annotations in OBS via browser source broadcasts.

    Features:
    - Broadcasts annotations to browser sources via OBS WebSocket custom events
    - Supports dual panel mode (panel 1 and panel 2)
    - Chatlog mode: overflow from panel 1 to panel 2 when full
    - Single mode: alternates between panels
    - Graceful OBS reconnection
    """

    # Reconnection settings
    INITIAL_RETRY_DELAY_S = 1.0
    MAX_RETRY_DELAY_S = 60.0
    BACKOFF_MULTIPLIER = 2.0

    def __init__(
        self,
        config: OverlayConfig,
        input_queue: Queue,
        ui_queue: Queue | None = None,
    ) -> None:
        self.config = config
        self.input_queue = input_queue
        self.ui_queue = ui_queue
        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._running = False
        self._process_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._annotations_displayed = 0
        self._obs_client = None
        self._obs_connected = False
        self._logger = logging.getLogger("popup_ai.actors.overlay")

        # Panel tracking for dual panel mode
        self._current_panel = 1  # For single mode alternation
        self._panel_1_count = 0  # Entries on panel 1 (for chatlog overflow)
        self._panel_2_count = 0  # Entries on panel 2 (for chatlog overflow)

        # Reconnection state
        self._retry_count = 0
        self._next_retry_delay_s = self.INITIAL_RETRY_DELAY_S
        self._next_retry_time: float | None = None
        self._reconnecting = False

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "annotations_displayed": self._annotations_displayed,
            "obs_connected": self._obs_connected,
            "panel_1_count": self._panel_1_count,
            "panel_2_count": self._panel_2_count,
            "retry_count": self._retry_count,
        }
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        if self._next_retry_time and not self._obs_connected:
            stats["next_retry_in_s"] = max(0, round(self._next_retry_time - time.time(), 1))
        if self._reconnecting:
            stats["reconnecting"] = True
        return ActorStatus(
            name="overlay",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the overlay actor."""
        if self._state == "running":
            return

        ensure_logfire_configured()
        with logfire.span("overlay.start"):
            self._logger.info("Starting overlay actor")
            self._state = "starting"
            self._error = None

            try:
                await self._connect_obs()
                self._state = "running"
                self._running = True
                self._start_time = time.time()
                self._process_task = asyncio.create_task(self._process_loop())
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
                self._publish_ui_event("started", {"obs_connected": self._obs_connected})
                logfire.info(
                    "overlay started",
                    obs_connected=self._obs_connected,
                    obs_host=self.config.obs_host,
                )
            except Exception as e:
                self._state = "error"
                self._error = str(e)
                logfire.exception("Failed to start overlay")
                self._publish_ui_event("error", {"message": str(e)})
                raise

    async def stop(self) -> None:
        """Stop the overlay actor."""
        if self._state == "stopped":
            return

        with logfire.span("overlay.stop"):
            self._logger.info("Stopping overlay actor")
            self._running = False

            # Cancel tasks
            for task in [self._process_task, self._reconnect_task]:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

            # Disconnect OBS
            self._disconnect_obs()

            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            logfire.info("overlay stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    def _disconnect_obs(self) -> None:
        """Disconnect from OBS websocket."""
        if self._obs_client:
            with contextlib.suppress(Exception):
                self._obs_client.disconnect()
            self._obs_client = None
        self._obs_connected = False

    def _reset_retry_state(self) -> None:
        """Reset retry state after successful connection."""
        self._retry_count = 0
        self._next_retry_delay_s = self.INITIAL_RETRY_DELAY_S
        self._next_retry_time = None
        self._error = None

    def _schedule_retry(self) -> None:
        """Schedule next retry with exponential backoff."""
        self._retry_count += 1
        self._next_retry_time = time.time() + self._next_retry_delay_s
        self._logger.info(
            f"OBS reconnect scheduled in {self._next_retry_delay_s:.1f}s "
            f"(attempt {self._retry_count})"
        )
        # Increase delay for next time (exponential backoff)
        self._next_retry_delay_s = min(
            self._next_retry_delay_s * self.BACKOFF_MULTIPLIER,
            self.MAX_RETRY_DELAY_S,
        )

    def _handle_connection_error(self, operation: str, error: Exception) -> None:
        """Handle OBS connection error - mark disconnected and schedule retry."""
        if self._obs_connected:
            self._logger.warning(f"OBS connection lost during {operation}: {error}")
            self._obs_connected = False
            self._error = f"Connection lost: {error}"
            self._publish_ui_event("connection_lost", {"error": str(error)})
            self._schedule_retry()

    async def _connect_obs(self) -> None:
        """Connect to OBS websocket."""
        self._reconnecting = True
        try:
            from obsws_python import ReqClient

            self._obs_client = ReqClient(
                host=self.config.obs_host,
                port=self.config.obs_port,
                password=self.config.obs_password or "",
            )
            self._obs_connected = True
            self._reset_retry_state()
            self._logger.info(f"Connected to OBS at {self.config.obs_host}:{self.config.obs_port}")
            self._publish_ui_event("obs_connected", {
                "host": self.config.obs_host,
                "port": self.config.obs_port,
            })

        except ImportError:
            self._logger.warning("obsws-python not installed, overlay will use mock mode")
            self._obs_connected = False
        except Exception as e:
            self._logger.warning(f"Failed to connect to OBS: {e}")
            self._obs_connected = False
            self._error = f"Connection failed: {e}"
            self._schedule_retry()
        finally:
            self._reconnecting = False

    async def _reconnect_loop(self) -> None:
        """Background loop for automatic OBS reconnection with exponential backoff."""
        while self._running:
            try:
                await asyncio.sleep(1.0)  # Check every second

                # Skip if already connected or reconnecting
                if self._obs_connected or self._reconnecting:
                    continue

                # Check if it's time to retry
                if self._next_retry_time and time.time() >= self._next_retry_time:
                    attempt = self._retry_count + 1
                    self._logger.info(f"Attempting OBS reconnection (attempt {attempt})...")
                    self._disconnect_obs()
                    await self._connect_obs()

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in reconnect loop")

    async def _process_loop(self) -> None:
        """Main processing loop."""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                # Get annotation from queue with timeout (in executor to avoid blocking)
                try:
                    annotation = await loop.run_in_executor(
                        None, lambda: self.input_queue.get(block=True, timeout=0.5)
                    )
                except Exception:
                    continue

                if isinstance(annotation, Annotation):
                    self._logger.info(f"Received annotation from queue: {annotation.term}")
                    # Publish annotation_received event for UI
                    self._publish_ui_event("annotation_received", {
                        "term": annotation.term,
                        "explanation": annotation.explanation,
                    })
                    await self._display_annotation(annotation)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in overlay loop")
                await asyncio.sleep(0.1)

    def _get_target_panel(self, is_chatlog: bool) -> int:
        """Determine which panel to send the annotation to.

        Args:
            is_chatlog: Whether we're in chatlog mode

        Returns:
            Panel number (1 or 2)
        """
        if is_chatlog:
            # Chatlog mode: overflow from panel 1 to panel 2
            max_per_panel = self.config.max_entries_per_panel
            if self._panel_1_count < max_per_panel:
                return 1
            elif self._panel_2_count < max_per_panel:
                return 2
            else:
                # Both panels full - reset panel 1 (oldest entries removed by HTML)
                self._panel_1_count = 0
                return 1
        else:
            # Single mode: alternate between panels
            panel = self._current_panel
            self._current_panel = 2 if self._current_panel == 1 else 1
            return panel

    async def _display_annotation(self, annotation: Annotation) -> None:
        """Display an annotation by broadcasting to browser sources."""
        # Determine target panel based on mode
        is_chatlog = self.config.chatlog_mode
        panel = self._get_target_panel(is_chatlog)

        # Update panel counts for chatlog mode
        if is_chatlog:
            if panel == 1:
                self._panel_1_count += 1
            else:
                self._panel_2_count += 1

        # Broadcast to browser sources
        self._broadcast_annotation(
            term=annotation.term,
            definition=annotation.explanation,
            panel=panel,
        )

        self._annotations_displayed += 1
        self._publish_ui_event("display", {
            "panel": panel,
            "term": annotation.term,
            "explanation": annotation.explanation,
        })

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="overlay",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

    def _broadcast_annotation(self, term: str, definition: str, panel: int = 1) -> None:
        """Broadcast annotation to browser sources via OBS BroadcastCustomEvent.

        Args:
            term: The term being defined
            definition: The definition/explanation
            panel: Target panel number (1 or 2)
        """
        if not self._obs_client or not self._obs_connected:
            self._logger.warning(
                f"Cannot broadcast: obs_client={self._obs_client is not None}, "
                f"connected={self._obs_connected}"
            )
            return

        def _broadcast():
            self._obs_client.broadcast_custom_event({
                "eventData": {
                    "panel": panel,
                    "term": term,
                    "definition": definition,
                    "timestamp": time.time(),
                }
            })

        try:
            _broadcast()
            self._logger.info(
                f"Broadcast to panel {panel}: [{term}] {definition[:50]}..."
            )
        except Exception as e:
            self._logger.error(f"Failed to broadcast custom event: {e}")
            self._handle_connection_error("broadcast", e)

    # ========== Public Methods for Direct Control ==========

    async def send_text(self, text: str, panel: int = 1) -> bool:
        """Send text directly to a browser panel.

        Args:
            text: Text to display
            panel: Target panel (1 or 2)

        Returns:
            True if text was sent successfully, False otherwise
        """
        if panel not in (1, 2):
            self._logger.warning(f"Invalid panel {panel}, must be 1 or 2")
            return False

        self._broadcast_annotation("", text, panel=panel)
        self._publish_ui_event("display", {
            "panel": panel,
            "term": text[:20] if len(text) > 20 else text,
            "explanation": "",
            "direct": True,
        })
        return True

    async def clear_panels(self) -> None:
        """Reset panel counts (browser sources handle their own clearing)."""
        self._panel_1_count = 0
        self._panel_2_count = 0
        self._current_panel = 1
        self._publish_ui_event("panels_cleared", {})

    async def reconnect(self) -> bool:
        """Force immediate reconnection to OBS websocket.

        Returns:
            True if reconnection successful, False otherwise
        """
        self._logger.info("Force reconnection requested, resetting backoff...")

        # Reset backoff state for fresh start
        self._retry_count = 0
        self._next_retry_delay_s = self.INITIAL_RETRY_DELAY_S
        self._next_retry_time = None

        # Disconnect existing client
        self._disconnect_obs()

        # Attempt reconnection
        try:
            await self._connect_obs()
            if self._obs_connected:
                self._publish_ui_event("reconnected", {"obs_connected": True})
            return self._obs_connected
        except Exception as e:
            self._logger.error(f"OBS reconnection failed: {e}")
            self._publish_ui_event("reconnect_failed", {"error": str(e)})
            return False

    def get_panel_status(self) -> dict:
        """Get current panel status.

        Returns:
            Dict with panel counts and configuration
        """
        return {
            "chatlog_mode": self.config.chatlog_mode,
            "max_entries_per_panel": self.config.max_entries_per_panel,
            "panel_1_count": self._panel_1_count,
            "panel_2_count": self._panel_2_count,
            "current_panel": self._current_panel,
        }

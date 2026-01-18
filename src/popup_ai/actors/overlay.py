"""Overlay actor for OBS websocket integration."""

import asyncio
import contextlib
import logging
import time

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import OverlayConfig
from popup_ai.messages import ActorStatus, Annotation, UIEvent

logger = logging.getLogger(__name__)


@ray.remote
class OverlayActor:
    """Actor that displays annotations in OBS via websocket.

    Features:
    - Manages 4 overlay slots with scheduling
    - Auto-hides annotations after display duration
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
        self._cleanup_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._annotations_displayed = 0
        self._obs_client = None
        self._obs_connected = False
        self._slots: dict[int, dict] = {}  # slot -> {annotation, hide_at}
        self._logger = logging.getLogger("popup_ai.actors.overlay")

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
            "active_slots": len(self._slots),
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
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                # Start background reconnection task (handles auto-reconnect)
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
            for task in [self._process_task, self._cleanup_task, self._reconnect_task]:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

            # Clear all slots
            await self._clear_all_slots()

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

            # Initialize overlay sources if needed
            await self._init_overlay_sources()

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

    async def _init_overlay_sources(self) -> None:
        """Initialize OBS overlay text sources."""
        if not self._obs_client:
            return

        # Create text sources for each slot if they don't exist
        for slot in range(1, self.config.max_slots + 1):
            source_name = f"popup-ai-slot-{slot}"
            try:
                # Check if source exists
                self._obs_client.get_input_settings(source_name)
            except Exception:
                # Source doesn't exist, create it
                self._logger.debug(f"Creating overlay source: {source_name}")
                try:
                    self._obs_client.create_input(
                        self.config.scene_name,
                        source_name,
                        "text_gdiplus_v2",  # Windows
                        {
                            "text": "",
                            "font": {"face": "Arial", "size": 24},
                        },
                        True,
                    )
                except Exception:
                    # Try macOS text source
                    try:
                        self._obs_client.create_input(
                            self.config.scene_name,
                            source_name,
                            "text_ft2_source_v2",  # macOS/Linux
                            {
                                "text": "",
                                "font": {"face": "Arial", "size": 24},
                            },
                            True,
                        )
                    except Exception as e:
                        self._logger.warning(f"Failed to create source {source_name}: {e}")

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
                    # Publish annotation_received event for UI
                    self._publish_ui_event("annotation_received", {
                        "term": annotation.term,
                        "explanation": annotation.explanation,
                        "slot": annotation.slot,
                    })
                    await self._display_annotation(annotation)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in overlay loop")
                await asyncio.sleep(0.1)

    async def _cleanup_loop(self) -> None:
        """Loop to clean up expired annotations."""
        while self._running:
            try:
                await asyncio.sleep(0.5)
                now = time.time() * 1000

                # Check for expired slots
                expired = [slot for slot, info in self._slots.items() if info["hide_at"] <= now]

                for slot in expired:
                    await self._clear_slot(slot)
                    del self._slots[slot]

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in cleanup loop")

    async def _display_annotation(self, annotation: Annotation) -> None:
        """Display an annotation in the appropriate slot."""
        slot = annotation.slot
        text = f"{annotation.term}: {annotation.explanation}"

        # Store slot info
        self._slots[slot] = {
            "annotation": annotation,
            "hide_at": time.time() * 1000 + annotation.display_duration_ms,
        }

        # Update OBS source
        if self._obs_client and self._obs_connected:
            source_name = f"popup-ai-slot-{slot}"
            try:
                self._obs_client.set_input_settings(
                    source_name,
                    {"text": text},
                    True,
                )
            except Exception as e:
                self._handle_connection_error("display_annotation", e)

        self._annotations_displayed += 1
        self._publish_ui_event("display", {
            "slot": slot,
            "term": annotation.term,
            "explanation": annotation.explanation,
        })
        self._logger.debug(f"Displayed annotation in slot {slot}: {annotation.term}")

    async def _clear_slot(self, slot: int) -> None:
        """Clear a slot."""
        if self._obs_client and self._obs_connected:
            source_name = f"popup-ai-slot-{slot}"
            try:
                self._obs_client.set_input_settings(
                    source_name,
                    {"text": ""},
                    True,
                )
            except Exception as e:
                self._handle_connection_error("clear_slot", e)

        self._publish_ui_event("clear", {"slot": slot})

    async def _clear_all_slots(self) -> None:
        """Clear all slots."""
        for slot in range(1, self.config.max_slots + 1):
            await self._clear_slot(slot)
        self._slots.clear()

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

    # ========== Public Methods for Direct Control ==========

    async def send_text(self, slot: int, text: str) -> bool:
        """Send text directly to an OBS overlay slot.

        This bypasses the normal annotation flow and directly updates the OBS source.

        Args:
            slot: Slot number (1-4)
            text: Text to display

        Returns:
            True if text was sent successfully, False otherwise
        """
        if not (1 <= slot <= self.config.max_slots):
            self._logger.warning(f"Invalid slot {slot}, must be 1-{self.config.max_slots}")
            return False

        if self._obs_client and self._obs_connected:
            source_name = f"popup-ai-slot-{slot}"
            try:
                self._obs_client.set_input_settings(
                    source_name,
                    {"text": text},
                    True,
                )
                self._publish_ui_event("display", {
                    "slot": slot,
                    "term": text[:20] if len(text) > 20 else text,
                    "explanation": "",
                    "direct": True,
                })
                self._logger.debug(f"Direct text sent to slot {slot}: {text[:30]}...")
                return True
            except Exception as e:
                self._handle_connection_error("send_text", e)
                return False
        else:
            self._logger.debug("OBS not connected, cannot send text")
            return False

    async def clear_slot(self, slot: int) -> None:
        """Clear a specific overlay slot (public wrapper).

        Args:
            slot: Slot number (1-4)
        """
        if not (1 <= slot <= self.config.max_slots):
            self._logger.warning(f"Invalid slot {slot}, must be 1-{self.config.max_slots}")
            return

        await self._clear_slot(slot)
        # Remove from tracking if present
        if slot in self._slots:
            del self._slots[slot]

    async def clear_all(self) -> None:
        """Clear all overlay slots (public wrapper)."""
        await self._clear_all_slots()

    async def reconnect(self) -> bool:
        """Force immediate reconnection to OBS websocket.

        Resets backoff state and attempts connection immediately.
        Use this for user-initiated reconnection from UI.

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

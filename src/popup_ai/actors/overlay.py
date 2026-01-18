"""Overlay actor for OBS websocket integration."""

import asyncio
import contextlib
import logging
import time

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
        self._annotations_displayed = 0
        self._obs_client = None
        self._obs_connected = False
        self._slots: dict[int, dict] = {}  # slot -> {annotation, hide_at}
        self._logger = logging.getLogger("popup_ai.actors.overlay")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "annotations_displayed": self._annotations_displayed,
            "obs_connected": self._obs_connected,
            "active_slots": len(self._slots),
        }
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
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
            self._publish_ui_event("started", {"obs_connected": self._obs_connected})
            self._logger.info("Overlay actor started")
        except Exception as e:
            self._state = "error"
            self._error = str(e)
            self._logger.exception("Failed to start overlay")
            self._publish_ui_event("error", {"message": str(e)})
            raise

    async def stop(self) -> None:
        """Stop the overlay actor."""
        if self._state == "stopped":
            return

        self._logger.info("Stopping overlay actor")
        self._running = False

        # Cancel tasks
        for task in [self._process_task, self._cleanup_task]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Clear all slots
        await self._clear_all_slots()

        # Disconnect OBS
        if self._obs_client:
            with contextlib.suppress(Exception):
                self._obs_client.disconnect()
            self._obs_client = None
            self._obs_connected = False

        self._state = "stopped"
        self._start_time = None
        self._publish_ui_event("stopped", {})
        self._logger.info("Overlay actor stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    async def _connect_obs(self) -> None:
        """Connect to OBS websocket."""
        try:
            from obsws_python import ReqClient

            self._obs_client = ReqClient(
                host=self.config.obs_host,
                port=self.config.obs_port,
                password=self.config.obs_password or "",
            )
            self._obs_connected = True
            self._logger.info(f"Connected to OBS at {self.config.obs_host}:{self.config.obs_port}")

            # Initialize overlay sources if needed
            await self._init_overlay_sources()

        except ImportError:
            self._logger.warning("obsws-python not installed, overlay will use mock mode")
            self._obs_connected = False
        except Exception as e:
            self._logger.warning(f"Failed to connect to OBS: {e}")
            self._obs_connected = False

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
        while self._running:
            try:
                # Get annotation from queue with timeout
                try:
                    annotation = self.input_queue.get(block=True, timeout=0.5)
                except Exception:
                    continue

                if isinstance(annotation, Annotation):
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
                self._logger.warning(f"Failed to update OBS source: {e}")

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
                self._logger.warning(f"Failed to clear OBS source: {e}")

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

"""Overlay actor for OBS websocket integration."""

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
        self._logger = logging.getLogger("popup_ai.actors.overlay")

        # Slot management - discovered from OBS, not auto-created
        self._discovered_slots: list[int] = []  # Sorted list of available slot numbers
        self._slot_item_ids: dict[int, int] = {}  # slot -> OBS sceneItemId (for visibility control)
        self._slot_widths: dict[int, float] = {}  # slot -> viewport width in pixels (for scroll calc)
        self._active_slots: dict[int, dict] = {}  # slot -> {annotation, hide_at}
        self._annotation_queue: list[Annotation] = []  # Queue for when all slots are busy

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
            "discovered_slots": len(self._discovered_slots),
            "active_slots": len(self._active_slots),
            "queued": len(self._annotation_queue),
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

            # Discover existing overlay sources (no auto-creation)
            await self._discover_overlay_sources()

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

    async def _discover_overlay_sources(self) -> None:
        """Discover existing OBS overlay text sources matching 'popup-ai-slot-*'.

        Does NOT auto-create sources. Works with whatever slots the user has created.
        On discovery, all slots are hidden (visibility off) until an annotation is shown.
        Also discovers the width of each slot for scroll speed calculation.
        """
        if not self._obs_client:
            return

        self._discovered_slots = []
        self._slot_item_ids = {}
        self._slot_widths = {}

        # First check if the target scene exists
        try:
            with suppress_stderr():
                scenes = self._obs_client.get_scene_list()
            scene_names = [s["sceneName"] for s in scenes.scenes]
            if self.config.scene_name not in scene_names:
                self._logger.warning(
                    f"OBS scene '{self.config.scene_name}' not found. "
                    f"Available scenes: {scene_names}. "
                    "No overlay sources will be used."
                )
                return
        except Exception as e:
            self._logger.warning(f"Could not verify OBS scene: {e}")
            return

        # Get list of existing sources in the scene
        try:
            with suppress_stderr():
                scene_items = self._obs_client.get_scene_item_list(self.config.scene_name)
            # Build a map of source name -> scene item id
            source_to_item_id = {
                item["sourceName"]: item["sceneItemId"]
                for item in scene_items.scene_items
            }
        except Exception as e:
            self._logger.warning(f"Could not get scene items: {e}")
            return

        # Find sources matching 'popup-ai-slot-*' pattern
        import re
        slot_pattern = re.compile(r"^popup-ai-slot-(\d+)$")

        for source_name, item_id in source_to_item_id.items():
            match = slot_pattern.match(source_name)
            if match:
                slot_num = int(match.group(1))
                self._discovered_slots.append(slot_num)
                self._slot_item_ids[slot_num] = item_id

        # Sort slots so we always use lower-numbered slots first
        self._discovered_slots.sort()

        if self._discovered_slots:
            self._logger.info(
                f"Discovered {len(self._discovered_slots)} overlay slot(s): "
                f"{['popup-ai-slot-' + str(s) for s in self._discovered_slots]}"
            )
            # Get width of each slot for scroll speed calculation, and hide them
            for slot in self._discovered_slots:
                item_id = self._slot_item_ids.get(slot)
                if item_id:
                    width = self._get_slot_width(item_id)
                    self._slot_widths[slot] = width
                    self._logger.debug(f"Slot {slot} width: {width}px")
                await self._set_slot_visible(slot, False)
            self._logger.debug("All slots hidden on discovery")
        else:
            self._logger.warning(
                f"No 'popup-ai-slot-*' sources found in scene '{self.config.scene_name}'. "
                "Please create text sources named 'popup-ai-slot-1', 'popup-ai-slot-2', etc. "
                "in OBS to enable overlay functionality."
            )

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
        """Loop to clean up expired annotations and process queue."""
        while self._running:
            try:
                await asyncio.sleep(0.5)
                now = time.time() * 1000

                # Check for expired slots
                expired = [
                    slot for slot, info in self._active_slots.items()
                    if info["hide_at"] <= now
                ]

                for slot in expired:
                    await self._clear_slot(slot)
                    del self._active_slots[slot]

                    # Check queue and display next annotation if available
                    if self._annotation_queue:
                        next_annotation = self._annotation_queue.pop(0)
                        self._logger.debug(
                            f"Slot {slot} freed, displaying queued annotation: {next_annotation.term}"
                        )
                        await self._show_in_slot(slot, next_annotation)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in cleanup loop")

    def _get_first_available_slot(self) -> int | None:
        """Get the first slot that is not currently displaying.

        Always prefers the lowest-numbered available slot.
        Returns None if all slots are busy.
        """
        for slot in self._discovered_slots:
            if slot not in self._active_slots:
                return slot
        return None

    async def _display_annotation(self, annotation: Annotation) -> None:
        """Display an annotation using smart slot selection.

        - Always uses the first available (invisible) slot
        - Queues annotation if all slots are busy
        - Does not use round-robin; prefers reusing slot 1 when possible
        """
        if not self._discovered_slots:
            self._logger.warning("No overlay slots available, cannot display annotation")
            return

        # Find first available slot
        slot = self._get_first_available_slot()

        if slot is None:
            # All slots are busy - queue the annotation
            self._annotation_queue.append(annotation)
            self._logger.debug(
                f"All {len(self._discovered_slots)} slot(s) busy, "
                f"queued annotation: {annotation.term} (queue size: {len(self._annotation_queue)})"
            )
            self._publish_ui_event("queued", {
                "term": annotation.term,
                "queue_size": len(self._annotation_queue),
            })
            return

        # Display in the selected slot
        await self._show_in_slot(slot, annotation)

    async def _set_slot_visible(self, slot: int, visible: bool) -> None:
        """Set visibility of a slot's OBS source.

        Args:
            slot: Slot number
            visible: True to show, False to hide
        """
        if not self._obs_client or not self._obs_connected:
            return

        item_id = self._slot_item_ids.get(slot)
        if item_id is None:
            self._logger.warning(f"No scene item ID for slot {slot}, cannot set visibility")
            return

        try:
            self._obs_client.set_scene_item_enabled(
                scene_name=self.config.scene_name,
                item_id=item_id,
                enabled=visible,
            )
        except Exception as e:
            self._handle_connection_error("set_visibility", e)

    def _get_slot_width(self, item_id: int) -> float:
        """Get the width of a slot's source from OBS.

        Uses GetSceneItemTransform to get the source bounds.
        Falls back to config default if not discoverable.

        Args:
            item_id: The OBS scene item ID

        Returns:
            Width in pixels
        """
        if not self._obs_client or not self._obs_connected:
            return self.config.scroll_viewport_width_px

        try:
            transform = self._obs_client.get_scene_item_transform(
                scene_name=self.config.scene_name,
                item_id=item_id,
            )
            # The transform has sourceWidth (base) and width (after scale)
            # We want the displayed width
            if hasattr(transform, "scene_item_transform"):
                t = transform.scene_item_transform
                # Try to get the bounded/cropped width first
                width = t.get("width") or t.get("sourceWidth")
                if width and width > 0:
                    return float(width)
        except Exception as e:
            self._logger.debug(f"Could not get slot width from OBS: {e}")

        return self.config.scroll_viewport_width_px

    def _calculate_scroll_speed(self, text: str, viewport_width: float, duration_ms: int) -> float:
        """Calculate the scroll speed needed for text to fully traverse the viewport.

        The text needs to:
        1. Enter from the right (travel = text_width to be fully visible)
        2. Exit on the left (travel = viewport_width to fully exit)

        Total travel = text_width + viewport_width
        Speed = total_travel / duration

        Args:
            text: The text to display
            viewport_width: Width of the visible area in pixels
            duration_ms: How long the annotation will be visible

        Returns:
            Scroll speed in pixels per second, clamped to min/max config values
        """
        # Estimate text width based on character count
        text_width = len(text) * self.config.scroll_char_width_px

        # Total distance the text needs to travel
        total_travel = text_width + viewport_width

        # Calculate speed (pixels per second)
        duration_s = duration_ms / 1000.0
        if duration_s <= 0:
            return self.config.scroll_min_speed

        speed = total_travel / duration_s

        # Clamp to configured min/max
        speed = max(self.config.scroll_min_speed, min(speed, self.config.scroll_max_speed))

        return speed

    def _set_scroll_filter_speed(self, slot: int, speed: float) -> None:
        """Update the scroll filter speed for a slot.

        Args:
            slot: Slot number
            speed: Scroll speed in pixels per second (positive = scroll left)
        """
        if not self._obs_client or not self._obs_connected:
            return

        source_name = f"popup-ai-slot-{slot}"
        try:
            self._obs_client.set_source_filter_settings(
                source_name=source_name,
                filter_name=self.config.scroll_filter_name,
                filter_settings={"speed_x": speed},
                overlay=True,  # Merge with existing settings
            )
        except Exception as e:
            # Filter might not exist - that's OK, just log debug
            self._logger.debug(f"Could not set scroll filter for slot {slot}: {e}")

    async def _show_in_slot(self, slot: int, annotation: Annotation) -> None:
        """Actually display an annotation in a specific slot.

        Calculates scroll speed based on text length and duration,
        updates the text content, sets scroll filter, and makes the slot visible.
        """
        # Prepend spaces so the first character doesn't scroll off immediately
        text = f"    {annotation.term}: {annotation.explanation}"

        # Store slot info
        self._active_slots[slot] = {
            "annotation": annotation,
            "hide_at": time.time() * 1000 + annotation.display_duration_ms,
        }

        # Update OBS source text AND make visible
        if self._obs_client and self._obs_connected:
            source_name = f"popup-ai-slot-{slot}"
            try:
                # Calculate scroll speed based on text length and duration
                viewport_width = self._slot_widths.get(
                    slot, self.config.scroll_viewport_width_px
                )
                scroll_speed = self._calculate_scroll_speed(
                    text, viewport_width, annotation.display_duration_ms
                )

                # Set scroll filter speed before showing
                self._set_scroll_filter_speed(slot, scroll_speed)

                # Update the text content
                self._obs_client.set_input_settings(
                    source_name,
                    {"text": text},
                    True,
                )
                # Make the slot visible
                await self._set_slot_visible(slot, True)

                self._logger.debug(
                    f"Slot {slot}: text={len(text)} chars, "
                    f"viewport={viewport_width}px, speed={scroll_speed:.1f}px/s"
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
        """Hide a slot (does not clear text content).

        The text remains in place but the element is made invisible.
        This is more efficient than clearing text since we'll just
        update the text and show again on next annotation.
        """
        await self._set_slot_visible(slot, False)
        self._publish_ui_event("clear", {"slot": slot})

    async def _clear_all_slots(self) -> None:
        """Hide all discovered slots and clear the queue."""
        for slot in self._discovered_slots:
            await self._clear_slot(slot)
        self._active_slots.clear()
        self._annotation_queue.clear()

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

    async def send_text(self, slot: int, text: str, duration_ms: int | None = None) -> bool:
        """Send text directly to an OBS overlay slot.

        This bypasses the normal annotation flow and directly updates the OBS source.
        Calculates scroll speed, updates text, and makes the slot visible.

        Args:
            slot: Slot number (must be one of the discovered slots)
            text: Text to display
            duration_ms: Optional duration for scroll speed calculation (defaults to config)

        Returns:
            True if text was sent successfully, False otherwise
        """
        if slot not in self._discovered_slots:
            self._logger.warning(
                f"Slot {slot} not available. Discovered slots: {self._discovered_slots}"
            )
            return False

        if self._obs_client and self._obs_connected:
            source_name = f"popup-ai-slot-{slot}"
            # Prepend spaces so the first character doesn't scroll off immediately
            padded_text = f"    {text}"
            try:
                # Calculate scroll speed based on text length (including padding)
                viewport_width = self._slot_widths.get(
                    slot, self.config.scroll_viewport_width_px
                )
                effective_duration = duration_ms or self.config.hold_duration_ms
                scroll_speed = self._calculate_scroll_speed(
                    padded_text, viewport_width, effective_duration
                )

                # Set scroll filter speed
                self._set_scroll_filter_speed(slot, scroll_speed)

                # Update text content
                self._obs_client.set_input_settings(
                    source_name,
                    {"text": padded_text},
                    True,
                )
                # Make the slot visible
                await self._set_slot_visible(slot, True)

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
        """Hide a specific overlay slot (public wrapper).

        Hides the slot (makes it invisible) and removes it from active tracking.

        Args:
            slot: Slot number (must be one of the discovered slots)
        """
        if slot not in self._discovered_slots:
            self._logger.warning(
                f"Slot {slot} not available. Discovered slots: {self._discovered_slots}"
            )
            return

        await self._clear_slot(slot)
        # Remove from tracking if present
        if slot in self._active_slots:
            del self._active_slots[slot]

    async def clear_all(self) -> None:
        """Hide all overlay slots (public wrapper)."""
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

    def get_discovered_slots(self) -> list[int]:
        """Get the list of discovered slot numbers.

        Returns:
            Sorted list of slot numbers (e.g., [1, 2, 3])
        """
        return list(self._discovered_slots)

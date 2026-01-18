"""Base actor class with common functionality."""

import logging
import time
from abc import ABC, abstractmethod

import ray

from popup_ai.messages import ActorStatus, UIEvent

logger = logging.getLogger(__name__)


class BaseActor(ABC):
    """Base class for all pipeline actors.

    Provides:
    - Lifecycle management (start, stop, health check)
    - Logging with actor context
    - UI event publishing
    - Error handling patterns
    """

    def __init__(self, name: str, ui_queue: ray.util.queue.Queue | None = None) -> None:
        self.name = name
        self.ui_queue = ui_queue
        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._stats: dict = {}
        self._running = False
        self._logger = logging.getLogger(f"popup_ai.actors.{name}")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = self._stats.copy()
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name=self.name,
            state=self._state,
            error=self._error,
            stats=stats,
        )

    def start(self) -> None:
        """Start the actor."""
        if self._state == "running":
            return
        self._logger.info(f"Starting {self.name}")
        self._state = "starting"
        self._error = None
        try:
            self._on_start()
            self._state = "running"
            self._running = True
            self._start_time = time.time()
            self._publish_ui_event("started", {})
            self._logger.info(f"{self.name} started")
        except Exception as e:
            self._state = "error"
            self._error = str(e)
            self._logger.exception(f"Failed to start {self.name}")
            self._publish_ui_event("error", {"message": str(e)})
            raise

    def stop(self) -> None:
        """Stop the actor gracefully."""
        if self._state == "stopped":
            return
        self._logger.info(f"Stopping {self.name}")
        self._running = False
        try:
            self._on_stop()
        except Exception as e:
            self._logger.exception(f"Error during {self.name} shutdown")
            self._error = str(e)
        finally:
            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            self._logger.info(f"{self.name} stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source=self.name,
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            self._logger.debug("UI queue full, dropping event")

    @abstractmethod
    def _on_start(self) -> None:
        """Called when actor starts. Override to initialize resources."""
        pass

    @abstractmethod
    def _on_stop(self) -> None:
        """Called when actor stops. Override to cleanup resources."""
        pass

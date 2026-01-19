"""UI state management with per-stage buffers."""

import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from popup_ai.messages import ActorStatus, UIEvent

logger = logging.getLogger(__name__)


@dataclass
class StageState:
    """State for a single pipeline stage."""

    name: str
    status: ActorStatus | None = None
    input_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    output_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    stats: dict[str, Any] = field(default_factory=dict)

    def add_input(self, event: UIEvent) -> None:
        """Add an input event to the buffer."""
        self.input_buffer.append(event)

    def add_output(self, event: UIEvent) -> None:
        """Add an output event to the buffer."""
        self.output_buffer.append(event)

    def update_status(self, status: ActorStatus) -> None:
        """Update the actor status."""
        self.status = status
        if status.stats:
            self.stats.update(status.stats)


class UIState:
    """Central state management for the admin UI."""

    STAGES = ["audio_ingest", "transcriber", "annotator", "overlay"]

    # Maps event_type to (stage, buffer_type) for routing
    EVENT_ROUTING: dict[str, tuple[str, str]] = {
        # Audio ingest outputs
        "chunk_produced": ("audio_ingest", "output"),
        # Transcriber inputs/outputs
        "audio_received": ("transcriber", "input"),
        "transcript": ("transcriber", "output"),
        # Annotator inputs/outputs
        "transcript_received": ("annotator", "input"),
        "annotation": ("annotator", "output"),
        # Overlay inputs/outputs
        "annotation_received": ("overlay", "input"),
        "display": ("overlay", "output"),
        "clear": ("overlay", "output"),
    }

    def __init__(self) -> None:
        self.stages: dict[str, StageState] = {
            name: StageState(name=name) for name in self.STAGES
        }
        self.pipeline_running = False
        self._event_callbacks: list[Callable[[UIEvent], None]] = []

    def get_stage(self, name: str) -> StageState | None:
        """Get stage state by name."""
        return self.stages.get(name)

    def update_statuses(self, statuses: dict[str, ActorStatus]) -> None:
        """Update all actor statuses."""
        for name, status in statuses.items():
            if name in self.stages:
                self.stages[name].update_status(status)

    def handle_event(self, event: UIEvent) -> None:
        """Route a UI event to the appropriate stage buffer."""
        # Route by event_type first
        if event.event_type in self.EVENT_ROUTING:
            stage_name, buffer_type = self.EVENT_ROUTING[event.event_type]
            stage = self.stages.get(stage_name)
            if stage:
                if buffer_type == "input":
                    stage.add_input(event)
                else:
                    stage.add_output(event)

        # Also route by source for status events
        elif event.source in self.stages:
            stage = self.stages[event.source]
            if event.event_type in ("started", "stopped", "error"):
                stage.stats["last_event"] = event.event_type
                if event.event_type == "error":
                    stage.stats["last_error"] = event.data.get("message", "Unknown error")

        # Notify callbacks
        for callback in self._event_callbacks:
            with contextlib.suppress(Exception):
                callback(event)

    def on_event(self, callback: Callable[[UIEvent], None]) -> None:
        """Register a callback for UI events."""
        self._event_callbacks.append(callback)

    def clear(self) -> None:
        """Clear all state."""
        for stage in self.stages.values():
            stage.status = None
            stage.input_buffer.clear()
            stage.output_buffer.clear()
            stage.stats.clear()
        self.pipeline_running = False

    def get_overview_stats(self) -> dict[str, Any]:
        """Get summary stats for the overview tab."""
        stats = {
            "pipeline_running": self.pipeline_running,
            "stages_running": 0,
            "stages_error": 0,
            "total_events": 0,
        }

        for stage in self.stages.values():
            if stage.status:
                if stage.status.state == "running":
                    stats["stages_running"] += 1
                elif stage.status.state == "error":
                    stats["stages_error"] += 1
            stats["total_events"] += len(stage.input_buffer) + len(stage.output_buffer)

        return stats

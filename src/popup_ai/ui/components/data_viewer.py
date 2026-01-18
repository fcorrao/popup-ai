"""Data viewer component for displaying input/output event logs."""

from collections import deque
from collections.abc import Callable
from datetime import datetime

from nicegui import ui

from popup_ai.messages import UIEvent


class DataViewer:
    """A component for displaying a log of UIEvent items."""

    def __init__(
        self,
        title: str,
        buffer: deque | None = None,
        max_display: int = 20,
        format_fn: Callable[[UIEvent], str] | None = None,
    ) -> None:
        self.title = title
        self._buffer = buffer or deque(maxlen=100)
        self._max_display = max_display
        self._format_fn = format_fn or self._default_format
        self._container: ui.column | None = None
        self._log: ui.log | None = None

    def build(self) -> ui.card:
        """Build and return the data viewer UI element."""
        card = ui.card().classes("w-full")
        with card:
            with ui.row().classes("items-center w-full"):
                ui.label(self.title).classes("font-medium")
                ui.space()
                self._count_label = ui.label("0 items").classes("text-caption text-grey")
                ui.button(icon="refresh", on_click=self.refresh).props(
                    "flat dense size=sm"
                )
                ui.button(icon="delete", on_click=self.clear).props(
                    "flat dense size=sm color=negative"
                )

            self._log = ui.log(max_lines=self._max_display).classes("w-full h-48")

        return card

    def set_buffer(self, buffer: deque) -> None:
        """Set the buffer to display."""
        self._buffer = buffer
        self.refresh()

    def add_event(self, event: UIEvent) -> None:
        """Add an event to the display (auto-updates)."""
        if self._log:
            formatted = self._format_fn(event)
            self._log.push(formatted)
            self._update_count()

    def refresh(self) -> None:
        """Refresh the display from the buffer."""
        if not self._log:
            return

        self._log.clear()
        # Get most recent items
        items = list(self._buffer)[-self._max_display:]
        for event in items:
            formatted = self._format_fn(event)
            self._log.push(formatted)
        self._update_count()

    def clear(self) -> None:
        """Clear the display and buffer."""
        if self._log:
            self._log.clear()
        self._buffer.clear()
        self._update_count()

    def _update_count(self) -> None:
        """Update the item count label."""
        if hasattr(self, "_count_label") and self._count_label:
            count = len(self._buffer)
            self._count_label.set_text(f"{count} items")

    @staticmethod
    def _default_format(event: UIEvent) -> str:
        """Default formatting for UIEvent."""
        timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
        data_str = ""
        if event.data:
            # Show key info from data
            if "text" in event.data:
                data_str = f": {event.data['text'][:80]}..."
            elif "term" in event.data:
                data_str = f": {event.data['term']}"
            elif "bytes" in event.data:
                data_str = f": {event.data['bytes']} bytes"
            elif "slot" in event.data:
                data_str = f": slot {event.data['slot']}"
        return f"[{timestamp}] {event.event_type}{data_str}"


def format_audio_event(event: UIEvent) -> str:
    """Format audio ingest events."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "chunk_produced":
        bytes_val = event.data.get("bytes", 0)
        chunk_num = event.data.get("chunk_num", "?")
        return f"[{timestamp}] Chunk #{chunk_num}: {bytes_val} bytes"
    return f"[{timestamp}] {event.event_type}"


def format_transcript_event(event: UIEvent) -> str:
    """Format transcriber events."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "audio_received":
        duration = event.data.get("duration_s", 0)
        return f"[{timestamp}] Audio: {duration:.2f}s"
    elif event.event_type == "transcript":
        text = event.data.get("text", "")[:80]
        return f"[{timestamp}] {text}..."
    return f"[{timestamp}] {event.event_type}"


def format_annotation_event(event: UIEvent) -> str:
    """Format annotator events."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "transcript_received":
        text = event.data.get("text", "")[:40]
        return f"[{timestamp}] Input: {text}..."
    elif event.event_type == "annotation":
        term = event.data.get("term", "")
        cache_hit = event.data.get("cache_hit", False)
        cache_str = " (cached)" if cache_hit else ""
        return f"[{timestamp}] {term}{cache_str}"
    return f"[{timestamp}] {event.event_type}"


def format_overlay_event(event: UIEvent) -> str:
    """Format overlay events."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "annotation_received":
        term = event.data.get("term", "")
        return f"[{timestamp}] Input: {term}"
    elif event.event_type == "display":
        slot = event.data.get("slot", "?")
        term = event.data.get("term", "")
        return f"[{timestamp}] Slot {slot}: {term}"
    elif event.event_type == "clear":
        slot = event.data.get("slot", "?")
        return f"[{timestamp}] Cleared slot {slot}"
    return f"[{timestamp}] {event.event_type}"

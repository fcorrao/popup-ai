"""Status card component for displaying actor status."""

import logging

from nicegui import ui

from popup_ai.messages import ActorStatus

logger = logging.getLogger(__name__)


class StatusCard:
    """A card component displaying an actor's status and stats."""

    STATE_ICONS = {
        "running": ("check_circle", "positive"),
        "error": ("error", "negative"),
        "starting": ("pending", "warning"),
        "stopped": ("circle", "grey-5"),
        "unreachable": ("cloud_off", "warning"),
    }

    def __init__(self, name: str, status: ActorStatus | None = None) -> None:
        self.name = name
        self._status = status
        # UI element references for direct updates
        self._icon: ui.icon | None = None
        self._state_label: ui.label | None = None
        self._stats_row: ui.row | None = None

    def build(self) -> ui.card:
        """Build and return the status card UI element."""
        card = ui.card().classes("w-full")
        with card:
            with ui.row().classes("items-center w-full"):
                icon_name, color = self._get_icon()
                self._icon = ui.icon(icon_name).classes(f"text-{color}")
                ui.label(self._format_name()).classes("font-medium")
                ui.space()
                self._state_label = ui.label(self._get_state_text()).classes(
                    "text-caption text-grey"
                )

            self._stats_row = ui.row().classes("text-caption flex-wrap gap-2")
            self._render_stats()

        return card

    def _render_stats(self) -> None:
        """Render stats inside the stats row."""
        if not self._stats_row:
            return
        self._stats_row.clear()
        with self._stats_row:
            if self._status and self._status.stats:
                for key, value in list(self._status.stats.items())[:4]:
                    formatted_key = key.replace("_", " ").title()
                    if isinstance(value, float):
                        value = f"{value:.2f}"
                    ui.label(f"{formatted_key}: {value}").classes(
                        "px-2 py-1 bg-grey-2 rounded"
                    )
            elif self._status and self._status.error:
                ui.label(f"Error: {self._status.error[:50]}...").classes(
                    "text-negative"
                )

    def update(self, status: ActorStatus | None) -> None:
        """Update the card with new status."""
        old_color = self._get_icon()[1] if self._status else "grey-5"
        self._status = status
        icon_name, new_color = self._get_icon()

        # Update icon
        if self._icon:
            self._icon.props(f'name="{icon_name}"')
            # Update color class
            self._icon.classes(remove=f"text-{old_color}", add=f"text-{new_color}")

        # Update state label
        if self._state_label:
            self._state_label.set_text(self._get_state_text())

        # Update stats
        self._render_stats()

    # Special display names for actors
    DISPLAY_NAMES = {
        "ocr": "OCR",
        "media_ingest": "Media Ingest",
    }

    def _format_name(self) -> str:
        """Format the actor name for display."""
        if self.name in self.DISPLAY_NAMES:
            return self.DISPLAY_NAMES[self.name]
        return self.name.replace("_", " ").title()

    def _get_icon(self) -> tuple[str, str]:
        """Get the icon name and color for current state."""
        if not self._status:
            return ("circle", "grey-5")
        return self.STATE_ICONS.get(self._status.state, ("circle", "grey-5"))

    def _get_state_text(self) -> str:
        """Get the state display text."""
        if not self._status:
            return "Not started"
        return self._status.state.capitalize()

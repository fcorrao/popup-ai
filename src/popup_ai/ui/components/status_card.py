"""Status card component for displaying actor status."""

from nicegui import ui

from popup_ai.messages import ActorStatus


class StatusCard:
    """A card component displaying an actor's status and stats."""

    STATE_ICONS = {
        "running": ("check_circle", "green"),
        "error": ("error", "red"),
        "starting": ("pending", "orange"),
        "stopped": ("circle", "grey"),
    }

    def __init__(self, name: str, status: ActorStatus | None = None) -> None:
        self.name = name
        self._status = status
        self._card: ui.card | None = None
        self._icon: ui.icon | None = None
        self._state_label: ui.label | None = None
        self._stats_row: ui.row | None = None

    def build(self) -> ui.card:
        """Build and return the status card UI element."""
        self._card = ui.card().classes("w-full")
        with self._card:
            with ui.row().classes("items-center w-full"):
                icon_name, color = self._get_icon()
                self._icon = ui.icon(icon_name, color=color)
                ui.label(self._format_name()).classes("font-medium")
                ui.space()
                self._state_label = ui.label(self._get_state_text()).classes(
                    "text-caption text-grey"
                )

            self._stats_row = ui.row().classes("text-caption flex-wrap gap-2")
            self._update_stats_display()

        return self._card

    def update(self, status: ActorStatus | None) -> None:
        """Update the card with new status."""
        self._status = status
        if self._icon:
            icon_name, color = self._get_icon()
            self._icon._props["name"] = icon_name
            self._icon._props["color"] = color
            self._icon.update()
        if self._state_label:
            self._state_label.set_text(self._get_state_text())
        self._update_stats_display()

    def _format_name(self) -> str:
        """Format the actor name for display."""
        return self.name.replace("_", " ").title()

    def _get_icon(self) -> tuple[str, str]:
        """Get the icon name and color for current state."""
        if not self._status:
            return ("circle", "grey")
        return self.STATE_ICONS.get(self._status.state, ("circle", "grey"))

    def _get_state_text(self) -> str:
        """Get the state display text."""
        if not self._status:
            return "Not started"
        return self._status.state.capitalize()

    def _update_stats_display(self) -> None:
        """Update the stats row display."""
        if not self._stats_row:
            return

        self._stats_row.clear()
        with self._stats_row:
            if self._status and self._status.stats:
                # Show first 4 stats
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

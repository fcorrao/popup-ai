"""Status card component for displaying actor status."""

from nicegui import ui

from popup_ai.messages import ActorStatus


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

    def build(self) -> ui.card:
        """Build and return the status card UI element."""
        card = ui.card().classes("w-full")
        with card:
            self._render_content()
        return card

    @ui.refreshable_method
    def _render_content(self) -> None:
        """Render the card content. Decorated with @ui.refreshable for updates."""
        with ui.row().classes("items-center w-full"):
            icon_name, color = self._get_icon()
            ui.icon(icon_name).classes(f"text-{color}")
            ui.label(self._format_name()).classes("font-medium")
            ui.space()
            ui.label(self._get_state_text()).classes("text-caption text-grey")

        with ui.row().classes("text-caption flex-wrap gap-2"):
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

    def update(self, status: ActorStatus | None) -> None:
        """Update the card with new status."""
        self._status = status
        self._render_content.refresh()

    def _format_name(self) -> str:
        """Format the actor name for display."""
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

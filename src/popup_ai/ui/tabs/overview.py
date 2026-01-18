"""Overview tab with status cards grid."""

from nicegui import ui

from popup_ai.messages import ActorStatus
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class OverviewTab:
    """Overview tab displaying all actor status cards and pipeline health."""

    def __init__(self, state: UIState) -> None:
        self._state = state
        self._cards: dict[str, StatusCard] = {}
        self._stats_labels: dict[str, ui.label] = {}

    def build(self) -> ui.column:
        """Build and return the overview tab content."""
        container = ui.column().classes("w-full gap-4")
        with container:
            # Pipeline health summary
            with ui.card().classes("w-full"):
                ui.label("Pipeline Health").classes("text-h6")
                with ui.row().classes("gap-4 mt-2"):
                    self._stats_labels["running"] = ui.label("Stages Running: 0").classes(
                        "px-3 py-1 bg-green-1 rounded"
                    )
                    self._stats_labels["error"] = ui.label("Errors: 0").classes(
                        "px-3 py-1 bg-red-1 rounded"
                    )
                    self._stats_labels["events"] = ui.label("Total Events: 0").classes(
                        "px-3 py-1 bg-blue-1 rounded"
                    )

            # Actor status cards in a 2x2 grid
            ui.label("Actor Status").classes("text-h6")
            with ui.grid(columns=2).classes("w-full gap-4"):
                for stage_name in UIState.STAGES:
                    stage = self._state.get_stage(stage_name)
                    card = StatusCard(stage_name, stage.status if stage else None)
                    self._cards[stage_name] = card
                    card.build()

        return container

    def update(self, statuses: dict[str, ActorStatus]) -> None:
        """Update all status cards."""
        for name, status in statuses.items():
            if name in self._cards:
                self._cards[name].update(status)

        # Update summary stats
        stats = self._state.get_overview_stats()
        if "running" in self._stats_labels:
            self._stats_labels["running"].set_text(
                f"Stages Running: {stats['stages_running']}"
            )
        if "error" in self._stats_labels:
            self._stats_labels["error"].set_text(f"Errors: {stats['stages_error']}")
        if "events" in self._stats_labels:
            self._stats_labels["events"].set_text(
                f"Total Events: {stats['total_events']}"
            )

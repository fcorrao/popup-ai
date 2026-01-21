"""Diagnostics tab showing system stats."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from nicegui import ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


class DiagnosticsTab:
    """Diagnostics tab displaying system resource usage."""

    def __init__(
        self,
        state: UIState,
        settings: Settings,
        supervisor_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._state = state
        self._settings = settings
        self._supervisor_getter = supervisor_getter
        self._status_card: StatusCard | None = None
        self._log: ui.log | None = None
        self._cpu_label: ui.label | None = None
        self._mem_label: ui.label | None = None
        self._disk_label: ui.label | None = None
        self._net_label: ui.label | None = None

    def build(self) -> ui.column:
        """Build and return the diagnostics tab content."""
        stage = self._state.get_stage("diagnostics")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard(
                "diagnostics", stage.status if stage else None
            )
            self._status_card.build()

            # Current stats cards
            with ui.row().classes("w-full gap-4"):
                with ui.card().classes("flex-1"):
                    ui.label("CPU").classes("text-caption text-grey")
                    self._cpu_label = ui.label("--").classes("text-2xl font-bold")

                with ui.card().classes("flex-1"):
                    ui.label("Memory").classes("text-caption text-grey")
                    self._mem_label = ui.label("--").classes("text-2xl font-bold")

                with ui.card().classes("flex-1"):
                    ui.label("Disk I/O").classes("text-caption text-grey")
                    self._disk_label = ui.label("--").classes("text-lg")

                with ui.card().classes("flex-1"):
                    ui.label("Network").classes("text-caption text-grey")
                    self._net_label = ui.label("--").classes("text-lg")

            # Log view
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center w-full"):
                    ui.label("System Stats Log").classes("font-medium")
                    ui.space()
                    ui.button(
                        "Export",
                        on_click=self._export_log,
                        icon="download",
                    ).props("flat dense")
                    ui.button(
                        "Clear",
                        on_click=self._clear_log,
                        icon="delete",
                    ).props("flat dense")

                self._log = ui.log(max_lines=100).classes("w-full h-96 font-mono text-xs")

        return container

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "sample":
            self._update_stats(event.data)
            self._append_log(event.data)

    def _update_stats(self, data: dict) -> None:
        """Update the stat cards."""
        if self._cpu_label:
            cpu = data.get("cpu_avg", 0)
            self._cpu_label.set_text(f"{cpu:.1f}%")

        if self._mem_label:
            mem = data.get("mem_percent", 0)
            used = data.get("mem_used_gb", 0)
            self._mem_label.set_text(f"{mem:.1f}% ({used:.1f}GB)")

        if self._disk_label:
            read = data.get("disk_read_mb_s", 0)
            write = data.get("disk_write_mb_s", 0)
            self._disk_label.set_text(f"R:{read:.1f} W:{write:.1f} MB/s")

        if self._net_label:
            recv = data.get("net_recv_mb_s", 0)
            sent = data.get("net_sent_mb_s", 0)
            drops = data.get("net_drop_in", 0) + data.get("net_drop_out", 0)
            drop_str = f" ⚠{drops}" if drops > 0 else ""
            self._net_label.set_text(f"↓{recv:.2f} ↑{sent:.2f} MB/s{drop_str}")

    def _append_log(self, data: dict) -> None:
        """Append a sample to the log."""
        if not self._log:
            return

        ts = datetime.fromtimestamp(data.get("timestamp", 0)).strftime("%H:%M:%S")
        cpu = data.get("cpu_avg", 0)
        mem = data.get("mem_percent", 0)
        disk_r = data.get("disk_read_mb_s", 0)
        disk_w = data.get("disk_write_mb_s", 0)
        net_r = data.get("net_recv_mb_s", 0)
        net_s = data.get("net_sent_mb_s", 0)
        drops = data.get("net_drop_in", 0) + data.get("net_drop_out", 0)

        line = f"[{ts}] CPU:{cpu:5.1f}% MEM:{mem:5.1f}% DISK:R{disk_r:6.2f}/W{disk_w:6.2f}MB/s NET:↓{net_r:6.2f}/↑{net_s:6.2f}MB/s"
        if drops > 0:
            line += f" DROPS:{drops}"

        self._log.push(line)

    def _clear_log(self) -> None:
        """Clear the log."""
        if self._log:
            self._log.clear()

    async def _export_log(self) -> None:
        """Export diagnostics history as JSON."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            import json
            import ray

            history = ray.get(supervisor.get_diagnostics_history.remote(limit=300))
            if not history:
                ui.notify("No diagnostics data to export", type="warning")
                return

            # Create downloadable JSON
            json_str = json.dumps(history, indent=2)
            ui.download(
                json_str.encode(),
                f"diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
            ui.notify(f"Exported {len(history)} samples", type="positive")

        except Exception as ex:
            ui.notify(f"Failed to export: {ex}", type="negative")

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("diagnostics")
        if stage and self._status_card:
            self._status_card.update(stage.status)

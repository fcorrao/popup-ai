"""Diagnostics actor for system monitoring using psutil."""

import asyncio
import logging
import time
from collections import deque
from typing import Any

import ray
from ray.util.queue import Queue

from popup_ai.messages import ActorStatus, UIEvent

logger = logging.getLogger(__name__)


@ray.remote
class DiagnosticsActor:
    """Actor that samples system stats using psutil.

    Captures CPU, memory, disk I/O, and network I/O every second.
    Publishes to UI queue for display.
    """

    def __init__(
        self,
        ui_queue: Queue | None = None,
        sample_interval: float = 1.0,
        history_size: int = 300,  # 5 minutes at 1 sample/sec
    ) -> None:
        self.ui_queue = ui_queue
        self.sample_interval = sample_interval
        self.history_size = history_size
        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._running = False
        self._sample_task: asyncio.Task | None = None
        self._history: deque[dict] = deque(maxlen=history_size)
        self._samples_collected = 0
        self._logger = logging.getLogger("popup_ai.actors.diagnostics")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "samples_collected": self._samples_collected,
            "history_size": len(self._history),
        }
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="diagnostics",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the diagnostics actor."""
        if self._state == "running":
            return

        self._logger.info("Starting diagnostics actor")
        self._state = "starting"
        self._error = None

        try:
            # Test psutil import
            import psutil
            _ = psutil.cpu_percent()

            self._state = "running"
            self._running = True
            self._start_time = time.time()
            self._sample_task = asyncio.create_task(self._sample_loop())
            self._publish_ui_event("started", {})
            self._logger.info("Diagnostics actor started")
        except Exception as e:
            self._state = "error"
            self._error = str(e)
            self._logger.exception("Failed to start diagnostics actor")
            raise

    async def stop(self) -> None:
        """Stop the diagnostics actor."""
        if self._state == "stopped":
            return

        self._logger.info("Stopping diagnostics actor")
        self._running = False

        if self._sample_task:
            self._sample_task.cancel()
            try:
                await self._sample_task
            except asyncio.CancelledError:
                pass

        self._state = "stopped"
        self._start_time = None
        self._publish_ui_event("stopped", {})
        self._logger.info("Diagnostics actor stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    async def _sample_loop(self) -> None:
        """Main sampling loop."""
        import psutil

        # Initialize previous I/O counters for delta calculation
        prev_disk_io = psutil.disk_io_counters()
        prev_net_io = psutil.net_io_counters()
        prev_time = time.time()

        while self._running:
            try:
                now = time.time()
                elapsed = now - prev_time

                # CPU
                cpu_percent = psutil.cpu_percent(percpu=True)
                cpu_avg = sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0

                # Memory
                mem = psutil.virtual_memory()

                # Disk I/O (delta since last sample)
                disk_io = psutil.disk_io_counters()
                disk_read_mb = 0
                disk_write_mb = 0
                if disk_io and prev_disk_io and elapsed > 0:
                    disk_read_mb = (disk_io.read_bytes - prev_disk_io.read_bytes) / (1024 * 1024) / elapsed
                    disk_write_mb = (disk_io.write_bytes - prev_disk_io.write_bytes) / (1024 * 1024) / elapsed

                # Network I/O (delta since last sample)
                net_io = psutil.net_io_counters()
                net_recv_mb = 0
                net_sent_mb = 0
                net_drop_in = 0
                net_drop_out = 0
                if net_io and prev_net_io and elapsed > 0:
                    net_recv_mb = (net_io.bytes_recv - prev_net_io.bytes_recv) / (1024 * 1024) / elapsed
                    net_sent_mb = (net_io.bytes_sent - prev_net_io.bytes_sent) / (1024 * 1024) / elapsed
                    net_drop_in = net_io.dropin - prev_net_io.dropin
                    net_drop_out = net_io.dropout - prev_net_io.dropout

                sample = {
                    "timestamp": now,
                    "cpu_avg": round(cpu_avg, 1),
                    "cpu_percpu": cpu_percent,
                    "mem_percent": round(mem.percent, 1),
                    "mem_used_gb": round(mem.used / (1024**3), 2),
                    "mem_available_gb": round(mem.available / (1024**3), 2),
                    "disk_read_mb_s": round(disk_read_mb, 2),
                    "disk_write_mb_s": round(disk_write_mb, 2),
                    "net_recv_mb_s": round(net_recv_mb, 2),
                    "net_sent_mb_s": round(net_sent_mb, 2),
                    "net_drop_in": net_drop_in,
                    "net_drop_out": net_drop_out,
                }

                self._history.append(sample)
                self._samples_collected += 1

                # Publish to UI
                self._publish_ui_event("sample", sample)

                # Update previous counters
                prev_disk_io = disk_io
                prev_net_io = net_io
                prev_time = now

                await asyncio.sleep(self.sample_interval)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in diagnostics sample loop")
                await asyncio.sleep(self.sample_interval)

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="diagnostics",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

    def get_history(self, limit: int = 60) -> list[dict]:
        """Get recent history samples."""
        return list(self._history)[-limit:]

    def get_latest(self) -> dict | None:
        """Get the most recent sample."""
        if self._history:
            return self._history[-1]
        return None

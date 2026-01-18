"""Audio ingest actor for SRT stream capture."""

import asyncio
import contextlib
import logging
import shutil
import subprocess
import time

import ray
from ray.util.queue import Queue

from popup_ai.config import AudioIngestConfig
from popup_ai.messages import ActorStatus, AudioChunk, UIEvent

logger = logging.getLogger(__name__)


class FFmpegNotFoundError(Exception):
    """Raised when ffmpeg is not installed or not in PATH."""

    pass


def check_ffmpeg_available() -> tuple[bool, str]:
    """Check if ffmpeg is available and return version info.

    Returns:
        Tuple of (is_available, version_or_error_message)
    """
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False, (
            "ffmpeg not found in PATH. Install it with:\n"
            "  macOS: brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg\n"
            "  Windows: winget install ffmpeg"
        )

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Extract first line (version info)
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown version"
        return True, version_line
    except subprocess.TimeoutExpired:
        return False, "ffmpeg found but timed out checking version"
    except Exception as e:
        return False, f"ffmpeg found but error checking version: {e}"


@ray.remote
class AudioIngestActor:
    """Actor that captures audio from SRT stream via ffmpeg.

    Spawns an ffmpeg subprocess that:
    - Listens on SRT port for incoming stream
    - Converts to PCM format
    - Outputs chunks to audio_queue
    """

    def __init__(
        self,
        config: AudioIngestConfig,
        output_queue: Queue,
        ui_queue: Queue | None = None,
    ) -> None:
        self.config = config
        self.output_queue = output_queue
        self.ui_queue = ui_queue
        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._running = False
        self._process: subprocess.Popen | None = None
        self._reader_task: asyncio.Task | None = None
        self._chunks_sent = 0
        self._bytes_processed = 0
        self._ffmpeg_available = False
        self._ffmpeg_version: str | None = None
        self._logger = logging.getLogger("popup_ai.actors.audio_ingest")

        # Check ffmpeg availability on init
        self._ffmpeg_available, self._ffmpeg_version = check_ffmpeg_available()
        if self._ffmpeg_available:
            self._logger.info(f"ffmpeg detected: {self._ffmpeg_version}")
        else:
            self._logger.warning(f"ffmpeg not available: {self._ffmpeg_version}")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "chunks_sent": self._chunks_sent,
            "bytes_processed": self._bytes_processed,
            "ffmpeg_available": self._ffmpeg_available,
        }
        if self._ffmpeg_version and self._ffmpeg_available:
            stats["ffmpeg_version"] = self._ffmpeg_version
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="audio_ingest",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the audio ingest process."""
        if self._state == "running":
            return

        self._logger.info("Starting audio ingest actor")
        self._state = "starting"
        self._error = None

        # Check ffmpeg availability first
        if not self._ffmpeg_available:
            self._state = "error"
            self._error = self._ffmpeg_version  # Contains the error message
            self._logger.error(f"Cannot start audio ingest: {self._error}")
            self._publish_ui_event("error", {
                "message": self._error,
                "type": "ffmpeg_not_found",
            })
            raise FFmpegNotFoundError(self._error)

        try:
            await self._start_ffmpeg()
            self._state = "running"
            self._running = True
            self._start_time = time.time()
            self._publish_ui_event("started", {"ffmpeg_version": self._ffmpeg_version})
            self._logger.info("Audio ingest actor started")
        except Exception as e:
            self._state = "error"
            self._error = str(e)
            self._logger.exception("Failed to start audio ingest")
            self._publish_ui_event("error", {"message": str(e)})
            raise

    async def stop(self) -> None:
        """Stop the audio ingest process."""
        if self._state == "stopped":
            return

        self._logger.info("Stopping audio ingest actor")
        self._running = False

        # Cancel reader task
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        # Terminate ffmpeg
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        self._state = "stopped"
        self._start_time = None
        self._publish_ui_event("stopped", {})
        self._logger.info("Audio ingest actor stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        if self._state != "running":
            return False
        if self._process and self._process.poll() is not None:
            return False
        return self._running

    async def _start_ffmpeg(self) -> None:
        """Start ffmpeg subprocess for SRT capture."""
        latency_us = self.config.srt_latency_ms * 1000
        srt_url = f"srt://0.0.0.0:{self.config.srt_port}?mode=listener&latency={latency_us}"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-i", srt_url,
            "-vn",  # No video
            "-acodec", "pcm_s16le",
            "-ar", str(self.config.sample_rate),
            "-ac", str(self.config.channels),
            "-f", "s16le",
            "pipe:1",
        ]

        self._logger.info(f"Starting ffmpeg: {' '.join(cmd)}")

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # Start reading output
        self._reader_task = asyncio.create_task(self._read_audio())

    async def _read_audio(self) -> None:
        """Read audio data from ffmpeg and push to queue."""
        chunk_bytes = int(
            self.config.sample_rate
            * self.config.channels
            * 2  # 16-bit samples = 2 bytes
            * self.config.chunk_duration_ms
            / 1000
        )

        while self._running and self._process:
            try:
                # Read in a thread to avoid blocking
                loop = asyncio.get_event_loop()

                def read_chunk() -> bytes:
                    if self._process and self._process.stdout:
                        return self._process.stdout.read(chunk_bytes)
                    return b""

                data = await loop.run_in_executor(None, read_chunk)

                if not data:
                    if self._process and self._process.poll() is not None:
                        # Process ended
                        self._logger.warning("ffmpeg process ended")
                        self._state = "error"
                        self._error = "ffmpeg process ended unexpectedly"
                        break
                    continue

                # Create audio chunk
                chunk = AudioChunk(
                    data=data,
                    timestamp_ms=int(time.time() * 1000),
                    sample_rate=self.config.sample_rate,
                    channels=self.config.channels,
                )

                # Push to queue
                try:
                    self.output_queue.put_nowait(chunk)
                    self._chunks_sent += 1
                    self._bytes_processed += len(data)
                except Exception:
                    self._logger.debug("Output queue full, dropping chunk")

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error reading audio")
                await asyncio.sleep(0.1)

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="audio_ingest",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

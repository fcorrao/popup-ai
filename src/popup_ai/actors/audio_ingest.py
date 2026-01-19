"""Audio ingest actor for SRT stream capture."""

import asyncio
import contextlib
import logging
import shutil
import socket
import subprocess
import time

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import AudioIngestConfig
from popup_ai.messages import ActorStatus, AudioChunk, UIEvent
from popup_ai.observability import ensure_logfire_configured

logger = logging.getLogger(__name__)


class FFmpegNotFoundError(Exception):
    """Raised when ffmpeg is not installed or not in PATH."""

    pass


class PortInUseError(Exception):
    """Raised when the SRT port is already in use."""

    pass


def is_port_available(port: int, udp: bool = True) -> bool:
    """Check if a port is available for binding.

    Args:
        port: Port number to check
        udp: If True, check UDP (for SRT). If False, check TCP.

    Returns:
        True if port is available, False if in use
    """
    sock_type = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    try:
        with socket.socket(socket.AF_INET, sock_type) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


def kill_orphan_ffmpeg_on_port(port: int) -> bool:
    """Kill any orphan ffmpeg processes listening on the specified port.

    This handles cleanup when a previous popup-ai instance crashed without
    properly terminating ffmpeg.

    Args:
        port: Port number to check

    Returns:
        True if a process was killed, False otherwise
    """
    import platform

    try:
        if platform.system() == "Darwin":
            # macOS: use lsof to find process
            result = subprocess.run(
                ["lsof", "-t", "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    try:
                        pid_int = int(pid.strip())
                        # Verify it's ffmpeg before killing
                        ps_result = subprocess.run(
                            ["ps", "-p", str(pid_int), "-o", "comm="],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if "ffmpeg" in ps_result.stdout.lower():
                            logger.warning(
                                f"Killing orphan ffmpeg process {pid_int} on port {port}"
                            )
                            subprocess.run(["kill", "-9", str(pid_int)], timeout=5)
                            return True
                    except (ValueError, subprocess.TimeoutExpired):
                        continue
        else:
            # Linux: use ss or netstat
            result = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "ffmpeg" in result.stdout:
                # Extract PID from ss output
                import re
                match = re.search(r"pid=(\d+)", result.stdout)
                if match:
                    pid = int(match.group(1))
                    logger.warning(f"Killing orphan ffmpeg process {pid} on port {port}")
                    subprocess.run(["kill", "-9", str(pid)], timeout=5)
                    return True
    except Exception as e:
        logger.debug(f"Could not check for orphan ffmpeg: {e}")

    return False


def check_ffmpeg_available() -> tuple[bool, str]:
    """Check if ffmpeg is available with SRT protocol support.

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
        # Check version
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown version"

        # Check for SRT protocol support
        protocols_result = subprocess.run(
            ["ffmpeg", "-protocols"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "srt" not in protocols_result.stdout.lower():
            return False, (
                f"ffmpeg found ({version_line}) but SRT protocol not supported.\n"
                "Reinstall ffmpeg with SRT support:\n"
                "  macOS: brew reinstall ffmpeg\n"
                "  Ubuntu: sudo apt install ffmpeg libsrt-dev\n"
                "  Or build from source with --enable-libsrt"
            )

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
        self._stderr_task: asyncio.Task | None = None
        self._ffmpeg_stderr: list[str] = []
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

        ensure_logfire_configured()
        with logfire.span("audio_ingest.start"):
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

            # Check port availability, try to clean up orphan ffmpeg if needed
            if not is_port_available(self.config.srt_port):
                self._logger.warning(
                    f"Port {self.config.srt_port} is in use, checking for orphan ffmpeg..."
                )
                if kill_orphan_ffmpeg_on_port(self.config.srt_port):
                    # Give the OS a moment to release the port
                    await asyncio.sleep(0.5)

                # Check again after cleanup attempt
                if not is_port_available(self.config.srt_port):
                    self._state = "error"
                    self._error = (
                        f"Port {self.config.srt_port} is already in use. "
                        "Could not clean up orphan process. Check with: "
                        f"lsof -i :{self.config.srt_port}"
                    )
                    self._logger.error(self._error)
                    self._publish_ui_event("error", {
                        "message": self._error,
                        "type": "port_in_use",
                        "port": self.config.srt_port,
                    })
                    raise PortInUseError(self._error)

            try:
                await self._start_ffmpeg()
                self._state = "running"
                self._running = True
                self._start_time = time.time()
                self._publish_ui_event("started", {"ffmpeg_version": self._ffmpeg_version})
                logfire.info(
                    "audio_ingest started",
                    srt_port=self.config.srt_port,
                    sample_rate=self.config.sample_rate,
                )
            except Exception as e:
                self._state = "error"
                self._error = str(e)
                logfire.exception("Failed to start audio ingest")
                self._publish_ui_event("error", {"message": str(e)})
                raise

    async def stop(self) -> None:
        """Stop the audio ingest process."""
        if self._state == "stopped":
            return

        with logfire.span("audio_ingest.stop"):
            self._logger.info("Stopping audio ingest actor")
            self._running = False

            # Cancel reader tasks
            if self._reader_task:
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task

            if self._stderr_task:
                self._stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._stderr_task

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
            logfire.info("audio_ingest stopped")

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
            "-loglevel", "info",  # Use info level for more diagnostic output
            "-threads", str(self.config.ffmpeg_threads),
            "-i", srt_url,
            "-vn",  # No video
            "-acodec", "pcm_s16le",
            "-ar", str(self.config.sample_rate),
            "-ac", str(self.config.channels),
            "-f", "s16le",
            "pipe:1",
        ]

        self._logger.info(f"Starting ffmpeg: {' '.join(cmd)}")

        # Clear previous stderr
        self._ffmpeg_stderr = []

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # Start reading output and stderr
        self._reader_task = asyncio.create_task(self._read_audio())
        self._stderr_task = asyncio.create_task(self._read_stderr())

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
                        # Process ended - get exit code and stderr
                        exit_code = self._process.returncode
                        stderr_output = "\n".join(self._ffmpeg_stderr[-20:])  # Last 20 lines

                        self._logger.error(
                            f"ffmpeg process ended with exit code {exit_code}\n"
                            f"stderr output:\n{stderr_output}"
                        )
                        self._state = "error"
                        self._error = (
                            f"ffmpeg exited with code {exit_code}. "
                            f"Last output: {stderr_output[:500]}"
                        )
                        self._publish_ui_event("error", {
                            "message": self._error,
                            "exit_code": exit_code,
                            "stderr": stderr_output,
                        })
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
                    # Publish chunk_produced event for UI
                    self._publish_ui_event("chunk_produced", {
                        "bytes": len(data),
                        "chunk_num": self._chunks_sent,
                        "total_bytes": self._bytes_processed,
                    })
                except Exception:
                    self._logger.debug("Output queue full, dropping chunk")

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error reading audio")
                await asyncio.sleep(0.1)

    async def _read_stderr(self) -> None:
        """Read stderr from ffmpeg and log it."""
        loop = asyncio.get_event_loop()

        while self._running and self._process:
            try:
                def read_line() -> str:
                    if self._process and self._process.stderr:
                        line = self._process.stderr.readline()
                        if line:
                            return line.decode("utf-8", errors="replace").rstrip()
                    return ""

                line = await loop.run_in_executor(None, read_line)

                if line:
                    self._ffmpeg_stderr.append(line)
                    # Keep only last 100 lines to avoid memory bloat
                    if len(self._ffmpeg_stderr) > 100:
                        self._ffmpeg_stderr = self._ffmpeg_stderr[-100:]
                    # Log ffmpeg output at debug level (info for errors/warnings)
                    if "error" in line.lower() or "warning" in line.lower():
                        self._logger.warning(f"[ffmpeg] {line}")
                    else:
                        self._logger.debug(f"[ffmpeg] {line}")
                elif self._process and self._process.poll() is not None:
                    # Process ended
                    break
                else:
                    # No data, brief sleep
                    await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error reading ffmpeg stderr")
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

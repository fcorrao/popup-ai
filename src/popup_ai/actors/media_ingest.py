"""Media ingest actor for SRT stream capture (audio and video)."""

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import subprocess
import time

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import AudioIngestConfig, VideoIngestConfig
from popup_ai.messages import ActorStatus, AudioChunk, UIEvent, VideoFrame
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
class MediaIngestActor:
    """Actor that captures audio and video from SRT stream via ffmpeg.

    Spawns an ffmpeg subprocess that:
    - Listens on SRT port for incoming stream
    - Converts audio to PCM format and outputs to audio_queue
    - Optionally extracts video frames and outputs to video_queue
    """

    def __init__(
        self,
        audio_config: AudioIngestConfig,
        audio_queue: Queue,
        video_config: VideoIngestConfig | None = None,
        video_queue: Queue | None = None,
        ui_queue: Queue | None = None,
        video_resolution: tuple[int, int] | None = None,
    ) -> None:
        self.audio_config = audio_config
        self.audio_queue = audio_queue
        self.video_config = video_config
        self.video_queue = video_queue
        self.ui_queue = ui_queue
        self.video_resolution = video_resolution  # (width, height) from OBS
        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._running = False
        self._process: subprocess.Popen | None = None
        self._audio_reader_task: asyncio.Task | None = None
        self._video_reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._ffmpeg_stderr: list[str] = []
        self._audio_chunks_sent = 0
        self._audio_bytes_processed = 0
        self._video_frames_sent = 0
        self._video_bytes_processed = 0
        self._ffmpeg_available = False
        self._ffmpeg_version: str | None = None
        self._logger = logging.getLogger("popup_ai.actors.media_ingest")
        self._last_ui_event_time = 0.0
        self._ui_event_interval = 1.0  # Only emit UI events every 1 second

        # Video output pipe fd
        self._video_read_fd: int | None = None
        self._video_write_fd: int | None = None

        # Check ffmpeg availability on init
        self._ffmpeg_available, self._ffmpeg_version = check_ffmpeg_available()
        if self._ffmpeg_available:
            self._logger.info(f"ffmpeg detected: {self._ffmpeg_version}")
        else:
            self._logger.warning(f"ffmpeg not available: {self._ffmpeg_version}")

        # Configure logfire in __init__ to avoid blocking async event loop
        ensure_logfire_configured()

    @property
    def video_enabled(self) -> bool:
        """Check if video extraction is enabled."""
        return (
            self.video_config is not None
            and self.video_config.enabled
            and self.video_queue is not None
        )

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "audio_chunks_sent": self._audio_chunks_sent,
            "audio_bytes_processed": self._audio_bytes_processed,
            "ffmpeg_available": self._ffmpeg_available,
            "video_enabled": self.video_enabled,
            "video_queue_available": self.video_queue is not None,
            "video_config_available": self.video_config is not None,
            "video_config_enabled": self.video_config.enabled if self.video_config else False,
        }
        if self.video_enabled:
            stats["video_frames_sent"] = self._video_frames_sent
            stats["video_bytes_processed"] = self._video_bytes_processed
        if self._ffmpeg_version and self._ffmpeg_available:
            stats["ffmpeg_version"] = self._ffmpeg_version
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="media_ingest",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the media ingest process."""
        if self._state == "running":
            return

        with logfire.span("media_ingest.start"):
            self._logger.info("Starting media ingest actor")
            self._state = "starting"
            self._error = None

            # Check ffmpeg availability first
            if not self._ffmpeg_available:
                self._state = "error"
                self._error = self._ffmpeg_version  # Contains the error message
                self._logger.error(f"Cannot start media ingest: {self._error}")
                self._publish_ui_event("error", {
                    "message": self._error,
                    "type": "ffmpeg_not_found",
                })
                raise FFmpegNotFoundError(self._error)

            # Check port availability, try to clean up orphan ffmpeg if needed
            if not is_port_available(self.audio_config.srt_port):
                self._logger.warning(
                    f"Port {self.audio_config.srt_port} is in use, checking for orphan ffmpeg..."
                )
                if kill_orphan_ffmpeg_on_port(self.audio_config.srt_port):
                    # Give the OS a moment to release the port
                    await asyncio.sleep(0.5)

                # Check again after cleanup attempt
                if not is_port_available(self.audio_config.srt_port):
                    self._state = "error"
                    self._error = (
                        f"Port {self.audio_config.srt_port} is already in use. "
                        "Could not clean up orphan process. Check with: "
                        f"lsof -i :{self.audio_config.srt_port}"
                    )
                    self._logger.error(self._error)
                    self._publish_ui_event("error", {
                        "message": self._error,
                        "type": "port_in_use",
                        "port": self.audio_config.srt_port,
                    })
                    raise PortInUseError(self._error)

            try:
                await self._start_ffmpeg()
                self._state = "running"
                self._running = True
                self._start_time = time.time()
                self._publish_ui_event("started", {
                    "ffmpeg_version": self._ffmpeg_version,
                    "video_enabled": self.video_enabled,
                })
                logfire.info(
                    "media_ingest started",
                    srt_port=self.audio_config.srt_port,
                    sample_rate=self.audio_config.sample_rate,
                    video_enabled=self.video_enabled,
                )
            except Exception as e:
                self._state = "error"
                self._error = str(e)
                logfire.exception("Failed to start media ingest")
                self._publish_ui_event("error", {"message": str(e)})
                raise

    async def stop(self) -> None:
        """Stop the media ingest process."""
        if self._state == "stopped":
            return

        with logfire.span("media_ingest.stop"):
            self._logger.info("Stopping media ingest actor")
            self._running = False

            # Cancel reader tasks
            if self._audio_reader_task:
                self._audio_reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._audio_reader_task

            if self._video_reader_task:
                self._video_reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._video_reader_task

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

            # Close video pipe fds
            if self._video_read_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._video_read_fd)
                self._video_read_fd = None

            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            logfire.info("media_ingest stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        if self._state != "running":
            return False
        if self._process and self._process.poll() is not None:
            return False
        return self._running

    async def _start_ffmpeg(self) -> None:
        """Start ffmpeg subprocess for SRT capture."""
        self._logger.warning(
            f"_start_ffmpeg: video_enabled={self.video_enabled}, "
            f"video_config={self.video_config is not None}, "
            f"video_queue={self.video_queue is not None}"
        )
        latency_us = self.audio_config.srt_latency_ms * 1000
        rcvbuf_bytes = self.audio_config.srt_rcvbuf_mb * 1024 * 1024
        srt_url = f"srt://0.0.0.0:{self.audio_config.srt_port}?mode=listener&latency={latency_us}&rcvbuf={rcvbuf_bytes}"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "info",
            "-threads", str(self.audio_config.ffmpeg_threads),
            "-fflags", "+discardcorrupt",
            "-i", srt_url,
            # Audio output (pipe:1)
            "-map", "0:a:0",
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(self.audio_config.sample_rate),
            "-ac", str(self.audio_config.channels),
            "-f", "s16le",
            "pipe:1",
        ]

        pass_fds = []

        # Add video output if enabled
        if self.video_enabled and self.video_config:
            # Create pipe for video output
            self._video_read_fd, self._video_write_fd = os.pipe()
            pass_fds.append(self._video_write_fd)

            # Calculate scaled height based on aspect ratio
            # Default to 720 if we don't know the resolution
            if self.video_resolution:
                src_width, src_height = self.video_resolution
                scale_width = self.video_config.scale_width
                scale_height = int(scale_width * src_height / src_width)
                # Ensure height is even for video codecs
                scale_height = scale_height + (scale_height % 2)
            else:
                scale_width = self.video_config.scale_width
                scale_height = -2  # Let ffmpeg calculate

            # Video filter chain
            fps = self.video_config.fps
            pixel_fmt = self.video_config.pixel_format
            vf = f"fps={fps},scale={scale_width}:{scale_height},format={pixel_fmt}"

            cmd.extend([
                # Video output (pipe:3 - fd passed via pass_fds)
                "-map", "0:v:0",
                "-an",
                "-vf", vf,
                "-f", "rawvideo",
                "-pix_fmt", self.video_config.pixel_format,
                f"pipe:{self._video_write_fd}",
            ])

        self._logger.info(f"Starting ffmpeg: {' '.join(cmd)}")

        # Clear previous stderr
        self._ffmpeg_stderr = []

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            pass_fds=pass_fds,
        )

        # Close write end of video pipe in parent process
        if self._video_write_fd is not None:
            os.close(self._video_write_fd)
            self._video_write_fd = None

        # Start reading output and stderr
        self._audio_reader_task = asyncio.create_task(self._read_audio())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        if self.video_enabled:
            self._logger.warning("Starting video reader task")
            self._video_reader_task = asyncio.create_task(self._read_video())
        else:
            self._logger.warning(f"Video reader NOT started: video_enabled={self.video_enabled}")

    async def _read_audio(self) -> None:
        """Read audio data from ffmpeg and push to queue."""
        chunk_bytes = int(
            self.audio_config.sample_rate
            * self.audio_config.channels
            * 2  # 16-bit samples = 2 bytes
            * self.audio_config.chunk_duration_ms
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
                    sample_rate=self.audio_config.sample_rate,
                    channels=self.audio_config.channels,
                )

                # Push to queue
                try:
                    self.audio_queue.put_nowait(chunk)
                    self._audio_chunks_sent += 1
                    self._audio_bytes_processed += len(data)
                    # Throttle UI events to reduce WebSocket traffic
                    now = time.time()
                    if now - self._last_ui_event_time >= self._ui_event_interval:
                        self._last_ui_event_time = now
                        self._publish_ui_event("chunk_produced", {
                            "bytes": len(data),
                            "chunk_num": self._audio_chunks_sent,
                            "total_bytes": self._audio_bytes_processed,
                        })
                except Exception:
                    self._logger.debug("Audio queue full, dropping chunk")

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error reading audio")
                await asyncio.sleep(0.1)

    async def _read_video(self) -> None:
        """Read video frames from ffmpeg and push to queue."""
        self._logger.warning(
            f"_read_video starting: video_enabled={self.video_enabled}, "
            f"video_read_fd={self._video_read_fd}, video_config={self.video_config is not None}"
        )
        if not self.video_enabled or self._video_read_fd is None or not self.video_config:
            self._logger.warning("_read_video returning early due to missing config")
            return

        # Calculate frame size
        if self.video_resolution:
            src_width, src_height = self.video_resolution
            width = self.video_config.scale_width
            height = int(width * src_height / src_width)
            height = height + (height % 2)  # Ensure even
        else:
            # Fallback: assume 16:9 aspect ratio
            width = self.video_config.scale_width
            height = int(width * 9 / 16)
            height = height + (height % 2)

        # Bytes per pixel depends on format (1 for gray, 3 for rgb24)
        bytes_per_pixel = 1 if self.video_config.pixel_format == "gray" else 3

        frame_size = width * height * bytes_per_pixel
        self._logger.warning(f"Video frame size: {width}x{height}, {frame_size} bytes per frame (read_fd={self._video_read_fd})")
        self._logger.warning(f"video_queue type={type(self.video_queue)}, value={self.video_queue}")

        loop = asyncio.get_event_loop()
        frames_read = 0

        while self._running and self._video_read_fd is not None:
            try:
                def read_frame() -> bytes:
                    if self._video_read_fd is None:
                        return b""
                    data = b""
                    while len(data) < frame_size:
                        try:
                            chunk = os.read(self._video_read_fd, frame_size - len(data))
                            if not chunk:
                                return data
                            data += chunk
                        except BlockingIOError:
                            return data
                        except OSError:
                            return b""
                    return data

                data = await loop.run_in_executor(None, read_frame)

                if not data or len(data) < frame_size:
                    if self._process and self._process.poll() is not None:
                        self._logger.info(f"Video reader: process ended, read {frames_read} frames total")
                        break
                    if data:
                        self._logger.debug(f"Video reader: partial frame {len(data)}/{frame_size} bytes")
                    continue

                frames_read += 1
                if frames_read <= 5 or frames_read % 10 == 0:
                    self._logger.warning(f"Video reader: frame {frames_read} received, {len(data)} bytes")

                # Create video frame
                frame = VideoFrame(
                    data=data,
                    width=width,
                    height=height,
                    pixel_format=self.video_config.pixel_format,
                    timestamp_ms=int(time.time() * 1000),
                )

                # Push to queue
                try:
                    if self.video_queue is not None:
                        self.video_queue.put_nowait(frame)
                        self._video_frames_sent += 1
                        self._video_bytes_processed += len(data)
                        if self._video_frames_sent <= 5 or self._video_frames_sent % 10 == 0:
                            self._logger.warning(f"Video frame {self._video_frames_sent} queued successfully")
                        self._publish_ui_event("frame_produced", {
                            "width": width,
                            "height": height,
                            "frame_num": self._video_frames_sent,
                            "total_bytes": self._video_bytes_processed,
                        })
                    else:
                        self._logger.warning(f"video_queue is None (type={type(self.video_queue)}), cannot queue frame")
                except Exception as e:
                    self._logger.warning(f"Failed to queue video frame: {e}")

            except asyncio.CancelledError:
                self._logger.info(f"Video reader cancelled, read {frames_read} frames")
                break
            except Exception as e:
                self._logger.exception(f"Error reading video frame: {e}")
                await asyncio.sleep(0.1)

        self._logger.info(f"Video reader loop ended, total frames: {frames_read}")

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
                source="media_ingest",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass


# Backwards compatibility alias
AudioIngestActor = MediaIngestActor

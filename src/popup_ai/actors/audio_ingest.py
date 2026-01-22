"""Audio ingest actor - backwards compatibility module.

This module re-exports from media_ingest for backwards compatibility.
New code should import from popup_ai.actors.media_ingest directly.
"""

from popup_ai.actors.media_ingest import (
    FFmpegNotFoundError,
    MediaIngestActor,
    PortInUseError,
    check_ffmpeg_available,
    is_port_available,
    kill_orphan_ffmpeg_on_port,
)

# Backwards compatibility alias
AudioIngestActor = MediaIngestActor

__all__ = [
    "AudioIngestActor",
    "MediaIngestActor",
    "FFmpegNotFoundError",
    "PortInUseError",
    "check_ffmpeg_available",
    "is_port_available",
    "kill_orphan_ffmpeg_on_port",
]

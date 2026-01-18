"""Audio format conversion utilities for test injection."""

import io
import logging

from pydub import AudioSegment

logger = logging.getLogger(__name__)

# Supported audio formats for conversion
SUPPORTED_FORMATS = {"wav", "mp3", "m4a", "flac", "ogg", "webm"}


def convert_to_pcm(
    file_bytes: bytes,
    format: str,
    target_sample_rate: int = 16000,
    target_channels: int = 1,
) -> tuple[bytes, int, int]:
    """Convert audio file bytes to raw PCM data.

    Args:
        file_bytes: Raw bytes of the audio file
        format: Source format (wav, mp3, m4a, flac, etc.)
        target_sample_rate: Target sample rate in Hz (default 16000)
        target_channels: Target number of channels (default 1 for mono)

    Returns:
        Tuple of (pcm_bytes, sample_rate, channels)

    Raises:
        ValueError: If format is not supported
        RuntimeError: If conversion fails
    """
    format = format.lower().lstrip(".")
    if format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported format: {format}. Supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    try:
        # Load audio from bytes
        audio = AudioSegment.from_file(io.BytesIO(file_bytes), format=format)

        # Convert to target sample rate and channels
        audio = audio.set_frame_rate(target_sample_rate)
        audio = audio.set_channels(target_channels)

        # Convert to 16-bit PCM
        audio = audio.set_sample_width(2)  # 16-bit = 2 bytes

        # Get raw PCM data
        pcm_bytes = audio.raw_data

        logger.debug(
            f"Converted {format} audio: {len(file_bytes)} bytes -> "
            f"{len(pcm_bytes)} bytes PCM @ {target_sample_rate}Hz, {target_channels}ch"
        )

        return pcm_bytes, target_sample_rate, target_channels

    except Exception as e:
        raise RuntimeError(f"Failed to convert audio: {e}") from e


def get_audio_duration_ms(pcm_bytes: bytes, sample_rate: int, channels: int) -> int:
    """Calculate duration of PCM audio in milliseconds.

    Args:
        pcm_bytes: Raw PCM data (16-bit)
        sample_rate: Sample rate in Hz
        channels: Number of channels

    Returns:
        Duration in milliseconds
    """
    bytes_per_sample = 2  # 16-bit
    total_samples = len(pcm_bytes) // (bytes_per_sample * channels)
    duration_s = total_samples / sample_rate
    return int(duration_s * 1000)


def chunk_pcm(
    pcm_bytes: bytes,
    sample_rate: int,
    channels: int,
    chunk_duration_ms: int = 500,
) -> list[bytes]:
    """Split PCM audio into chunks of specified duration.

    Args:
        pcm_bytes: Raw PCM data (16-bit)
        sample_rate: Sample rate in Hz
        channels: Number of channels
        chunk_duration_ms: Duration of each chunk in milliseconds

    Returns:
        List of PCM byte chunks
    """
    bytes_per_sample = 2  # 16-bit
    bytes_per_second = sample_rate * channels * bytes_per_sample
    chunk_size = int(bytes_per_second * chunk_duration_ms / 1000)

    chunks = []
    for i in range(0, len(pcm_bytes), chunk_size):
        chunk = pcm_bytes[i : i + chunk_size]
        if len(chunk) > 0:
            chunks.append(chunk)

    return chunks

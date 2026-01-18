"""Audio preprocessing for improved transcription quality.

Provides VAD (Voice Activity Detection), volume normalization, and silence trimming.
"""

import logging
from dataclasses import dataclass

import numpy as np

from popup_ai.config import TranscriberConfig

logger = logging.getLogger(__name__)


@dataclass
class PreprocessingResult:
    """Result of audio preprocessing."""

    audio_data: bytes
    has_speech: bool
    speech_ratio: float  # 0.0-1.0, ratio of audio that contains speech
    was_normalized: bool
    was_trimmed: bool


class AudioPreprocessor:
    """Preprocesses audio before transcription.

    Features:
    - VAD (Voice Activity Detection) using Silero VAD
    - Volume normalization (zero-mean unit-variance)
    - Silence trimming from start/end of audio
    """

    def __init__(self, config: TranscriberConfig) -> None:
        self.config = config
        self._vad_model = None
        self._logger = logging.getLogger("popup_ai.audio.preprocessor")

    def load_vad_model(self) -> None:
        """Load the Silero VAD model."""
        if not self.config.vad_enabled:
            return

        try:
            from silero_vad import load_silero_vad

            self._vad_model = load_silero_vad()
            self._logger.info("Silero VAD model loaded")
        except ImportError:
            self._logger.warning("silero-vad not installed, VAD disabled")
            self._vad_model = None
        except Exception as e:
            self._logger.warning(f"Failed to load VAD model: {e}")
            self._vad_model = None

    def process(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
    ) -> PreprocessingResult:
        """Process audio data with configured preprocessing steps.

        Args:
            audio_data: Raw PCM audio bytes (16-bit signed, mono)
            sample_rate: Audio sample rate in Hz

        Returns:
            PreprocessingResult with processed audio and metadata
        """
        # Convert bytes to numpy array (16-bit signed PCM)
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)

        # Track what we did
        was_normalized = False
        was_trimmed = False
        has_speech = True
        speech_ratio = 1.0

        # 1. Volume normalization (if enabled)
        if self.config.normalize_enabled:
            audio_array = self._normalize(audio_array)
            was_normalized = True

        # 2. VAD - detect speech segments (if enabled)
        if self.config.vad_enabled and self._vad_model is not None:
            has_speech, speech_ratio, audio_array = self._apply_vad(
                audio_array, sample_rate
            )

        # 3. Silence trimming (if enabled)
        if self.config.silence_trim_enabled:
            audio_array, was_trimmed = self._trim_silence(audio_array, sample_rate)

        # Convert back to bytes
        # Clip to int16 range and convert
        audio_array = np.clip(audio_array, -32768, 32767).astype(np.int16)
        processed_bytes = audio_array.tobytes()

        return PreprocessingResult(
            audio_data=processed_bytes,
            has_speech=has_speech,
            speech_ratio=speech_ratio,
            was_normalized=was_normalized,
            was_trimmed=was_trimmed,
        )

    def _normalize(self, audio: np.ndarray) -> np.ndarray:
        """Apply zero-mean unit-variance normalization.

        This normalization helps Whisper perform better according to the docs.
        """
        if len(audio) == 0:
            return audio

        # Zero mean
        audio = audio - np.mean(audio)

        # Unit variance (avoid division by zero)
        std = np.std(audio)
        if std > 0:
            audio = audio / std
            # Scale back to int16 range (use a reasonable amplitude)
            audio = audio * 8000  # ~1/4 of int16 max for headroom

        return audio

    def _apply_vad(
        self,
        audio: np.ndarray,
        sample_rate: int,
    ) -> tuple[bool, float, np.ndarray]:
        """Apply Voice Activity Detection.

        Returns:
            Tuple of (has_speech, speech_ratio, filtered_audio)
        """
        if self._vad_model is None:
            return True, 1.0, audio

        try:
            import torch
            from silero_vad import get_speech_timestamps

            # Convert to tensor (Silero expects float32 normalized to [-1, 1])
            audio_tensor = torch.from_numpy(audio.copy()).float()
            if audio_tensor.abs().max() > 1.0:
                audio_tensor = audio_tensor / 32768.0  # Normalize from int16 range

            # Get speech timestamps
            speech_timestamps = get_speech_timestamps(
                audio_tensor,
                self._vad_model,
                sampling_rate=sample_rate,
                threshold=self.config.vad_threshold,
                min_speech_duration_ms=self.config.vad_min_speech_ms,
                min_silence_duration_ms=self.config.vad_min_silence_ms,
            )

            if not speech_timestamps:
                # No speech detected
                return False, 0.0, audio

            # Calculate speech ratio
            total_samples = len(audio)
            speech_samples = sum(
                ts["end"] - ts["start"] for ts in speech_timestamps
            )
            speech_ratio = speech_samples / total_samples if total_samples > 0 else 0.0

            # Extract speech segments with buffer
            buffer_samples = int(self.config.silence_buffer_ms * sample_rate / 1000)
            speech_audio = []

            for ts in speech_timestamps:
                start = max(0, ts["start"] - buffer_samples)
                end = min(len(audio), ts["end"] + buffer_samples)
                speech_audio.append(audio[start:end])

            filtered_audio = np.concatenate(speech_audio) if speech_audio else audio

            return True, speech_ratio, filtered_audio

        except Exception as e:
            self._logger.warning(f"VAD processing failed: {e}")
            return True, 1.0, audio

    def _trim_silence(
        self,
        audio: np.ndarray,
        sample_rate: int,
    ) -> tuple[np.ndarray, bool]:
        """Trim silence from start and end of audio.

        Returns:
            Tuple of (trimmed_audio, was_trimmed)
        """
        if len(audio) == 0:
            return audio, False

        # Convert threshold from dB to amplitude
        # dB = 20 * log10(amplitude / reference)
        # For int16 audio, reference is 32768
        threshold_linear = 32768 * (10 ** (self.config.silence_threshold_db / 20))

        # Find first and last samples above threshold
        above_threshold = np.abs(audio) > threshold_linear

        if not np.any(above_threshold):
            # All silence - return original
            return audio, False

        # Find indices
        nonzero_indices = np.where(above_threshold)[0]
        start_idx = nonzero_indices[0]
        end_idx = nonzero_indices[-1] + 1

        # Add buffer
        buffer_samples = int(self.config.silence_buffer_ms * sample_rate / 1000)
        start_idx = max(0, start_idx - buffer_samples)
        end_idx = min(len(audio), end_idx + buffer_samples)

        # Check if we actually trimmed anything significant
        was_trimmed = (start_idx > buffer_samples) or (end_idx < len(audio) - buffer_samples)

        return audio[start_idx:end_idx], was_trimmed

    def get_stats(self) -> dict:
        """Get preprocessor stats for status reporting."""
        return {
            "vad_enabled": self.config.vad_enabled,
            "vad_loaded": self._vad_model is not None,
            "normalize_enabled": self.config.normalize_enabled,
            "silence_trim_enabled": self.config.silence_trim_enabled,
        }

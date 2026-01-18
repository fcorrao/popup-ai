"""Configuration system using pydantic-settings.

Layered config: defaults → config file → env vars → runtime updates.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioIngestConfig(BaseSettings):
    """Configuration for AudioIngestActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_AUDIO_")

    srt_port: int = Field(default=9998, description="SRT listener port")
    srt_latency_ms: int = Field(default=200, description="SRT latency in milliseconds")
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    channels: int = Field(default=1, description="Number of audio channels")
    chunk_duration_ms: int = Field(default=100, description="Audio chunk duration in ms")


class TranscriberConfig(BaseSettings):
    """Configuration for TranscriberActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_TRANSCRIBER_")

    # Model settings
    model: str = Field(default="mlx-community/whisper-base-mlx", description="Whisper model")
    chunk_length_s: float = Field(default=5.0, description="Audio chunk length for processing")
    overlap_s: float = Field(default=0.5, description="Overlap between chunks")
    language: str | None = Field(default=None, description="Language code or None for auto")

    # Audio preprocessing - VAD (Voice Activity Detection)
    vad_enabled: bool = Field(default=False, description="Enable Silero VAD preprocessing")
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="VAD speech threshold")
    vad_min_speech_ms: int = Field(default=250, description="Minimum speech duration in ms")
    vad_min_silence_ms: int = Field(default=100, description="Minimum silence duration in ms")

    # Audio preprocessing - Silence trimming
    silence_trim_enabled: bool = Field(default=False, description="Trim silence from chunks")
    silence_threshold_db: float = Field(default=-40.0, description="Silence threshold in dB")
    silence_buffer_ms: int = Field(default=100, description="Buffer around speech in ms")

    # Audio preprocessing - Normalization
    normalize_enabled: bool = Field(default=True, description="Enable volume normalization")


class AnnotatorConfig(BaseSettings):
    """Configuration for AnnotatorActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_ANNOTATOR_")

    provider: str = Field(default="openai", description="LLM provider")
    model: str = Field(default="gpt-4o-mini", description="LLM model")
    cache_enabled: bool = Field(default=True, description="Enable SQLite cache")
    cache_path: Path = Field(default=Path("~/.popup-ai/cache.db"), description="Cache DB path")
    prompt_template: str = Field(
        default="Extract key terms and provide brief explanations for: {text}",
        description="Prompt template for annotation",
    )


class OverlayConfig(BaseSettings):
    """Configuration for OverlayActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_OVERLAY_")

    obs_host: str = Field(default="localhost", description="OBS WebSocket host")
    obs_port: int = Field(default=4455, description="OBS WebSocket port")
    obs_password: str | None = Field(default=None, description="OBS WebSocket password")
    scene_name: str = Field(default="popup-ai-overlay", description="OBS scene name")
    hold_duration_ms: int = Field(default=5000, description="Annotation display duration")
    max_slots: int = Field(default=4, description="Number of overlay slots")


class PipelineConfig(BaseSettings):
    """Configuration for the overall pipeline."""

    model_config = SettingsConfigDict(env_prefix="POPUP_")

    audio_enabled: bool = Field(default=True, description="Enable audio ingest actor")
    transcriber_enabled: bool = Field(default=True, description="Enable transcriber actor")
    annotator_enabled: bool = Field(default=True, description="Enable annotator actor")
    overlay_enabled: bool = Field(default=True, description="Enable overlay actor")
    headless: bool = Field(default=False, description="Run without UI")
    log_level: str = Field(default="INFO", description="Logging level")


class Settings(BaseSettings):
    """Root settings container."""

    model_config = SettingsConfigDict(
        env_prefix="POPUP_",
        env_nested_delimiter="__",
    )

    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    audio: AudioIngestConfig = Field(default_factory=AudioIngestConfig)
    transcriber: TranscriberConfig = Field(default_factory=TranscriberConfig)
    annotator: AnnotatorConfig = Field(default_factory=AnnotatorConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)


def load_settings() -> Settings:
    """Load settings from all sources."""
    return Settings()

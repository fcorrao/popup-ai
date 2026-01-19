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
    vad_enabled: bool = Field(default=True, description="Enable Silero VAD preprocessing")
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
    base_url: str | None = Field(default=None, description="Custom API URL for local models")
    api_key_env_var: str | None = Field(default=None, description="Env var for API key")
    max_tokens: int = Field(default=500, description="Max response tokens")
    cache_enabled: bool = Field(default=True, description="Enable SQLite cache")
    cache_path: Path = Field(default=Path("~/.popup-ai/cache.db"), description="Cache DB path")
    prompt_template: str = Field(
        default="Extract up to 3 key technical terms or concepts from this transcript with terse explanations: {text}",
        description="Prompt template for annotation",
    )


# Model suggestions for each provider (used by UI)
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
    "anthropic": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    "cerebras": ["llama3.1-8b", "llama3.1-70b"],
    "openai_compatible": ["llama3.2", "mistral", "qwen2.5"],
}


class OverlayConfig(BaseSettings):
    """Configuration for OverlayActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_OVERLAY_")

    obs_host: str = Field(default="100.126.180.24", description="OBS WebSocket host")
    obs_port: int = Field(default=4455, description="OBS WebSocket port")
    obs_password: str | None = Field(default=None, description="OBS WebSocket password")
    scene_name: str = Field(default="popup-ai-overlay", description="OBS scene name")
    hold_duration_ms: int = Field(default=5000, description="Annotation display duration")

    # Scroll filter settings for horizontal scrolling text
    scroll_filter_name: str = Field(
        default="Scroll", description="Name of the scroll filter on each slot source"
    )
    scroll_viewport_width_px: int = Field(
        default=400, description="Fallback viewport width if not discoverable from OBS"
    )
    scroll_char_width_px: float = Field(
        default=12.0, description="Estimated pixels per character for text width calculation"
    )
    scroll_min_speed: float = Field(
        default=50.0, description="Minimum scroll speed (pixels/second)"
    )
    scroll_max_speed: float = Field(
        default=500.0, description="Maximum scroll speed (pixels/second)"
    )


class LogfireConfig(BaseSettings):
    """Configuration for Logfire observability."""

    model_config = SettingsConfigDict(env_prefix="POPUP_LOGFIRE_")

    enabled: bool = Field(default=True, description="Enable Logfire observability")
    sample_rate: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Trace sampling rate (0.0-1.0). Errors always captured.",
    )
    environment: str = Field(default="development", description="Environment name")
    dashboard_url: str = Field(
        default="https://logfire-us.pydantic.dev/fcorrao/popup-ai",
        description="Logfire dashboard URL",
    )


class PipelineConfig(BaseSettings):
    """Configuration for the overall pipeline."""

    model_config = SettingsConfigDict(env_prefix="POPUP_")

    audio_enabled: bool = Field(default=True, description="Enable audio ingest actor")
    transcriber_enabled: bool = Field(default=True, description="Enable transcriber actor")
    annotator_enabled: bool = Field(default=True, description="Enable annotator actor")
    overlay_enabled: bool = Field(default=True, description="Enable overlay actor")
    headless: bool = Field(default=False, description="Run without UI")
    log_level: str = Field(default="INFO", description="Logging level")
    ui_host: str = Field(
        default="0.0.0.0",
        description="Host for admin UI (0.0.0.0 for all interfaces)",
    )
    ui_port: int = Field(default=8080, description="Port for admin UI")


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
    logfire: LogfireConfig = Field(default_factory=LogfireConfig)


def load_settings() -> Settings:
    """Load settings from all sources."""
    return Settings()

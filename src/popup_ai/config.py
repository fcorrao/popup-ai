"""Configuration system using pydantic-settings.

Layered config: defaults → config file → env vars → runtime updates.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioIngestConfig(BaseSettings):
    """Configuration for MediaIngestActor (audio settings)."""

    model_config = SettingsConfigDict(env_prefix="POPUP_AUDIO_")

    srt_port: int = Field(default=9998, description="SRT listener port")
    srt_latency_ms: int = Field(default=200, description="SRT latency in milliseconds")
    srt_rcvbuf_mb: int = Field(default=64, description="SRT receive buffer size in MB")
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    channels: int = Field(default=1, description="Number of audio channels")
    chunk_duration_ms: int = Field(default=200, description="Audio chunk duration in ms")
    ffmpeg_threads: int = Field(default=4, description="FFmpeg thread count (limits CPU usage)")


class VideoIngestConfig(BaseSettings):
    """Configuration for video frame extraction."""

    model_config = SettingsConfigDict(env_prefix="POPUP_VIDEO_")

    enabled: bool = Field(default=True, description="Enable video frame extraction")
    fps: float = Field(default=1.0, description="Target frames per second")
    scale_width: int = Field(default=1280, description="Frame width (height auto)")
    pixel_format: str = Field(default="rgb24", description="Pixel format: rgb24 or gray")


class OcrConfig(BaseSettings):
    """Configuration for OcrActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_OCR_")

    enabled: bool = Field(default=True, description="Enable OCR processing")
    engine: str = Field(default="rapidocr", description="OCR engine: rapidocr, tesseract")
    languages: list[str] = Field(default=["eng"], description="OCR languages")
    min_confidence: float = Field(default=0.4, description="Min OCR confidence threshold")
    min_chars: int = Field(default=3, description="Min text length to emit")
    dedupe_window_s: int = Field(default=60, description="Dedupe window in seconds")


class TranscriberConfig(BaseSettings):
    """Configuration for TranscriberActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_TRANSCRIBER_")

    # Model settings
    model: str = Field(default="mlx-community/whisper-base-mlx", description="Whisper model")
    chunk_length_s: float = Field(default=2.0, description="Audio chunk length for processing")
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

    provider: str = Field(default="cerebras", description="LLM provider")
    model: str = Field(default="gpt-oss-120b", description="LLM model")
    base_url: str | None = Field(default="http://192.168.1.64:1234/v1", description="Custom API URL for local models (e.g., LM Studio)")
    api_key_env_var: str | None = Field(default=None, description="Env var for API key")
    max_tokens: int = Field(default=500, description="Max response tokens")
    cache_enabled: bool = Field(default=True, description="Enable SQLite cache")
    cache_path: Path = Field(default=Path("~/.popup-ai/cache.db"), description="Cache DB path")
    system_prompt: str = Field(
        default="""You are a helpful assistant that extracts key terms from speech transcripts and provides brief, educational explanations.

Focus on "terms of art" - specialized jargon that someone outside programming or computer science would NOT be familiar with. These are words that have specific technical meanings in our field but would confuse a general audience.

GOOD examples of terms to annotate:
- "emacs" → "A text editor like MS Word used by programmers. Very extensible, so it can do much more than edit text."
- "internal fragmentation" → "Renting a storage unit too big for your stuff—you pay for space you can't use."
- "garbage collection" → "Automatic cleanup of memory your program no longer needs, like a self-emptying trash can."
- "race condition" → "When two processes compete to update the same data, like two people editing the same document—whoever saves last wins."

BAD examples (too common/obvious, don't annotate these):
- "computer", "software", "website", "app", "code", "programming"

Guidelines:
- Use metaphors and analogies to everyday objects when possible
- Keep explanations terse - readable in under 5 seconds
- Return 1-3 annotations per transcript depending on content density
- Return an empty annotations list if no terms warrant explanation""",
        description="System prompt for the LLM",
    )
    prompt_template: str = Field(
        default="Extract key technical terms or concepts from this transcript: {text}",
        description="Prompt template for annotation",
    )


# Model suggestions for each provider (used by UI)
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5-nano", "gpt-4.1-mini", "gpt-5", "gpt-5.2"],
    "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"],
    "cerebras": ["gpt-oss-120b", "llama-3.3-70b"],
    "openai_compatible": ["nvidia/nemotron-3-nano", "llama3.2", "mistral", "qwen2.5"],
}


class OverlayConfig(BaseSettings):
    """Configuration for OverlayActor (browser source only)."""

    model_config = SettingsConfigDict(env_prefix="POPUP_OVERLAY_")

    obs_host: str = Field(default="100.126.180.24", description="OBS WebSocket host")
    obs_port: int = Field(default=4455, description="OBS WebSocket port")
    obs_password: str | None = Field(default=None, description="OBS WebSocket password")

    # Browser panel settings
    chatlog_mode: bool = Field(
        default=True, description="Chatlog mode (stack entries) vs single mode (one at a time)"
    )
    max_entries_per_panel: int = Field(
        default=6, description="Max entries per panel before overflow to panel 2 (chatlog mode)"
    )


class DiagnosticsConfig(BaseSettings):
    """Configuration for DiagnosticsActor."""

    model_config = SettingsConfigDict(env_prefix="POPUP_DIAGNOSTICS_")

    enabled: bool = Field(default=True, description="Enable diagnostics actor")
    sample_interval: float = Field(default=1.0, description="Sample interval in seconds")
    history_size: int = Field(default=300, description="Number of samples to keep (5 min at 1/sec)")


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
    video_enabled: bool = Field(default=True, description="Enable video frame extraction")
    ocr_enabled: bool = Field(default=True, description="Enable OCR actor")
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
    video: VideoIngestConfig = Field(default_factory=VideoIngestConfig)
    transcriber: TranscriberConfig = Field(default_factory=TranscriberConfig)
    annotator: AnnotatorConfig = Field(default_factory=AnnotatorConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    logfire: LogfireConfig = Field(default_factory=LogfireConfig)


def load_settings() -> Settings:
    """Load settings from all sources."""
    return Settings()

"""Message types for inter-actor communication.

Defines Pydantic models for data flowing between pipeline actors via Ray Queues.
"""

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegment(BaseModel):
    """A single segment of transcribed speech."""

    text: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0


class AudioChunk(BaseModel):
    """Audio data chunk from ingest actor.

    Flows: AudioIngestActor → TranscriberActor
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: bytes
    timestamp_ms: int
    sample_rate: int = 16000
    channels: int = 1


class Transcript(BaseModel):
    """Transcription result from STT or OCR processing.

    Flows: TranscriberActor → AnnotatorActor
           OcrActor → AnnotatorActor
    """

    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    is_partial: bool = False
    timestamp_ms: int = 0
    source: str = "audio"  # "audio" or "ocr"


class VideoFrame(BaseModel):
    """Video frame for OCR processing.

    Flows: MediaIngestActor → OcrActor
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: bytes
    width: int
    height: int
    pixel_format: str  # "gray" or "rgb24"
    timestamp_ms: int


class Annotation(BaseModel):
    """LLM-generated annotation for display.

    Flows: AnnotatorActor → OverlayActor
    """

    term: str
    explanation: str
    display_duration_ms: int = 5000
    slot: int = Field(ge=1, le=4, default=1)
    timestamp_ms: int = 0


class UIEvent(BaseModel):
    """Event for UI updates from any actor.

    Flows: All actors → UI
    """

    source: str
    event_type: str
    data: dict = Field(default_factory=dict)
    timestamp_ms: int = 0


class ActorStatus(BaseModel):
    """Status information for an actor."""

    name: str
    state: str  # "stopped", "starting", "running", "error"
    error: str | None = None
    stats: dict = Field(default_factory=dict)

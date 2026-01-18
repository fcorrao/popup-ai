"""Message types for inter-actor communication.

Defines Pydantic models for data flowing between pipeline actors via Ray Queues.
"""

from pydantic import BaseModel, Field


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

    data: bytes
    timestamp_ms: int
    sample_rate: int = 16000
    channels: int = 1

    class Config:
        arbitrary_types_allowed = True


class Transcript(BaseModel):
    """Transcription result from STT processing.

    Flows: TranscriberActor → AnnotatorActor
    """

    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    is_partial: bool = False
    timestamp_ms: int = 0


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

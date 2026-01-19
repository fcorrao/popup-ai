# Actors Reference

Technical reference for popup-ai's Ray actors.

## Overview

popup-ai uses [Ray](https://ray.io/) actors as the backbone of its pipeline architecture. Each pipeline stage is an isolated actor that communicates via Ray Queues.

## Actor Hierarchy

```
PipelineSupervisor
├── AudioIngestActor
├── TranscriberActor
├── AnnotatorActor
└── OverlayActor
```

## PipelineSupervisor

**Module:** `popup_ai.actors.supervisor`

The supervisor manages all child actor lifecycles.

### Responsibilities

- Spawn and configure child actors
- Health monitoring (every 5 seconds)
- Automatic restart on actor crash
- Graceful shutdown coordination
- Queue creation and management

### Methods

| Method | Description |
|--------|-------------|
| `start()` | Start all enabled actors |
| `stop()` | Stop all actors gracefully |
| `get_status()` | Get status of all actors |
| `start_actor(name)` | Start a specific actor |
| `stop_actor(name)` | Stop a specific actor |
| `restart_actor(name)` | Restart a specific actor |
| `configure(settings)` | Update configuration |

### Queues Created

| Queue | Capacity | Flow |
|-------|----------|------|
| `audio` | 100 | AudioIngest → Transcriber |
| `transcript` | 100 | Transcriber → Annotator |
| `annotation` | 100 | Annotator → Overlay |
| `ui` | 1000 | All actors → UI |

## AudioIngestActor

**Module:** `popup_ai.actors.audio_ingest`

Captures audio from SRT stream via ffmpeg subprocess.

### Configuration

Uses `AudioIngestConfig`:

- `srt_port`: SRT listener port
- `srt_latency_ms`: SRT latency
- `sample_rate`: Audio sample rate (Hz)
- `channels`: Number of channels
- `chunk_duration_ms`: Chunk size

### Data Flow

```
SRT Stream → ffmpeg subprocess → PCM data → AudioChunk → audio_queue
```

### Stats

| Stat | Description |
|------|-------------|
| `chunks_sent` | Number of audio chunks sent |
| `bytes_processed` | Total bytes processed |
| `uptime_s` | Actor uptime in seconds |

### ffmpeg Command

```bash
ffmpeg -hide_banner -loglevel warning \
  -i "srt://0.0.0.0:{port}?mode=listener&latency={latency}" \
  -vn -acodec pcm_s16le -ar {sample_rate} -ac {channels} \
  -f s16le pipe:1
```

## TranscriberActor

**Module:** `popup_ai.actors.transcriber`

Transcribes audio using mlx-whisper.

### Configuration

Uses `TranscriberConfig`:

- `model`: Whisper model identifier
- `chunk_length_s`: Audio chunk length
- `overlap_s`: Overlap between chunks
- `language`: Language code or None

### Data Flow

```
audio_queue → AudioChunk → buffer → WAV file → mlx-whisper → Transcript → transcript_queue
```

### Processing Logic

1. Accumulate AudioChunks in buffer
2. When buffer >= `chunk_length_s`:
   - Write buffer to temp WAV file
   - Run mlx-whisper transcription
   - Keep `overlap_s` of audio for next chunk
   - Push Transcript to output queue

### Stats

| Stat | Description |
|------|-------------|
| `transcripts_sent` | Number of transcripts sent |
| `audio_seconds_processed` | Total audio processed |
| `buffer_duration_s` | Current buffer size |
| `uptime_s` | Actor uptime in seconds |

## AnnotatorActor

**Module:** `popup_ai.actors.annotator`

Extracts terms and generates explanations via LLM.

### Configuration

Uses `AnnotatorConfig`:

- `provider`: LLM provider
- `model`: LLM model
- `cache_enabled`: Enable SQLite cache
- `cache_path`: Cache database path
- `prompt_template`: Prompt template

### Data Flow

```
transcript_queue → Transcript → cache check → LLM call → Annotation → annotation_queue
```

### Cache Schema

```sql
CREATE TABLE annotations (
    text_hash TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at REAL NOT NULL
)
```

### Stats

| Stat | Description |
|------|-------------|
| `annotations_sent` | Number of annotations sent |
| `cache_hits` | Cache hits |
| `llm_calls` | LLM API calls made |
| `uptime_s` | Actor uptime in seconds |

## OverlayActor

**Module:** `popup_ai.actors.overlay`

Sends annotations to OBS via WebSocket.

### Configuration

Uses `OverlayConfig`:

- `obs_host`: OBS WebSocket host
- `obs_port`: OBS WebSocket port
- `obs_password`: OBS WebSocket password
- `scene_name`: Target OBS scene
- `hold_duration_ms`: Display duration

### Data Flow

```
annotation_queue → Annotation → slot assignment → OBS WebSocket → text source update
```

### Slot Management

- **Discovery-based**: Discovers existing `popup-ai-slot-*` sources in OBS (no auto-creation)
- **Smart selection**: Always uses the first available (invisible) slot
- **Queuing**: Annotations queue when all slots are busy, display when slots clear
- Auto-hide after `hold_duration_ms`
- Cleanup loop runs every 500ms

### OBS Sources

User must manually create text sources in OBS named:

- `popup-ai-slot-1` (required)
- `popup-ai-slot-2` (optional, for simultaneous display)
- `popup-ai-slot-3` (optional)
- etc.

The overlay actor discovers these sources by pattern matching and works with whatever slots exist.

### Stats

| Stat | Description |
|------|-------------|
| `annotations_displayed` | Total annotations shown |
| `obs_connected` | OBS connection status |
| `discovered_slots` | Number of discovered slot sources |
| `active_slots` | Currently displaying slots |
| `queued` | Annotations waiting for a free slot |
| `retry_count` | OBS reconnection attempts |
| `uptime_s` | Actor uptime in seconds |

## Message Types

Defined in `popup_ai.messages`:

### AudioChunk

```python
class AudioChunk(BaseModel):
    data: bytes           # Raw PCM audio data
    timestamp_ms: int     # Capture timestamp
    sample_rate: int      # Sample rate (default: 16000)
    channels: int         # Channels (default: 1)
```

### Transcript

```python
class Transcript(BaseModel):
    text: str                           # Full transcript text
    segments: list[TranscriptSegment]   # Word-level segments
    is_partial: bool                    # Partial vs final
    timestamp_ms: int                   # Processing timestamp
```

### Annotation

```python
class Annotation(BaseModel):
    term: str                # Extracted term
    explanation: str         # Brief explanation
    display_duration_ms: int # Display time (default: 5000)
    slot: int               # Overlay slot number
    timestamp_ms: int       # Generation timestamp
```

### UIEvent

```python
class UIEvent(BaseModel):
    source: str      # Actor name
    event_type: str  # Event type (started, stopped, error, etc.)
    data: dict       # Event-specific data
    timestamp_ms: int
```

### ActorStatus

```python
class ActorStatus(BaseModel):
    name: str           # Actor name
    state: str          # stopped, starting, running, error
    error: str | None   # Error message if state is error
    stats: dict         # Actor-specific statistics
```

## Health Checks

Each actor implements `health_check() -> bool`:

- Returns `True` if actor is healthy
- Supervisor checks every 5 seconds
- Unhealthy actors are automatically restarted

## Independent Testability

Each actor is designed to be testable in isolation without requiring the full pipeline:

### Design Principles

- **Queue-based I/O**: Actors only communicate via Ray Queues, not direct method calls
- **Configuration injection**: All settings passed via config objects
- **No global state**: Actors are self-contained
- **Optional dependencies**: External services (OBS, LLM APIs) handled gracefully when unavailable

### Testing an Actor in Isolation

```python
import ray
from ray.util.queue import Queue
from popup_ai.actors.annotator import AnnotatorActor
from popup_ai.config import AnnotatorConfig
from popup_ai.messages import Transcript

ray.init()

# Create test queues
input_queue = Queue()
output_queue = Queue()

# Create actor with test config
config = AnnotatorConfig(cache_enabled=False)
actor = AnnotatorActor.remote(config, input_queue, output_queue)

# Start and inject test data
await actor.start.remote()
input_queue.put(Transcript(text="Test transcript", segments=[], is_partial=False, timestamp_ms=0))

# Verify output
annotation = output_queue.get(timeout=10)
assert annotation.term is not None
```

### UI Injection for Testing

The admin UI supports direct injection of test data into actors via the Annotator tab:

1. Start pipeline with only the Annotator actor enabled
2. Use the "Inject Transcript" feature to send test text
3. Observe annotations in the Overlay tab or OBS

This allows testing the LLM integration without needing live audio input.

## See Also

- [Architecture](../explanation/architecture.md) - Design overview
- [Ray Actors](../explanation/ray-actors.md) - Why Ray?

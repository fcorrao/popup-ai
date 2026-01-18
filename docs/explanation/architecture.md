# Architecture Overview

This document explains popup-ai's architecture and the reasoning behind key design decisions.

## System Overview

popup-ai is a **modular monolith** - a single process with isolated components that communicate via message queues.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Main Process                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   NiceGUI Admin UI                         │  │
│  │  • Actor status dashboard    • Settings configuration      │  │
│  │  • Pipeline controls         • Live transcript view        │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              │ actor handles                     │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              PipelineSupervisor (Ray Actor)                │  │
│  │  • Spawns/manages child actors    • Health monitoring      │  │
│  │  • Restart policies               • Graceful shutdown      │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
         ▼                     ▼                     ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ AudioIngestActor│  │TranscriberActor │  │ AnnotatorActor  │
│                 │  │                 │  │                 │
│ ffmpeg SRT→PCM  │─▶│ mlx-whisper     │─▶│ pydantic-ai     │
│ chunks to queue │  │ PCM→transcript  │  │ text→annotation │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │ OverlayActor    │
                                          │                 │
                                          │ obsws-python    │
                                          │ annotation→OBS  │
                                          └─────────────────┘
```

## Components

### Main Process

The main Python process runs:

- **NiceGUI Admin UI**: Web interface for monitoring and control
- **Ray runtime**: Manages actor lifecycle and communication

### PipelineSupervisor

A Ray actor that orchestrates the pipeline:

- Creates Ray Queues for inter-actor communication
- Spawns child actors based on configuration
- Monitors actor health every 5 seconds
- Restarts crashed actors automatically
- Coordinates graceful shutdown

### Pipeline Actors

Four specialized actors, each handling one pipeline stage:

| Actor | Responsibility | Technology |
|-------|----------------|------------|
| AudioIngestActor | Capture SRT audio | ffmpeg subprocess |
| TranscriberActor | Speech-to-text | mlx-whisper |
| AnnotatorActor | Term extraction | pydantic-ai + SQLite |
| OverlayActor | OBS display | obsws-python |

## Data Flow

Data flows through the pipeline via Ray Queues:

```
SRT Stream
    │
    ▼
┌─────────────────┐
│ AudioIngestActor│
└────────┬────────┘
         │ AudioChunk
         ▼
    [audio_queue]
         │
         ▼
┌─────────────────┐
│ TranscriberActor│
└────────┬────────┘
         │ Transcript
         ▼
  [transcript_queue]
         │
         ▼
┌─────────────────┐
│ AnnotatorActor  │
└────────┬────────┘
         │ Annotation
         ▼
  [annotation_queue]
         │
         ▼
┌─────────────────┐
│  OverlayActor   │
└────────┬────────┘
         │
         ▼
    OBS WebSocket
```

### Message Types

| Type | Fields | Flow |
|------|--------|------|
| AudioChunk | data, timestamp_ms, sample_rate, channels | AudioIngest → Transcriber |
| Transcript | text, segments, is_partial, timestamp_ms | Transcriber → Annotator |
| Annotation | term, explanation, slot, display_duration_ms | Annotator → Overlay |
| UIEvent | source, event_type, data, timestamp_ms | All actors → UI |

### Queue Properties

- **Bounded capacity**: 100 items (audio, transcript, annotation), 1000 (ui)
- **Backpressure**: Producers drop items if queue is full
- **Async-compatible**: Non-blocking put/get operations

## UI Integration

The admin UI runs in the main process and communicates with actors via:

1. **Actor handles**: Direct method calls (start, stop, get_status)
2. **UI event queue**: Actors push events, UI polls for updates

```python
# UI calling actor method
await supervisor.start.remote()

# UI receiving events
event = ui_queue.get_nowait()
if event.source == "transcriber":
    transcript_log.push(event.data["text"])
```

This design means:

- UI is always responsive (not blocked by actor work)
- UI can start/stop without affecting running actors
- Actor crashes don't crash the UI

## Design Decisions

### Why Ray?

We evaluated several options for actor-based concurrency:

| Option | Pros | Cons |
|--------|------|------|
| asyncio | Simple, no deps | No isolation, complex error handling |
| multiprocessing | Isolation | Complex IPC, no supervision |
| Celery | Battle-tested | Heavy, needs Redis/RabbitMQ |
| **Ray** | Isolation, supervision, queues | Learning curve, memory overhead |

Ray won because:

- **Actor isolation**: Each actor runs in its own process
- **Built-in queues**: `ray.util.queue.Queue` for backpressure-aware messaging
- **Supervision**: Detect crashes, get actor state
- **Apple Silicon**: Good performance on M1/M2/M3

### Why Modular Monolith?

We considered microservices but chose a monolith because:

- **Simpler deployment**: One process to manage
- **Lower latency**: No network hops between components
- **Easier debugging**: Single process, shared logs
- **Good enough isolation**: Ray actors provide fault boundaries

The architecture is modular enough that individual actors could be extracted to separate services if needed.

### Why NiceGUI?

For the admin UI, we chose NiceGUI over alternatives:

| Option | Pros | Cons |
|--------|------|------|
| FastAPI + React | Full control | Two codebases, more complexity |
| Streamlit | Easy | Stateless, reruns on every change |
| Gradio | ML-focused | Limited customization |
| **NiceGUI** | Python-only, reactive | Less mature ecosystem |

NiceGUI lets us build the entire UI in Python with reactive updates.

### Why SQLite for Cache?

The annotator caches LLM results in SQLite:

- **Zero config**: No database server needed
- **Persistent**: Survives restarts
- **Fast**: Local file, no network
- **Simple**: Single table, key-value pattern

For v1, this is sufficient. Future versions may add Redis for distributed caching.

## Failure Modes

### Actor Crash

1. Supervisor detects crash via health check
2. Supervisor respawns the actor
3. Actor restarts with fresh state
4. Data in queues is preserved

### OBS Disconnection

1. OverlayActor detects connection failure
2. Continues processing (annotations queued)
3. Reconnects when OBS is available
4. Resumes sending annotations

### LLM API Failure

1. AnnotatorActor catches exception
2. Logs error, continues processing
3. No annotation generated for that transcript
4. Pipeline continues with next transcript

## Performance Considerations

### Memory

- Each actor runs in a separate process
- Whisper models load into GPU memory
- SQLite cache grows with unique transcripts
- Queues are bounded to prevent unbounded growth

### Latency

Approximate latency breakdown:

| Stage | Typical Latency |
|-------|-----------------|
| SRT ingest | 200ms (configurable) |
| Audio buffering | 5s (chunk_length) |
| Whisper transcription | 1-5s (depends on model) |
| LLM annotation | 1-3s (depends on provider) |
| OBS update | <100ms |

**Total end-to-end**: 7-15 seconds from speech to overlay.

### Throughput

- Audio: Real-time (1x)
- Transcription: ~0.5-2x real-time (depends on model)
- Annotation: Limited by LLM API rate limits

## See Also

- [Ray Actors](ray-actors.md) - Deep dive into Ray
- [Actors Reference](../reference/actors.md) - Technical details
- [Configuration](../reference/configuration.md) - All settings

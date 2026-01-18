# Ray Actors

This document explains why popup-ai uses Ray actors and how they work.

## What is Ray?

[Ray](https://ray.io/) is a framework for building distributed applications. popup-ai uses Ray's actor model for:

- **Process isolation**: Each actor runs in its own process
- **Fault tolerance**: Crashed actors can be detected and restarted
- **Message passing**: Queues for inter-actor communication
- **Async/await**: Native async support in actors

## Why Actors?

The popup-ai pipeline has natural stage boundaries:

```
Audio Capture → Transcription → Annotation → Overlay
```

Each stage:

- Has different failure modes
- Runs at different speeds
- Can be developed/tested independently
- Should not block other stages

Actors provide this isolation naturally. If the LLM API fails, transcription continues. If Whisper crashes, it restarts without affecting the overlay.

## Actor Basics

### Defining an Actor

Ray actors are Python classes decorated with `@ray.remote`:

```python
import ray

@ray.remote
class MyActor:
    def __init__(self, config):
        self.config = config
        self.state = {}

    def get_state(self):
        return self.state

    async def process(self, data):
        # Do work
        result = await expensive_operation(data)
        return result
```

### Creating Actors

Actors are created with `.remote()`:

```python
# Create an actor instance
actor = MyActor.remote(config)

# Call methods with .remote()
result = await actor.process.remote(data)

# Get return value with ray.get()
state = ray.get(actor.get_state.remote())
```

### Actor Lifecycle

```
Created → Running → Stopped
              ↓
           Crashed → Restarted
```

popup-ai's supervisor manages this lifecycle for all pipeline actors.

## Queue Communication

### Why Queues?

Actors could call each other directly:

```python
# Direct call (not recommended)
transcript = await transcriber.process.remote(audio)
annotation = await annotator.process.remote(transcript)
```

But this creates tight coupling. Instead, we use queues:

```python
# Queue-based (recommended)
audio_queue.put(audio_chunk)
# Transcriber pulls from audio_queue, pushes to transcript_queue
# Annotator pulls from transcript_queue, pushes to annotation_queue
```

Benefits:

- **Decoupling**: Actors don't know about each other
- **Backpressure**: Bounded queues prevent memory explosion
- **Buffering**: Fast producers don't overwhelm slow consumers
- **Observability**: Queue depths indicate pipeline health

### Ray Queue API

```python
from ray.util.queue import Queue

# Create bounded queue
queue = Queue(maxsize=100)

# Non-blocking put (raises if full)
queue.put_nowait(item)

# Blocking get with timeout
item = queue.get(block=True, timeout=0.5)

# Non-blocking get (raises if empty)
item = queue.get_nowait()
```

### Queue Topology

popup-ai creates four queues:

```
audio_queue (100)     : AudioIngest → Transcriber
transcript_queue (100): Transcriber → Annotator
annotation_queue (100): Annotator → Overlay
ui_queue (1000)       : All actors → Admin UI
```

## Supervision

### Health Monitoring

The supervisor checks actor health every 5 seconds:

```python
async def _health_monitor(self):
    while self._running:
        await asyncio.sleep(5)

        for name, actor in self._actors.items():
            try:
                healthy = await actor.health_check.remote()
                if not healthy:
                    await self.restart_actor(name)
            except ray.exceptions.RayActorError:
                # Actor crashed
                await self._spawn_actor(name)
```

### Health Check Implementation

Each actor implements a simple health check:

```python
def health_check(self) -> bool:
    return self._state == "running" and self._running
```

Actors can report unhealthy if:

- Internal error state
- External dependency unavailable
- Processing stalled

### Restart Policy

On crash or unhealthy status:

1. Old actor is killed (`ray.kill()`)
2. New actor is spawned with same config
3. Actor reconnects to queues
4. Processing resumes

Data in queues is preserved across restarts.

## Error Handling

### Actor Crashes

Ray detects when an actor process dies:

```python
try:
    await actor.method.remote()
except ray.exceptions.RayActorError:
    # Actor crashed
    pass
```

The supervisor catches this and restarts the actor.

### Method Exceptions

Exceptions in actor methods are captured and re-raised to callers:

```python
@ray.remote
class MyActor:
    async def risky_method(self):
        raise ValueError("Something went wrong")

# Caller sees the exception
try:
    await actor.risky_method.remote()
except ValueError:
    # Handle error
    pass
```

### Graceful Degradation

popup-ai actors are designed to degrade gracefully:

- **AudioIngest**: If ffmpeg dies, reports error but doesn't crash
- **Transcriber**: If Whisper fails, logs error and continues
- **Annotator**: If LLM fails, skips annotation and continues
- **Overlay**: If OBS disconnects, continues processing

## Performance

### Process Overhead

Each actor is a separate Python process:

- ~50-100MB base memory per actor
- Context switch overhead for queue operations
- Worth it for isolation benefits

### Serialization

Data crossing actor boundaries is serialized:

- Small messages (AudioChunk): Fast, minimal overhead
- Large objects: Consider memory-mapped files or object store

popup-ai's messages are small Pydantic models, so serialization is fast.

### Local vs. Distributed

popup-ai runs Ray in local mode:

```python
ray.init(ignore_reinit_error=True, logging_level=logging.WARNING)
```

For single-machine use, this is optimal. Ray can scale to clusters if needed.

## Comparison to Alternatives

### vs. asyncio

```python
# asyncio approach
async def pipeline():
    audio = await capture_audio()
    transcript = await transcribe(audio)
    annotation = await annotate(transcript)
    await display(annotation)
```

Problems:

- One crash takes down everything
- No process isolation
- Complex error handling

### vs. multiprocessing

```python
# multiprocessing approach
audio_queue = multiprocessing.Queue()
transcript_queue = multiprocessing.Queue()

Process(target=transcribe, args=(audio_queue, transcript_queue)).start()
```

Problems:

- Manual process management
- No supervision
- Complex IPC patterns

### vs. Celery

```python
# Celery approach
@celery.task
def transcribe(audio):
    return whisper.transcribe(audio)
```

Problems:

- Requires Redis/RabbitMQ
- Heavier deployment
- Task-based, not actor-based

### Ray Wins

Ray provides:

- Actor model (stateful processes)
- Built-in supervision (detect crashes)
- Simple queues (no external deps)
- Async/await native
- Good local performance

## Further Reading

- [Ray Documentation](https://docs.ray.io/)
- [Ray Actors Guide](https://docs.ray.io/en/latest/ray-core/actors.html)
- [Ray Queues](https://docs.ray.io/en/latest/ray-core/api/doc/ray.util.queue.Queue.html)
- [Architecture Overview](architecture.md) - How actors fit into popup-ai

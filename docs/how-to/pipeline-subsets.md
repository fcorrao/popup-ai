# Run Pipeline Subsets

Run only the pipeline stages you need for specific workflows.

## Understanding Actors

popup-ai has four pipeline actors:

| Actor | Function | Depends On |
|-------|----------|------------|
| `audio_ingest` | Captures SRT audio | None |
| `transcriber` | Converts audio to text | `audio_ingest` |
| `annotator` | Extracts terms via LLM | `transcriber` |
| `overlay` | Sends to OBS | `annotator` |

## Enabling/Disabling Actors

### Using CLI Flags (Recommended)

The easiest way to control actors is with CLI flags:

```bash
# Enable only specific actors
uv run popup-ai --actors audio,transcriber

# Disable specific actors (keep others enabled)
uv run popup-ai --no-overlay
uv run popup-ai --no-annotator --no-overlay
```

Available flags:

| Flag | Description |
|------|-------------|
| `--actors LIST` | Comma-separated list of actors to enable (disables others) |
| `--no-audio` | Disable audio ingest actor |
| `--no-transcriber` | Disable transcriber actor |
| `--no-annotator` | Disable annotator actor |
| `--no-overlay` | Disable overlay actor |

### Using Environment Variables

Each actor also has an enable flag via environment variables:

```bash
# Enable/disable specific actors
export POPUP_AUDIO_ENABLED=true
export POPUP_TRANSCRIBER_ENABLED=true
export POPUP_ANNOTATOR_ENABLED=true
export POPUP_OVERLAY_ENABLED=true
```

Set to `false` to disable an actor.

## Common Patterns

### Audio + Transcription Only

Run transcription without LLM annotations or OBS overlay:

```bash
# Using CLI flags
uv run popup-ai --actors audio,transcriber

# Or using --no flags
uv run popup-ai --no-annotator --no-overlay

# Or using environment variables
export POPUP_ANNOTATOR_ENABLED=false
export POPUP_OVERLAY_ENABLED=false
uv run popup-ai
```

### Full Pipeline Except OBS

Run the full pipeline but skip OBS integration:

```bash
uv run popup-ai --no-overlay
```

Useful when:

- Testing transcription/annotation without OBS
- OBS is on a different machine (future remote support)
- Developing/debugging

### Transcription + Annotation (No OBS)

Test LLM annotations without OBS:

```bash
uv run popup-ai --no-overlay
```

### Full Pipeline

All actors enabled (default):

```bash
uv run popup-ai
```

### Transcription Only (No Audio Ingest)

!!! warning "Not Yet Implemented"
    File-based transcription input is planned for a future release.

## Actor Dependencies

When disabling actors, be aware of dependencies:

```
audio_ingest → transcriber → annotator → overlay
```

- Disabling `transcriber` means `annotator` gets no input
- Disabling `annotator` means `overlay` gets no input

The pipeline handles this gracefully - downstream actors simply wait for input that never comes.

## Checking Actor Status

In the admin UI, disabled actors show as grey (stopped) in the Actor Status panel.

You can verify which actors are running:

```bash
# Check Ray actors
uv run python -c "import ray; ray.init(); print(ray.actors())"
```

## What's Next?

- [Configuration Reference](../reference/configuration.md) - All enable flags
- [Architecture](../explanation/architecture.md) - How actors work together

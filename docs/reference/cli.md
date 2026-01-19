# CLI Reference

Complete command-line interface reference for popup-ai.

## Synopsis

```bash
popup-ai [OPTIONS] [COMMAND]
```

## Global Options

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Show version and exit |
| `--headless` | | Run without UI |
| `--config PATH` | `-c` | Path to config file |
| `--actors LIST` | | Comma-separated list of actors to enable |
| `--no-audio` | | Disable audio ingest actor |
| `--no-transcriber` | | Disable transcriber actor |
| `--no-annotator` | | Disable annotator actor |
| `--no-overlay` | | Disable overlay actor |
| `--help` | | Show help and exit |

## Commands

### Default (no command)

Starts popup-ai with the admin UI.

```bash
popup-ai
```

Opens browser to `http://127.0.0.1:8080`.

### status

Shows pipeline status.

```bash
popup-ai status
```

Displays whether Ray is initialized and pipeline state.

## Options Detail

### --version, -v

Print version information and exit.

```bash
$ popup-ai --version
popup-ai version 0.1.0
```

### --headless

Run without the NiceGUI admin UI. Useful for:

- Automation and scripting
- Running on headless servers
- CI/CD pipelines

```bash
popup-ai --headless
```

The pipeline starts immediately. Press Ctrl+C to stop.

### --config, -c

Specify a configuration file path.

```bash
popup-ai --config /path/to/config.toml
```

!!! note "Config File Support"
    Config file loading is planned but not yet implemented. Use environment variables for configuration.

### --actors

Enable only specific actors. Accepts a comma-separated list.

Valid actors: `audio`, `transcriber`, `annotator`, `overlay`

```bash
# Only audio + transcription
popup-ai --actors audio,transcriber

# Only transcription + annotation (no audio or overlay)
popup-ai --actors transcriber,annotator
```

When `--actors` is specified, all actors not in the list are disabled.

### --no-audio, --no-transcriber, --no-annotator, --no-overlay

Disable specific actors while keeping others enabled.

```bash
# Full pipeline except OBS overlay
popup-ai --no-overlay

# Audio + transcription only
popup-ai --no-annotator --no-overlay
```

These flags are mutually exclusive with `--actors`. If both are specified, `--actors` takes precedence.

## Environment Variables

All configuration can be set via environment variables with the `POPUP_` prefix.

### Pipeline Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POPUP_AUDIO_ENABLED` | `true` | Enable audio ingest actor |
| `POPUP_TRANSCRIBER_ENABLED` | `true` | Enable transcriber actor |
| `POPUP_ANNOTATOR_ENABLED` | `true` | Enable annotator actor |
| `POPUP_OVERLAY_ENABLED` | `true` | Enable overlay actor |
| `POPUP_HEADLESS` | `false` | Run without UI |
| `POPUP_LOG_LEVEL` | `INFO` | Logging level |

### Audio Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POPUP_AUDIO_SRT_PORT` | `9998` | SRT listener port |
| `POPUP_AUDIO_SRT_LATENCY_MS` | `200` | SRT latency in ms |
| `POPUP_AUDIO_SAMPLE_RATE` | `16000` | Audio sample rate |
| `POPUP_AUDIO_CHANNELS` | `1` | Audio channels |
| `POPUP_AUDIO_CHUNK_DURATION_MS` | `100` | Chunk duration |

### Transcriber Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POPUP_TRANSCRIBER_MODEL` | `mlx-community/whisper-base-mlx` | Whisper model |
| `POPUP_TRANSCRIBER_CHUNK_LENGTH_S` | `5.0` | Chunk length |
| `POPUP_TRANSCRIBER_OVERLAP_S` | `0.5` | Chunk overlap |
| `POPUP_TRANSCRIBER_LANGUAGE` | (auto) | Language code |

### Annotator Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POPUP_ANNOTATOR_PROVIDER` | `openai` | LLM provider |
| `POPUP_ANNOTATOR_MODEL` | `gpt-4o-mini` | LLM model |
| `POPUP_ANNOTATOR_CACHE_ENABLED` | `true` | Enable SQLite cache |
| `POPUP_ANNOTATOR_CACHE_PATH` | `~/.popup-ai/cache.db` | Cache path |

### Overlay Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POPUP_OVERLAY_OBS_HOST` | `localhost` | OBS WebSocket host |
| `POPUP_OVERLAY_OBS_PORT` | `4455` | OBS WebSocket port |
| `POPUP_OVERLAY_OBS_PASSWORD` | (none) | OBS WebSocket password |
| `POPUP_OVERLAY_SCENE_NAME` | `popup-ai-overlay` | OBS scene name |
| `POPUP_OVERLAY_HOLD_DURATION_MS` | `5000` | Annotation display time |

Slots are discovered from OBS, not configured. Create text sources named `popup-ai-slot-1`, `popup-ai-slot-2`, etc. in your OBS scene.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (missing dependency, configuration error) |

## Examples

### Start with UI (default)

```bash
popup-ai
```

### Start headless

```bash
popup-ai --headless
```

### Start with custom port

```bash
POPUP_AUDIO_SRT_PORT=9999 popup-ai
```

### Start with specific model

```bash
POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-small-mlx" popup-ai
```

### Disable overlay for testing

```bash
# Using CLI flag (recommended)
popup-ai --no-overlay

# Using environment variable
POPUP_OVERLAY_ENABLED=false popup-ai
```

### Run only audio + transcription

```bash
popup-ai --actors audio,transcriber
```

### Run headless without overlay

```bash
popup-ai --headless --no-overlay
```

## See Also

- [Configuration Reference](configuration.md) - Detailed configuration options
- [Quickstart Tutorial](../tutorials/quickstart.md) - Getting started

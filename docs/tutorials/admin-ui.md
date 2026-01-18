# Understanding the Admin UI

A guided tour of the popup-ai admin interface. Learn what each panel shows and how to use the controls effectively.

## Launching the UI

Start popup-ai to open the admin UI:

```bash
uv run popup-ai
```

The UI opens at `http://127.0.0.1:8080`.

!!! tip "Headless Mode"
    If you don't need the UI, run `popup-ai --headless` for automation scenarios.

## UI Overview

The admin UI has four main areas:

1. **Header** - Pipeline controls
2. **Actor Status** - Health monitoring
3. **Live Transcript** - Real-time transcription output
4. **Recent Annotations** - Generated annotations
5. **Settings** - Configuration panel

## Header and Pipeline Controls

The header contains the main pipeline controls:

| Button | Action |
|--------|--------|
| **Start Pipeline** | Spawns all actors and begins processing |
| **Stop Pipeline** | Gracefully stops all actors |

When the pipeline is stopped, only "Start Pipeline" is enabled. Once running, only "Stop Pipeline" is enabled.

## Actor Status Panel

The left panel shows the status of each pipeline actor:

### Status Indicators

| Icon | Meaning |
|------|---------|
| :material-check-circle:{ style="color: green" } | Running normally |
| :material-clock:{ style="color: orange" } | Starting up |
| :material-alert-circle:{ style="color: red" } | Error state |
| :material-circle:{ style="color: grey" } | Stopped |

### Actors

- **Audio Ingest** - Captures audio from SRT stream via ffmpeg
- **Transcriber** - Converts audio to text using mlx-whisper
- **Annotator** - Extracts terms and generates explanations via LLM
- **Overlay** - Sends annotations to OBS for display

### Stats

Each actor card shows relevant statistics:

- **Audio Ingest**: chunks_sent, bytes_processed
- **Transcriber**: transcripts_sent, audio_seconds_processed
- **Annotator**: annotations_sent, cache_hits, llm_calls
- **Overlay**: annotations_displayed, obs_connected

## Live Transcript Panel

The center panel shows real-time transcription output:

```
[12:34:56] Hello everyone, welcome to the stream
[12:34:59] Today we're going to talk about machine learning
[12:35:03] Specifically, we'll cover neural networks
```

The transcript updates as audio is processed. This is useful for:

- Verifying transcription is working
- Checking transcription quality
- Debugging audio issues

## Recent Annotations Panel

The right panel shows generated annotations:

Each annotation card shows:

- **Term** - The extracted key term (bold)
- **Explanation** - Brief educational explanation

Annotations appear here before being sent to OBS. The panel shows the 10 most recent annotations.

## Settings Panel

Click the **Settings** expansion panel to configure the pipeline.

### Audio Ingest Settings

| Setting | Description | Default |
|---------|-------------|---------|
| SRT Port | Port for SRT listener | 9998 |
| Latency (ms) | SRT buffer latency | 200 |

### Transcriber Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Model | Whisper model to use | mlx-community/whisper-base-mlx |

### Annotator Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Provider | LLM provider | openai |
| Model | LLM model | gpt-4o-mini |

### OBS Overlay Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Host | OBS WebSocket host | localhost |
| Port | OBS WebSocket port | 4455 |

!!! warning "Settings Changes"
    Changes to settings take effect on the next pipeline start. You may need to stop and restart the pipeline for changes to apply.

## Common Workflows

### Starting Fresh

1. Open the admin UI
2. Verify all actors show grey (stopped)
3. Configure settings if needed
4. Click "Start Pipeline"
5. Wait for all actors to turn green

### Monitoring a Stream

1. Start the pipeline
2. Watch the Live Transcript for real-time text
3. Check Recent Annotations for extracted terms
4. Monitor actor stats for throughput

### Troubleshooting

1. Check actor status for red indicators
2. Hover over error indicators for details
3. Check terminal output for full error messages
4. Stop and restart individual problem actors (future feature)

## What's Next?

- [Configure OBS for SRT](../how-to/configure-obs.md) - Set up OBS properly
- [Tune Transcription](../how-to/tune-transcription.md) - Improve accuracy
- [Configuration Reference](../reference/configuration.md) - All settings

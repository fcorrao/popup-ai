# Configuration Reference

Complete configuration reference for popup-ai.

## Overview

popup-ai uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) for configuration. Settings are loaded from:

1. Default values (in code)
2. Environment variables (with `POPUP_` prefix)
3. Config file (planned, not yet implemented)

## Configuration Sections

### PipelineConfig

Controls which actors are enabled and global settings.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `audio_enabled` | `POPUP_AUDIO_ENABLED` | bool | `true` | Enable audio ingest actor |
| `transcriber_enabled` | `POPUP_TRANSCRIBER_ENABLED` | bool | `true` | Enable transcriber actor |
| `annotator_enabled` | `POPUP_ANNOTATOR_ENABLED` | bool | `true` | Enable annotator actor |
| `overlay_enabled` | `POPUP_OVERLAY_ENABLED` | bool | `true` | Enable overlay actor |
| `headless` | `POPUP_HEADLESS` | bool | `false` | Run without admin UI |
| `log_level` | `POPUP_LOG_LEVEL` | str | `INFO` | Logging level |

### AudioIngestConfig

Settings for the SRT audio capture actor.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `srt_port` | `POPUP_AUDIO_SRT_PORT` | int | `9998` | Port for SRT listener |
| `srt_latency_ms` | `POPUP_AUDIO_SRT_LATENCY_MS` | int | `200` | SRT buffer latency in milliseconds |
| `sample_rate` | `POPUP_AUDIO_SAMPLE_RATE` | int | `16000` | Audio sample rate in Hz |
| `channels` | `POPUP_AUDIO_CHANNELS` | int | `1` | Number of audio channels |
| `chunk_duration_ms` | `POPUP_AUDIO_CHUNK_DURATION_MS` | int | `100` | Duration of each audio chunk in ms |

#### Notes

- `srt_port`: Must not conflict with other services. Default 9998 is typically available.
- `srt_latency_ms`: Higher values provide more buffer against network jitter but add delay. Range: 120-500ms.
- `sample_rate`: 16000 Hz is optimal for Whisper. Other values may affect quality.
- `channels`: Mono (1) is recommended. Stereo will be downmixed.

### TranscriberConfig

Settings for the mlx-whisper transcription actor.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `model` | `POPUP_TRANSCRIBER_MODEL` | str | `mlx-community/whisper-base-mlx` | Whisper model identifier |
| `chunk_length_s` | `POPUP_TRANSCRIBER_CHUNK_LENGTH_S` | float | `5.0` | Audio chunk length for processing |
| `overlap_s` | `POPUP_TRANSCRIBER_OVERLAP_S` | float | `0.5` | Overlap between consecutive chunks |
| `language` | `POPUP_TRANSCRIBER_LANGUAGE` | str\|None | `None` | Language code (e.g., "en") or None for auto |

#### Model Options

| Model | HuggingFace ID | Size | Accuracy |
|-------|----------------|------|----------|
| Tiny | `mlx-community/whisper-tiny-mlx` | 39M | Basic |
| Base | `mlx-community/whisper-base-mlx` | 74M | Good |
| Small | `mlx-community/whisper-small-mlx` | 244M | Better |
| Medium | `mlx-community/whisper-medium-mlx` | 769M | Great |
| Large | `mlx-community/whisper-large-v3-mlx` | 1.5B | Best |

### AnnotatorConfig

Settings for the LLM annotation actor with multi-provider support.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `provider` | `POPUP_ANNOTATOR_PROVIDER` | str | `openai` | LLM provider: `openai`, `anthropic`, `cerebras`, `openai_compatible` |
| `model` | `POPUP_ANNOTATOR_MODEL` | str | `gpt-4o-mini` | LLM model name |
| `base_url` | `POPUP_ANNOTATOR_BASE_URL` | str\|None | `None` | Custom API URL for `openai_compatible` provider |
| `api_key_env_var` | `POPUP_ANNOTATOR_API_KEY_ENV_VAR` | str\|None | `None` | Custom env var for API key (for `openai_compatible`) |
| `max_tokens` | `POPUP_ANNOTATOR_MAX_TOKENS` | int | `500` | Maximum response tokens |
| `cache_enabled` | `POPUP_ANNOTATOR_CACHE_ENABLED` | bool | `true` | Enable SQLite result cache |
| `cache_path` | `POPUP_ANNOTATOR_CACHE_PATH` | Path | `~/.popup-ai/cache.db` | Path to cache database |
| `prompt_template` | `POPUP_ANNOTATOR_PROMPT_TEMPLATE` | str | (see below) | Prompt template |

#### Default Prompt Template

```
Extract key terms and provide brief explanations for: {text}
```

#### Supported Providers

The annotator uses pydantic-ai for multi-provider LLM support. Configure provider credentials via environment variables:

| Provider | Credential Variable | Example Models |
|----------|---------------------|----------------|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini`, `gpt-4o`, `gpt-4-turbo` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022` |
| `cerebras` | `CEREBRAS_API_KEY` | `llama3.1-8b`, `llama3.1-70b` |
| `openai_compatible` | `LOCAL_LLM_API_KEY` (optional) | `llama3.2`, `mistral`, `qwen2.5` |

#### Local Model Setup (Ollama Example)

To use local models via Ollama:

1. Install and start Ollama: `ollama serve`
2. Pull a model: `ollama pull llama3.2`
3. Configure popup-ai:

```bash
export POPUP_ANNOTATOR_PROVIDER="openai_compatible"
export POPUP_ANNOTATOR_MODEL="llama3.2"
export POPUP_ANNOTATOR_BASE_URL="http://localhost:11434/v1"
```

Or configure via the Admin UI under Annotator → Settings.

#### Runtime Reconfiguration

The annotator supports runtime reconfiguration without requiring a full restart. Use the Admin UI's Annotator Settings panel to:

1. Change provider and model
2. Configure local model endpoints
3. Edit the prompt template
4. Apply changes instantly

### OverlayConfig

Settings for the OBS WebSocket overlay actor.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `obs_host` | `POPUP_OVERLAY_OBS_HOST` | str | `localhost` | OBS WebSocket host |
| `obs_port` | `POPUP_OVERLAY_OBS_PORT` | int | `4455` | OBS WebSocket port |
| `obs_password` | `POPUP_OVERLAY_OBS_PASSWORD` | str\|None | `None` | OBS WebSocket password |
| `scene_name` | `POPUP_OVERLAY_SCENE_NAME` | str | `popup-ai-overlay` | OBS scene for overlays |
| `hold_duration_ms` | `POPUP_OVERLAY_HOLD_DURATION_MS` | int | `5000` | How long to display each annotation |

#### Slot Discovery

The overlay actor discovers text sources in OBS named `popup-ai-slot-1`, `popup-ai-slot-2`, etc. The number of available slots is determined by how many sources you create, not by configuration.

- **Smart selection**: Always uses the first available slot (prefers reusing slot 1)
- **Queuing**: When all slots are busy, annotations queue until a slot clears

#### OBS WebSocket Setup

1. In OBS, go to **Tools** → **WebSocket Server Settings**
2. Enable the WebSocket server
3. Note the port (default: 4455)
4. Set a password if desired
5. Configure popup-ai with matching settings
6. Create text sources named `popup-ai-slot-1`, `popup-ai-slot-2`, etc. in your overlay scene

### LogfireConfig

Settings for [Logfire](https://logfire.pydantic.dev/) observability integration.

| Setting | Env Var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `enabled` | `POPUP_LOGFIRE_ENABLED` | bool | `true` | Enable Logfire observability |
| `sample_rate` | `POPUP_LOGFIRE_SAMPLE_RATE` | float | `0.5` | Trace sampling rate (0.0-1.0) |
| `environment` | `POPUP_LOGFIRE_ENVIRONMENT` | str | `development` | Environment name for traces |
| `dashboard_url` | `POPUP_LOGFIRE_DASHBOARD_URL` | str | (see below) | Logfire dashboard URL |

#### Default Dashboard URL

```
https://logfire-us.pydantic.dev/fcorrao/popup-ai
```

#### What Gets Traced

- **pydantic-ai agent runs** - Automatic LLM call tracing via `instrument_pydantic_ai()`
- **Actor lifecycle events** - Start/stop spans for all actors
- **Errors and exceptions** - Always captured regardless of sampling rate
- **Custom metrics** - `popup_ai.llm_calls`, `popup_ai.llm_cache_hits`, `popup_ai.llm_latency_ms`

#### Sampling Strategy

The default 50% head sampling balances observability with quota usage. Logfire automatically escalates errors to 100% capture regardless of sampling rate.

```bash
# Disable observability entirely
export POPUP_LOGFIRE_ENABLED=false

# Full tracing for debugging
export POPUP_LOGFIRE_SAMPLE_RATE=1.0

# Minimal tracing for production
export POPUP_LOGFIRE_SAMPLE_RATE=0.1
```

## Example Configurations

### Development Setup

Minimal configuration for local development:

```bash
export POPUP_OVERLAY_ENABLED=false
export POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-tiny-mlx"
```

### Production Setup

Full pipeline with optimized settings:

```bash
export POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-small-mlx"
export POPUP_TRANSCRIBER_CHUNK_LENGTH_S="5.0"
export POPUP_AUDIO_SRT_LATENCY_MS="200"
export POPUP_OVERLAY_HOLD_DURATION_MS="6000"
export POPUP_LOG_LEVEL="WARNING"
```

### Remote OBS

Connecting to OBS on another machine:

```bash
export POPUP_OVERLAY_OBS_HOST="192.168.1.100"
export POPUP_OVERLAY_OBS_PORT="4455"
export POPUP_OVERLAY_OBS_PASSWORD="your-password"
```

## See Also

- [CLI Reference](cli.md) - Command-line options
- [Tune Transcription](../how-to/tune-transcription.md) - Optimizing transcription
- [Configure OBS](../how-to/configure-obs.md) - OBS setup guide

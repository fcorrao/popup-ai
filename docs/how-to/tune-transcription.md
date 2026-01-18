# Tune Transcription Settings

Optimize transcription quality for your content, language, and hardware.

## Understanding the Settings

The transcriber has four main settings:

| Setting | Description | Trade-off |
|---------|-------------|-----------|
| `model` | Whisper model variant | Accuracy vs. speed |
| `chunk_length_s` | Audio chunk size | Context vs. latency |
| `overlap_s` | Overlap between chunks | Continuity vs. processing |
| `language` | Language code | Speed vs. auto-detection |

## Choosing a Model

popup-ai uses [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) models optimized for Apple Silicon.

### Available Models

| Model | Size | Speed | Accuracy | Memory |
|-------|------|-------|----------|--------|
| `whisper-tiny` | 39M | Fastest | Basic | ~1GB |
| `whisper-base` | 74M | Fast | Good | ~1GB |
| `whisper-small` | 244M | Medium | Better | ~2GB |
| `whisper-medium` | 769M | Slow | Great | ~5GB |
| `whisper-large-v3` | 1.5B | Slowest | Best | ~10GB |

### Setting the Model

=== "Environment Variable"

    ```bash
    export POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-small-mlx"
    ```

=== "Admin UI"

    1. Open Settings panel
    2. Find "Transcriber" section
    3. Enter model name in "Model" field

### Model Recommendations

| Use Case | Recommended Model |
|----------|-------------------|
| Testing/development | `whisper-base` |
| General streaming | `whisper-small` |
| Technical content | `whisper-medium` |
| Maximum accuracy | `whisper-large-v3` |

!!! tip "M3 Ultra Users"
    With 128GB+ RAM, you can comfortably run `whisper-large-v3` for the best accuracy.

## Chunk Length

Chunk length determines how much audio is processed at once.

### Trade-offs

| Shorter chunks (2-3s) | Longer chunks (5-10s) |
|-----------------------|-----------------------|
| Lower latency | Higher latency |
| Less context | More context |
| May cut words | Better sentence detection |
| More processing overhead | More efficient |

### Setting Chunk Length

```bash
export POPUP_TRANSCRIBER_CHUNK_LENGTH_S="5.0"
```

### Recommendations

| Priority | Chunk Length |
|----------|--------------|
| Low latency | 2.0 - 3.0 |
| **Balanced** | **5.0** |
| Maximum accuracy | 8.0 - 10.0 |

## Overlap

Overlap keeps audio from the end of one chunk to include at the start of the next.

### Why Overlap Matters

Without overlap, words at chunk boundaries may be cut:

```
Chunk 1: "Today we're going to talk about machi-"
Chunk 2: "-ne learning and neural networks"
```

With overlap, the boundary is handled smoothly.

### Setting Overlap

```bash
export POPUP_TRANSCRIBER_OVERLAP_S="0.5"
```

### Recommendations

| Chunk Length | Recommended Overlap |
|--------------|---------------------|
| 2.0s | 0.3s |
| 5.0s | 0.5s |
| 10.0s | 1.0s |

Rule of thumb: overlap should be ~10% of chunk length.

## Language Setting

By default, Whisper auto-detects the language. You can specify a language for faster processing.

### Setting Language

```bash
# Auto-detect (default)
export POPUP_TRANSCRIBER_LANGUAGE=""

# Force English
export POPUP_TRANSCRIBER_LANGUAGE="en"

# Force Spanish
export POPUP_TRANSCRIBER_LANGUAGE="es"
```

### When to Set Language

- **Set it** if you always stream in one language
- **Leave auto** if you switch languages or have multilingual content

Setting the language:

- Skips language detection step
- Slightly faster processing
- May improve accuracy for that language

## Tuning Workflow

### Step 1: Start with Defaults

```bash
# Default settings
POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-base-mlx"
POPUP_TRANSCRIBER_CHUNK_LENGTH_S="5.0"
POPUP_TRANSCRIBER_OVERLAP_S="0.5"
POPUP_TRANSCRIBER_LANGUAGE=""
```

### Step 2: Monitor Quality

1. Start popup-ai with defaults
2. Stream some representative content
3. Watch the Live Transcript panel
4. Note any issues:
   - Words cut off at boundaries?
   - Wrong language detected?
   - Missing technical terms?
   - Too slow/delayed?

### Step 3: Adjust One Setting

Change only one setting at a time:

1. **Accuracy issues** → Try larger model
2. **Words cut off** → Increase overlap
3. **Too slow** → Reduce chunk length or use smaller model
4. **Wrong language** → Set explicit language

### Step 4: Test and Iterate

Repeat with your actual streaming content until quality is acceptable.

## Performance Considerations

### Memory Usage

Monitor memory when using larger models:

```bash
# Check memory usage
top -l 1 | grep -E "PhysMem"
```

### CPU Usage

Larger models and shorter chunks increase CPU load. Monitor with:

```bash
# Check Ray worker CPU usage
top -l 1 | grep -E "ray"
```

### Latency Measurement

The admin UI shows stats for latency estimation:

- `audio_seconds_processed` - How much audio has been transcribed
- `buffer_duration_s` - Current audio buffer size

If buffer keeps growing, processing is falling behind.

## All Settings Reference

```bash
# Model selection
POPUP_TRANSCRIBER_MODEL="mlx-community/whisper-small-mlx"

# Chunk settings
POPUP_TRANSCRIBER_CHUNK_LENGTH_S="5.0"
POPUP_TRANSCRIBER_OVERLAP_S="0.5"

# Language (empty for auto-detect)
POPUP_TRANSCRIBER_LANGUAGE="en"
```

## What's Next?

- [Configuration Reference](../reference/configuration.md) - All settings
- [Architecture](../explanation/architecture.md) - How transcription fits in

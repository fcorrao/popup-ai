# Configure OBS for SRT Streaming

This guide walks you through configuring OBS Studio to stream audio to popup-ai via SRT.

## Prerequisites

- OBS Studio 28.0+ (SRT support built-in)
- popup-ai installed and running
- Network access between OBS and popup-ai (localhost or LAN)

## Basic Configuration

### Step 1: Open Stream Settings

1. Open OBS Studio
2. Go to **Settings** → **Stream**

### Step 2: Configure Custom Server

1. Set **Service** to `Custom...`
2. Set **Server** to:

```
srt://localhost:9998?mode=caller&latency=200000
```

3. Leave **Stream Key** empty
4. Click **Apply**

### Understanding the SRT URL

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `srt://` | Protocol | SRT streaming protocol |
| `localhost` | Host | Where popup-ai is running |
| `9998` | Port | popup-ai's SRT listener port |
| `mode=caller` | Mode | OBS initiates the connection |
| `latency=200000` | Latency | 200ms buffer (in microseconds) |

## Audio Configuration

### Step 3: Configure Audio Output

Go to **Settings** → **Output** → **Streaming**:

1. Set **Audio Encoder** to `AAC`
2. Set **Audio Bitrate** to `128` (or higher for better quality)

!!! tip "Audio Quality"
    Higher bitrates improve transcription quality but use more bandwidth. 128kbps is a good balance.

### Step 4: Verify Audio Sources

In the main OBS window, check your **Audio Mixer**:

- Ensure your microphone/audio source is active
- Levels should be visible when speaking
- Avoid clipping (staying in the red)

## Remote Streaming

If OBS and popup-ai are on different machines:

### On the popup-ai machine

Ensure the SRT port is accessible:

```bash
# Check if port is listening
lsof -i :9998
```

### On the OBS machine

Update the SRT URL with the remote IP:

```
srt://192.168.1.100:9998?mode=caller&latency=200000
```

Replace `192.168.1.100` with the popup-ai machine's IP address.

!!! warning "Firewall"
    Make sure port 9998 is open on the popup-ai machine's firewall.

## Latency Tuning

The `latency` parameter affects the trade-off between delay and reliability:

| Latency | Delay | Reliability | Use Case |
|---------|-------|-------------|----------|
| `120000` | 120ms | Lower | Local network, low jitter |
| `200000` | 200ms | Medium | **Recommended default** |
| `500000` | 500ms | Higher | Unstable network |

### Symptoms of Wrong Latency

**Too low:**
- Audio glitches and dropouts
- Disconnections

**Too high:**
- Noticeable delay in transcription
- Annotations appear late

## Testing the Connection

### Step 1: Start popup-ai

```bash
uv run popup-ai
```

Verify the SRT listener is ready by checking the terminal output.

### Step 2: Start OBS Streaming

1. Click **Start Streaming** in OBS
2. Watch the OBS status bar for connection confirmation
3. Check popup-ai admin UI for incoming audio

### Step 3: Verify Audio Flow

In popup-ai admin UI:

- Audio Ingest actor should show green
- `chunks_sent` stat should be increasing
- Live Transcript should show text (once Transcriber processes)

## Troubleshooting

### "Connection refused"

1. Ensure popup-ai is running before OBS starts streaming
2. Check the port number matches (default: 9998)
3. Verify no firewall blocking the port

### OBS shows "Connecting..." indefinitely

1. Check the SRT URL is correct
2. Verify `mode=caller` is set
3. Try increasing latency

### Audio glitches in transcription

1. Increase latency to 300000 or higher
2. Check network stability
3. Reduce OBS output resolution to lower CPU usage

### No audio reaching popup-ai

1. Verify OBS audio mixer shows levels
2. Check OBS is actually streaming (status bar)
3. Restart both OBS and popup-ai

## Advanced: Multiple Audio Tracks

OBS can send multiple audio tracks. By default, popup-ai uses Track 1.

To configure which tracks OBS sends:

1. Go to **Settings** → **Output** → **Streaming**
2. Under **Audio Track**, select the tracks to include

!!! note "Future Feature"
    Multi-track selection in popup-ai is planned for a future release.

## What's Next?

- [Tune Transcription](tune-transcription.md) - Optimize transcription quality
- [Configuration Reference](../reference/configuration.md) - All audio settings

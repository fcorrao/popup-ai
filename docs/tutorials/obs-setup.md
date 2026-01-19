# Setting Up OBS for popup-ai

This tutorial walks you through the complete OBS Studio setup for popup-ai, including:

- WebSocket connection for overlay control
- SRT streaming for audio capture
- Text sources for annotation display

## Prerequisites

- **OBS Studio 28.0 or later** - WebSocket 5.x is built-in
- **popup-ai installed** - See [Quickstart](quickstart.md)

!!! info "OBS Version"
    OBS Studio 28+ includes WebSocket support natively. You do **not** need to install a separate WebSocket plugin. If you have an older obs-websocket plugin installed, [remove it](https://obsproject.com/forum/threads/websocket-plugin-fails-to-load-in-obs-30-0.171302/) to avoid conflicts.

## Part 1: WebSocket Configuration

The WebSocket connection allows popup-ai to control OBS text sources for displaying annotations.

### Step 1: Open WebSocket Settings

1. Open OBS Studio
2. Go to **Tools** → **WebSocket Server Settings**

### Step 2: Enable the Server

1. Check **Enable WebSocket server**
2. Note the **Server Port** (default: `4455`)
3. Check **Enable Authentication** (recommended)
4. Copy or note the generated **Server Password**
5. Click **Apply**

!!! tip "Password"
    OBS generates a secure password automatically. Copy it now - you'll need it for popup-ai configuration.

### Step 3: Verify Connection Status

The dialog should show a green status indicator when the server is running.

### Step 4: Configure popup-ai

Set the WebSocket credentials via environment variables:

```bash
export POPUP_OVERLAY_OBS_HOST=localhost
export POPUP_OVERLAY_OBS_PORT=4455
export POPUP_OVERLAY_OBS_PASSWORD="your-password-here"
```

Or in the admin UI Settings panel under "OBS Overlay".

## Part 2: Scene and Source Setup

popup-ai displays annotations using text sources in OBS. You create these sources manually, giving you full control over positioning and styling.

### How Slot Selection Works

popup-ai uses a smart slot selection system:

- **Always prefers the first available slot** - If slot 1 is free, it uses slot 1
- **Only uses additional slots when needed** - Slot 2 is only used when slot 1 is still displaying
- **Queues annotations when all slots are busy** - Queued annotations display as soon as a slot clears

This means:

- **1 slot**: All annotations display one at a time, queued in order
- **2 slots**: Up to 2 annotations can display simultaneously
- **3-4 slots**: For high-frequency annotation scenarios

!!! tip "Start Simple"
    Most users only need 1-2 slots. Start with one and add more if you find annotations are queuing too often.

### Step 1: Create a Scene

1. In **Scenes**, click **+** to add a new scene
2. Name it `popup-ai-overlay` (or set a different name via `POPUP_OVERLAY_SCENE_NAME`)

### Step 2: Add Text Sources

Create one or more text sources with this exact naming pattern:

1. In **Sources**, click **+** → **Text (GDI+)** (Windows) or **Text (FreeType 2)** (macOS/Linux)
2. Name it exactly: `popup-ai-slot-1`
3. Click **OK**
4. Repeat for additional slots: `popup-ai-slot-2`, `popup-ai-slot-3`, etc.

!!! warning "Naming Required"
    Sources **must** be named exactly `popup-ai-slot-1`, `popup-ai-slot-2`, etc. popup-ai discovers these sources by name pattern matching.

### Step 3: Configure Text Properties

For each text source:

1. **Font**: Choose a readable font (e.g., Arial, 24pt)
2. **Color**: White or contrasting color for your scene
3. **Outline**: Optional, helps visibility
4. Leave **Text** empty (popup-ai will fill it)

### Step 4: Position the Sources

Position your text source(s) where you want annotations to appear:

**Single slot (recommended for most users):**
```
┌─────────────────────────────────────┐
│                                     │
│         Your Stream Content         │
│                                     │
│  [slot-1 - lower third area]        │
│                                     │
└─────────────────────────────────────┘
```

**Multiple slots (for simultaneous display):**
```
┌─────────────────────────────────────┐
│                                     │
│  [slot-1]              [slot-2]     │
│                                     │
│         Your Stream Content         │
│                                     │
└─────────────────────────────────────┘
```

!!! tip "Positioning"
    - Lower-third positioning works well for educational content
    - Corner positions are less intrusive
    - Test visibility against your typical stream content

### Suggested Text Source Settings

| Property | Recommended Value |
|----------|-------------------|
| Font | Arial, Helvetica, or system sans-serif |
| Size | 24-32pt (adjust for your resolution) |
| Color | White (#FFFFFF) |
| Outline | 2px black (improves readability) |
| Background | Semi-transparent black (optional) |
| Word Wrap | Enabled, ~400px width |

## Part 3: SRT Audio Streaming

SRT (Secure Reliable Transport) sends your audio to popup-ai for transcription.

### Step 1: Configure Stream Output

1. Go to **Settings** → **Stream**
2. Set **Service** to `Custom...`
3. Set **Server** to:

```
srt://localhost:9998?mode=caller&latency=200000
```

4. Leave **Stream Key** empty
5. Click **Apply**

### Step 2: Configure Audio Encoding

1. Go to **Settings** → **Output** → **Streaming**
2. Set **Audio Encoder** to `AAC`
3. Set **Audio Bitrate** to `128` Kbps (or higher)

### Step 3: Verify Audio Sources

In the main OBS window:

1. Check your **Audio Mixer** panel
2. Ensure microphone/audio input is active
3. Speak and verify levels move

### SRT URL Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `srt://` | Protocol | SRT streaming |
| `localhost` | Host | popup-ai location |
| `9998` | Port | SRT listener port |
| `mode=caller` | Mode | OBS initiates connection |
| `latency=200000` | Buffer | 200ms (microseconds) |

!!! warning "Port Conflicts"
    If port 9998 is in use, change it in both:
    - OBS SRT URL
    - popup-ai config: `POPUP_AUDIO_SRT_PORT`

## Part 4: Testing the Setup

### Step 1: Start popup-ai

```bash
uv run popup-ai
```

Wait for the admin UI to open at `http://127.0.0.1:8080`.

### Step 2: Check Initial State

- All actors should show grey (stopped)
- No errors in terminal

### Step 3: Start Streaming in OBS

1. Select your `popup-ai-overlay` scene
2. Click **Start Streaming**
3. Watch OBS status bar for connection

### Step 4: Start the Pipeline

1. In popup-ai admin UI, click **Start Pipeline**
2. Watch actors turn green one by one

### Step 5: Verify Each Component

| Component | How to Verify |
|-----------|---------------|
| Audio Ingest | `chunks_sent` increasing |
| Transcriber | Text appearing in Live Transcript |
| Annotator | Annotations in Recent Annotations panel |
| Overlay | Text appearing in OBS sources |

## Complete Setup Checklist

- [ ] OBS Studio 28+ installed
- [ ] WebSocket server enabled (Tools → WebSocket Server Settings)
- [ ] WebSocket password configured in popup-ai
- [ ] Scene `popup-ai-overlay` created
- [ ] At least one text source `popup-ai-slot-1` created (add more if needed)
- [ ] Text source(s) positioned and styled
- [ ] SRT stream configured (Custom service, srt://localhost:9998)
- [ ] Audio encoder set to AAC
- [ ] Audio sources active in mixer
- [ ] popup-ai can connect to OBS (Overlay actor green)
- [ ] Overlay status shows "discovered_slots: 1" (or more)
- [ ] Transcription working (text in Live Transcript)
- [ ] Annotations appearing in OBS

## Troubleshooting

### WebSocket Connection Issues

**"OBS not connected" in popup-ai**

1. Verify WebSocket server is enabled in OBS
2. Check port matches (default 4455)
3. Verify password is correct
4. Check no firewall blocking the port

**Overlay actor shows error**

1. Check OBS is running
2. Verify scene name matches configuration
3. Try restarting both OBS and popup-ai

### SRT Streaming Issues

**"Connection refused" when starting stream**

1. Ensure popup-ai is running **before** OBS starts streaming
2. Verify SRT port (9998) is not in use
3. Check `mode=caller` in SRT URL

**Audio glitches**

1. Increase latency: `latency=300000` or higher
2. Check network stability
3. Reduce OBS encoding load

### Text Source Issues

**Sources not created automatically**

1. Create them manually (see Option B above)
2. Ensure scene name matches exactly

**Text not appearing**

1. Check source is visible (eye icon in Sources)
2. Verify source is in the active scene
3. Check text color contrasts with background

## Network Considerations

### Local Setup (Same Machine)

Use `localhost` for both WebSocket and SRT:

- WebSocket: `localhost:4455`
- SRT: `srt://localhost:9998`

### Remote Setup (Different Machines)

Replace `localhost` with the popup-ai machine's IP:

- WebSocket: `192.168.1.100:4455`
- SRT: `srt://192.168.1.100:9998`

Ensure ports 4455 (WebSocket) and 9998 (SRT) are open on the popup-ai machine.

## What's Next?

- [Tune Transcription](../how-to/tune-transcription.md) - Improve accuracy
- [Understanding the Admin UI](admin-ui.md) - Learn the controls
- [Configuration Reference](../reference/configuration.md) - All settings

## References

- [OBS Remote Control Guide](https://obsproject.com/kb/remote-control-guide)
- [OBS WebSocket GitHub](https://github.com/obsproject/obs-websocket)
- [SRT Protocol](https://www.haivision.com/resources/streaming-video-definitions/srt/)

# popup-ai

Real-time audio transcription and annotation overlay system for live streaming.

popup-ai captures audio from your stream via SRT, transcribes it using Whisper, extracts key terms with an LLM, and displays educational annotations as overlays in OBS.

## Features

- **Real-time transcription** using mlx-whisper on Apple Silicon
- **Automatic term extraction** via pydantic-ai with configurable LLM providers
- **OBS overlay integration** for displaying annotations during streams
- **Admin UI** for monitoring and control
- **Modular architecture** with Ray actors for fault isolation

## Quick Start

```bash
# Install with all extras
uv pip install 'popup-ai[all]'

# Start the application
uv run popup-ai
```

This opens the admin UI at `http://127.0.0.1:8080`. Configure OBS to stream audio via SRT, then click "Start Pipeline" to begin.

## Documentation Structure

This documentation follows the [Diataxis](https://diataxis.fr/) framework:

<div class="grid cards" markdown>

-   :material-school: **Tutorials**

    ---

    Learning-oriented lessons to get you started

    [:octicons-arrow-right-24: Start learning](tutorials/index.md)

-   :material-directions: **How-to Guides**

    ---

    Task-oriented guides for specific goals

    [:octicons-arrow-right-24: Find a guide](how-to/index.md)

-   :material-book-open-variant: **Reference**

    ---

    Technical descriptions of CLI, config, and APIs

    [:octicons-arrow-right-24: Browse reference](reference/index.md)

-   :material-lightbulb: **Explanation**

    ---

    Conceptual discussions of architecture and design

    [:octicons-arrow-right-24: Understand more](explanation/index.md)

</div>

## Requirements

- macOS with Apple Silicon (M1/M2/M3)
- Python 3.13+
- OBS Studio (for overlay display)
- ffmpeg (for SRT audio capture)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Main Process                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   NiceGUI Admin UI                         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              PipelineSupervisor (Ray Actor)                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ AudioIngestActor│→ │TranscriberActor │→ │ AnnotatorActor  │
│   (ffmpeg SRT)  │  │  (mlx-whisper)  │  │  (pydantic-ai)  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │  OverlayActor   │
                                          │ (obsws-python)  │
                                          └─────────────────┘
```

See [Architecture](explanation/architecture.md) for details.

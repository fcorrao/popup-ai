# Proposal: Single-Stream Video OCR Ingest

Status: proposal (no implementation in this doc)

## Summary
Add video OCR to the existing audio pipeline without introducing a second SRT stream. A single ffmpeg process will decode the SRT input once and emit:
- PCM audio chunks (existing path)
- sampled video frames for OCR (new path)

OCR text is merged with audio transcripts and sent to the annotator to enrich overlays.

## Goals
- Keep **one** SRT input (no second stream).
- Reuse the existing audio → transcriber → annotator → overlay flow.
- Add OCR text as an additional input to annotation.
- Target **1 fps** OCR by default; drop frames if OCR is slower.

## Non-goals
- No changes to OBS output format or overlay rendering.
- No new stream configuration in OBS.

## Current State (context)
Audio ingest uses ffmpeg and explicitly drops video:
- `-map 0:a:0` and `-vn` in `AudioIngestActor`
- Output is PCM `s16le` chunks into the audio queue

## Proposed Architecture

### High-level flow
```
SRT (audio+video)
  → ffmpeg (decode once)
    → audio: PCM chunks → audio_queue → Transcriber → Annotator → Overlay
    → video: frames → video_queue → OCR → text_queue → Annotator → Overlay
```

### Actor changes
1. **New `AvIngestActor`** (or extend existing `AudioIngestActor`)
   - Spawns ffmpeg with **two outputs**: audio + video frames.
   - Pushes audio chunks to the existing `audio` queue.
   - Pushes `VideoFrame` messages to a new `video` queue.

2. **New `OcrActor`**
   - Consumes `VideoFrame` from `video` queue.
   - Runs OCR and emits `TextSignal` to a new `text` queue.
   - Applies dedupe / filtering to avoid LLM spam.

3. **Annotator input**
   - Change `AnnotatorActor` to consume `TextSignal` (from a `text` queue),
     or add a small `TextRouterActor` that merges transcripts + OCR into one stream.

### Message types (new)
```python
class VideoFrame(BaseModel):
    data: bytes
    width: int
    height: int
    pixel_format: str  # "gray" or "rgb24"
    timestamp_ms: int

class TextSignal(BaseModel):
    text: str
    source: Literal["audio", "ocr"]
    timestamp_ms: int
    confidence: float | None = None
```

## ffmpeg Design (single SRT input, dual outputs)

### Conceptual command
```bash
ffmpeg -hide_banner -loglevel info -threads 4 -fflags +discardcorrupt \
  -i "srt://0.0.0.0:{port}?mode=listener&latency={latency}&rcvbuf={rcvbuf}" \
  -map 0:a:0 -acodec pcm_s16le -ar 16000 -ac 1 -f s16le pipe:1 \
  -map 0:v:0 -vf fps=1,scale=1280:-2,format=gray \
  -f rawvideo -pix_fmt gray pipe:3
```

### Output strategy
- **Audio** stays on stdout (`pipe:1`), same as today.
- **Video** goes to an extra file descriptor (`pipe:3`) using `os.pipe()` +
  `subprocess.Popen(..., pass_fds=[fd])`.
- For Windows, consider a named pipe or `image2pipe` (MJPEG) as fallback.

### Frame parsing
- Use fixed dimensions (`scale=1280:-2`) to compute frame size.
- For `gray`, frame_size = `width * height`.
- If OCR is slower than real-time, drop frames instead of queueing indefinitely.

## OCR Sampling & Throttling
Default target: **1 fps**.

Suggested policy:
- `target_fps = 1.0`
- If OCR time > `1/target_fps`, drop incoming frames and only keep the latest.
- Optional adaptive mode: lower fps when OCR load is high.

## OCR Engine Options (Python)
Prefer native or accelerated backends:

1. **Tesseract (C++ engine) + pytesseract wrapper**
   - Pros: native C++ engine, easy to install, small Python surface.
   - Cons: accuracy can be lower on stylized UI text.

2. **RapidOCR (ONNXRuntime by default)**
   - Pros: ONNXRuntime inference; lighter footprint than full PaddleOCR.
   - Cons: model management is an extra step vs Tesseract.

3. **PaddleOCR (PaddlePaddle backend)**
   - Pros: strong accuracy, production-grade toolkit.
   - Cons: heavier dependency tree and model downloads.

4. **EasyOCR (PyTorch backend)**
   - Pros: broad language support.
   - Cons: heavy dependency footprint; slower on CPU.

**Recommendation for a first pass:** start with **Tesseract** for integration
speed, then evaluate **RapidOCR (ONNXRuntime)** if accuracy/latency needs
improve.

## Config Additions (proposal)
```python
class VideoIngestConfig(BaseSettings):
    srt_port: int
    fps: float = 1.0
    scale_width: int = 1280
    pixel_format: str = "gray"
    ffmpeg_threads: int = 4

class OcrConfig(BaseSettings):
    engine: str = "tesseract"  # or "rapidocr", "paddleocr", "easyocr"
    languages: list[str] = ["en"]
    min_confidence: float = 0.4
    min_chars: int = 3
    dedupe_window_s: int = 60
```

## UI / Observability
- Add a **Video/OCR** stage in the UI for:
  - frames received
  - OCR texts emitted
  - OCR latency (ms)
- Add events: `frame_received`, `ocr_text_emitted`, `ocr_dropped_frame`.

## Risks & Mitigations
- **CPU pressure:** OCR can be expensive → cap fps, drop frames, throttle.
- **Frame parsing bugs:** enforce fixed width/height and pixel format.
- **Annotation spam:** dedupe OCR text and enforce min length/entropy filters.

## Open Questions
- Target OCR languages (`["en"]` vs multi-language).
- Preferred primary engine (Tesseract vs RapidOCR).
- Windows compatibility requirements (if any).


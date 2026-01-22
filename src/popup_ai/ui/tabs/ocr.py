"""OCR tab with video frame input and text output."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from nicegui import events, ui

from popup_ai.config import Settings
from popup_ai.messages import UIEvent
from popup_ai.ui.components.data_viewer import DataViewer
from popup_ai.ui.components.status_card import StatusCard
from popup_ai.ui.state import UIState


def format_ocr_input_event(event: UIEvent) -> str:
    """Format OCR input events (video frames)."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "frame_processed":
        latency = event.data.get("latency_ms", 0)
        text_len = event.data.get("text_length", 0)
        conf = event.data.get("confidence", 0)
        return f"[{timestamp}] Frame: {latency:.0f}ms, {text_len} chars, conf={conf:.2f}"
    return f"[{timestamp}] {event.event_type}"


def format_ocr_output_event(event: UIEvent) -> str:
    """Format OCR output events (transcripts)."""
    timestamp = datetime.fromtimestamp(event.timestamp_ms / 1000).strftime("%H:%M:%S")
    if event.event_type == "transcript_emitted":
        text = event.data.get("text", "")[:60]
        conf = event.data.get("confidence", 0)
        return f"[{timestamp}] ({conf:.2f}) {text}..."
    return f"[{timestamp}] {event.event_type}"


class OcrTab:
    """OCR tab displaying video frame input and OCR text output."""

    def __init__(
        self,
        state: UIState,
        settings: Settings,
        supervisor_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._state = state
        self._settings = settings
        self._supervisor_getter = supervisor_getter
        self._status_card: StatusCard | None = None
        self._input_viewer: DataViewer | None = None
        self._output_viewer: DataViewer | None = None
        self._test_result: ui.label | None = None
        self._test_image_preview: ui.image | None = None

    def build(self) -> ui.column:
        """Build and return the OCR tab content."""
        stage = self._state.get_stage("ocr")

        container = ui.column().classes("w-full gap-4")
        with container:
            # Status card
            self._status_card = StatusCard("ocr", stage.status if stage else None)
            self._status_card.build()

            # Settings (collapsed)
            with ui.expansion("OCR Settings", icon="settings").classes("w-full"):
                with ui.column().classes("gap-2 p-2"):
                    ui.select(
                        label="OCR Engine",
                        options=["tesseract", "rapidocr"],
                        value=self._settings.ocr.engine,
                        on_change=lambda e: setattr(
                            self._settings.ocr, "engine", e.value
                        ),
                    ).classes("w-48")

                    ui.number(
                        "Min Confidence",
                        value=self._settings.ocr.min_confidence,
                        min=0.0,
                        max=1.0,
                        step=0.1,
                        on_change=lambda e: setattr(
                            self._settings.ocr, "min_confidence", float(e.value)
                        ),
                    ).classes("w-32")

                    ui.number(
                        "Min Characters",
                        value=self._settings.ocr.min_chars,
                        min=1,
                        max=50,
                        step=1,
                        on_change=lambda e: setattr(
                            self._settings.ocr, "min_chars", int(e.value)
                        ),
                    ).classes("w-32")

                    ui.number(
                        "Dedupe Window (s)",
                        value=self._settings.ocr.dedupe_window_s,
                        min=1,
                        max=300,
                        step=10,
                        on_change=lambda e: setattr(
                            self._settings.ocr, "dedupe_window_s", int(e.value)
                        ),
                    ).classes("w-32")

                    ui.label("Video Settings").classes("font-medium mt-2")
                    ui.checkbox(
                        "Video Extraction Enabled",
                        value=self._settings.video.enabled,
                        on_change=lambda e: setattr(
                            self._settings.video, "enabled", e.value
                        ),
                    )

                    ui.number(
                        "Target FPS",
                        value=self._settings.video.fps,
                        min=0.1,
                        max=10.0,
                        step=0.5,
                        on_change=lambda e: setattr(
                            self._settings.video, "fps", float(e.value)
                        ),
                    ).classes("w-32")

                    ui.number(
                        "Scale Width",
                        value=self._settings.video.scale_width,
                        min=320,
                        max=1920,
                        step=64,
                        on_change=lambda e: setattr(
                            self._settings.video, "scale_width", int(e.value)
                        ),
                    ).classes("w-32")

            # Test Input panel (collapsed)
            with ui.expansion("Test OCR", icon="science").classes("w-full"):
                with ui.column().classes("gap-2 p-2 w-full"):
                    ui.label("Upload an image to test OCR independently").classes(
                        "text-caption text-grey"
                    )

                    with ui.row().classes("items-start gap-4 w-full"):
                        with ui.column().classes("gap-2"):
                            ui.upload(
                                label="Select Image",
                                auto_upload=True,
                                on_upload=self._handle_image_upload,
                            ).props('accept=".png,.jpg,.jpeg,.bmp,.webp"').classes(
                                "max-w-xs"
                            )

                        # Preview area
                        with ui.card().classes("w-64 h-48"):
                            ui.label("Preview").classes("text-caption")
                            self._test_image_preview = ui.image().classes(
                                "w-full h-36 object-contain"
                            )
                            self._test_image_preview.set_visibility(False)

                    # Results area
                    with ui.card().classes("w-full"):
                        ui.label("OCR Result").classes("font-medium")
                        self._test_result = ui.label("").classes(
                            "text-caption whitespace-pre-wrap"
                        )

            # Input/Output viewers side by side
            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("w-1/2"):
                    self._input_viewer = DataViewer(
                        "Input: Video Frames",
                        buffer=stage.input_buffer if stage else None,
                        format_fn=format_ocr_input_event,
                    )
                    self._input_viewer.build()

                with ui.column().classes("w-1/2"):
                    self._output_viewer = DataViewer(
                        "Output: OCR Text",
                        buffer=stage.output_buffer if stage else None,
                        format_fn=format_ocr_output_event,
                    )
                    self._output_viewer.build()

            # Stats panel
            with ui.card().classes("w-full"):
                ui.label("OCR Statistics").classes("font-medium")
                with ui.row().classes("gap-4 text-caption"):
                    self._frames_label = ui.label("Frames: 0")
                    self._texts_label = ui.label("Texts Emitted: 0")
                    self._deduped_label = ui.label("Deduplicated: 0")
                    self._latency_label = ui.label("Latency: -")

        return container

    async def _handle_image_upload(self, e: events.UploadEventArguments) -> None:
        """Handle image upload for OCR testing."""
        if not self._supervisor_getter:
            ui.notify("Pipeline not initialized", type="warning")
            return

        supervisor = self._supervisor_getter()
        if not supervisor:
            ui.notify("Pipeline not running", type="warning")
            return

        try:
            import base64
            import io

            from PIL import Image

            # Read image
            filename = e.file.name
            file_bytes = await e.file.read()

            # Load and convert image
            img = Image.open(io.BytesIO(file_bytes))

            # Show preview
            if self._test_image_preview:
                # Convert to base64 for display
                img_b64 = base64.b64encode(file_bytes).decode()
                ext = filename.rsplit(".", 1)[-1].lower()
                mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
                    ext, "image/png"
                )
                self._test_image_preview.set_source(f"data:{mime};base64,{img_b64}")
                self._test_image_preview.set_visibility(True)

            # Convert to RGB if necessary
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size

            # Run OCR locally for testing (simpler than going through pipeline)
            result = await self._run_local_ocr(img)

            # Display result
            if self._test_result:
                text = result.get("text", "")
                conf = result.get("confidence", 0)
                latency = result.get("latency_ms", 0)
                error = result.get("error", "")
                note = result.get("note", "")

                if error:
                    self._test_result.set_text(f"Error: {error}")
                else:
                    status = note if note else ("(no text detected)" if not text else "")
                    self._test_result.set_text(
                        f"Engine: {self._settings.ocr.engine}\n"
                        f"Confidence: {conf:.2f}\n"
                        f"Latency: {latency:.0f}ms\n"
                        f"Text:\n{text if text else status}"
                    )

            ui.notify(
                f"OCR complete: {len(result.get('text', ''))} chars detected",
                type="positive",
            )

        except Exception as ex:
            ui.notify(f"OCR failed: {ex}", type="negative")
            if self._test_result:
                self._test_result.set_text(f"Error: {ex}")

    async def _run_local_ocr(self, img) -> dict:
        """Run OCR locally using configured engine."""
        import asyncio
        import time

        import numpy as np

        engine = self._settings.ocr.engine.lower()
        start = time.time()

        try:
            if engine == "rapidocr":
                from rapidocr_onnxruntime import RapidOCR

                ocr = RapidOCR()
                img_array = np.array(img)

                result, _ = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ocr(img_array)
                )
                latency = (time.time() - start) * 1000

                if not result:
                    return {
                        "text": "",
                        "confidence": 0,
                        "latency_ms": latency,
                        "note": "No text detected by RapidOCR",
                    }

                # RapidOCR returns list of (bbox, text, confidence)
                texts = []
                confidences = []
                for item in result:
                    text = item[1].strip()
                    conf = item[2]
                    if text:
                        texts.append(text)
                        confidences.append(conf)

                return {
                    "text": " ".join(texts),
                    "confidence": sum(confidences) / len(confidences) if confidences else 0,
                    "latency_ms": latency,
                }

            else:  # tesseract
                import pytesseract

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: pytesseract.image_to_data(
                        img, output_type=pytesseract.Output.DICT
                    ),
                )
                latency = (time.time() - start) * 1000

                texts = []
                confidences = []
                for i, conf in enumerate(result["conf"]):
                    text = result["text"][i].strip()
                    if text and conf > 0:
                        texts.append(text)
                        confidences.append(conf / 100.0)

                return {
                    "text": " ".join(texts),
                    "confidence": sum(confidences) / len(confidences) if confidences else 0,
                    "latency_ms": latency,
                }

        except Exception as e:
            latency = (time.time() - start) * 1000
            return {
                "text": "",
                "confidence": 0,
                "latency_ms": latency,
                "error": f"{engine}: {e}",
            }

    def handle_event(self, event: UIEvent) -> None:
        """Handle a UI event for this tab."""
        if event.event_type == "frame_processed" and self._input_viewer:
            self._input_viewer.add_event(event)
        elif event.event_type == "transcript_emitted" and self._output_viewer:
            self._output_viewer.add_event(event)

    def update(self) -> None:
        """Update the tab display."""
        stage = self._state.get_stage("ocr")
        if stage and self._status_card:
            self._status_card.update(stage.status)
            # Update stats
            if stage.status and stage.status.stats:
                stats = stage.status.stats
                if hasattr(self, "_frames_label"):
                    self._frames_label.set_text(
                        f"Frames: {stats.get('frames_processed', 0)}"
                    )
                if hasattr(self, "_texts_label"):
                    self._texts_label.set_text(
                        f"Texts Emitted: {stats.get('texts_emitted', 0)}"
                    )
                if hasattr(self, "_deduped_label"):
                    self._deduped_label.set_text(
                        f"Deduplicated: {stats.get('texts_deduplicated', 0)}"
                    )
                if hasattr(self, "_latency_label"):
                    latency = stats.get("last_latency_ms", 0)
                    self._latency_label.set_text(
                        f"Latency: {latency:.0f}ms" if latency > 0 else "Latency: -"
                    )

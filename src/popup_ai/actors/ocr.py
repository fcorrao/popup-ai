"""OCR actor for extracting text from video frames."""

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import OcrConfig
from popup_ai.messages import ActorStatus, Transcript, TranscriptSegment, UIEvent, VideoFrame
from popup_ai.observability import ensure_logfire_configured

logger = logging.getLogger(__name__)


def _similarity_ratio(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings (0.0 to 1.0)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Simple Jaccard similarity on words
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


@ray.remote
class OcrActor:
    """Actor that extracts text from video frames using OCR.

    Consumes VideoFrame messages from input_queue, runs OCR,
    and emits Transcript messages to output_queue (same as transcriber).
    """

    def __init__(
        self,
        config: OcrConfig,
        input_queue: Queue,
        output_queue: Queue,
        ui_queue: Queue | None = None,
    ) -> None:
        self.config = config
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.ui_queue = ui_queue

        self._state = "stopped"
        self._error: str | None = None
        self._start_time: float | None = None
        self._running = False
        self._process_task: asyncio.Task | None = None

        # Stats
        self._frames_processed = 0
        self._texts_emitted = 0
        self._texts_deduplicated = 0
        self._last_latency_ms = 0.0

        # Dedupe tracking: (timestamp_ms, text)
        self._recent_texts: deque[tuple[int, str]] = deque()

        # Previous frame text for change detection
        self._previous_text: str | None = None

        # OCR engine instance
        self._ocr_engine: Any = None

        self._logger = logging.getLogger("popup_ai.actors.ocr")

        # Configure logfire in __init__ to avoid blocking async event loop
        ensure_logfire_configured()

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "frames_processed": self._frames_processed,
            "texts_emitted": self._texts_emitted,
            "texts_deduplicated": self._texts_deduplicated,
            "engine": self.config.engine,
            "languages": self.config.languages,
        }
        if self._last_latency_ms > 0:
            stats["last_latency_ms"] = self._last_latency_ms
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="ocr",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the OCR processing loop."""
        if self._state == "running":
            return

        with logfire.span("ocr.start"):
            self._logger.warning("Starting OCR actor")
            self._state = "starting"
            self._error = None

            try:
                # Initialize OCR engine
                self._init_ocr_engine()

                self._running = True
                self._start_time = time.time()
                self._process_task = asyncio.create_task(self._process_loop())
                self._state = "running"
                self._publish_ui_event("started", {"engine": self.config.engine})
                logfire.info("ocr started", engine=self.config.engine)
            except Exception as e:
                self._state = "error"
                self._error = str(e)
                logfire.exception("Failed to start OCR actor")
                self._publish_ui_event("error", {"message": str(e)})
                raise

    async def stop(self) -> None:
        """Stop the OCR processing loop."""
        if self._state == "stopped":
            return

        with logfire.span("ocr.stop"):
            self._logger.info("Stopping OCR actor")
            self._running = False

            if self._process_task:
                self._process_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._process_task

            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            logfire.info("ocr stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    def _init_ocr_engine(self) -> None:
        """Initialize the OCR engine based on config."""
        engine = self.config.engine.lower()

        if engine == "tesseract":
            try:
                import pytesseract
                # Verify tesseract is available
                pytesseract.get_tesseract_version()
                self._ocr_engine = pytesseract
                self._logger.warning("Tesseract OCR initialized")
            except Exception as e:
                raise RuntimeError(
                    f"Tesseract not available: {e}. Install with: brew install tesseract"
                ) from e

        elif engine == "rapidocr":
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._ocr_engine = RapidOCR()
                self._logger.warning("RapidOCR initialized")
            except ImportError as e:
                raise RuntimeError(
                    f"RapidOCR not available: {e}. Install with: pip install rapidocr-onnxruntime"
                ) from e

        else:
            raise ValueError(f"Unknown OCR engine: {engine}")

    def _run_ocr(self, frame: VideoFrame) -> tuple[str, float]:
        """Run OCR on a video frame.

        Returns:
            Tuple of (extracted_text, confidence)
        """
        import numpy as np
        from PIL import Image

        # Convert frame bytes to image
        if frame.pixel_format == "gray":
            img_array = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                (frame.height, frame.width)
            )
            img = Image.fromarray(img_array, mode="L")
        else:  # rgb24
            img_array = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                (frame.height, frame.width, 3)
            )
            img = Image.fromarray(img_array, mode="RGB")

        engine = self.config.engine.lower()

        if engine == "tesseract":
            import pytesseract

            # Run tesseract with language config
            lang = "+".join(self.config.languages)
            result = pytesseract.image_to_data(
                img, lang=lang, output_type=pytesseract.Output.DICT
            )

            # Extract text and calculate average confidence
            texts = []
            confidences = []
            for i, conf in enumerate(result["conf"]):
                text = result["text"][i].strip()
                if text and conf > 0:  # conf is -1 for non-text
                    texts.append(text)
                    confidences.append(conf / 100.0)  # Normalize to 0-1

            full_text = " ".join(texts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            return full_text, avg_conf

        elif engine == "rapidocr":
            # RapidOCR returns list of (bbox, text, confidence)
            result, _ = self._ocr_engine(np.array(img))
            if not result:
                return "", 0.0

            texts = []
            confidences = []
            for item in result:
                text = item[1].strip()
                conf = item[2]
                if text:
                    texts.append(text)
                    confidences.append(conf)

            full_text = " ".join(texts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            return full_text, avg_conf

        return "", 0.0

    def _is_duplicate(self, text: str, timestamp_ms: int) -> bool:
        """Check if text is a duplicate of recent OCR results."""
        # Clean up old entries outside dedupe window
        window_start = timestamp_ms - (self.config.dedupe_window_s * 1000)
        while self._recent_texts and self._recent_texts[0][0] < window_start:
            self._recent_texts.popleft()

        # Check for exact or near matches
        for _, recent_text in self._recent_texts:
            if text == recent_text:
                return True
            # Check similarity (threshold 0.9 for near-duplicates)
            if _similarity_ratio(text, recent_text) > 0.9:
                return True

        return False

    def _should_emit(self, text: str, confidence: float) -> bool:
        """Determine if OCR text should be emitted."""
        # Check minimum confidence
        if confidence < self.config.min_confidence:
            return False

        # Check minimum text length
        if len(text.strip()) < self.config.min_chars:
            return False

        # Check if text is similar to previous frame (fuzzy match, not just exact)
        if self._previous_text:
            similarity = _similarity_ratio(text, self._previous_text)
            if similarity > 0.8:  # 80% similar = likely same content
                return False

        return True

    async def _process_loop(self) -> None:
        """Main processing loop for OCR."""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Get frame from queue with timeout (in executor to avoid blocking)
                try:
                    frame = await loop.run_in_executor(
                        None, lambda: self.input_queue.get(block=True, timeout=0.5)
                    )
                except Exception:
                    continue

                if not isinstance(frame, VideoFrame):
                    self._logger.warning(f"Unexpected message type: {type(frame)}")
                    continue

                if self._frames_processed < 5 or self._frames_processed % 10 == 0:
                    self._logger.warning(f"OCR received frame {self._frames_processed + 1}: {frame.width}x{frame.height}")

                # Run OCR
                start_time = time.time()
                text, confidence = await asyncio.get_event_loop().run_in_executor(
                    None, self._run_ocr, frame
                )
                self._last_latency_ms = (time.time() - start_time) * 1000
                self._frames_processed += 1

                if self._frames_processed <= 5 or self._frames_processed % 10 == 0:
                    self._logger.warning(
                        f"OCR processed frame {self._frames_processed}: "
                        f"conf={confidence:.2f}, text_len={len(text)}, latency={self._last_latency_ms:.0f}ms"
                    )

                # Publish frame processed event
                self._publish_ui_event("frame_processed", {
                    "latency_ms": self._last_latency_ms,
                    "text_length": len(text),
                    "confidence": confidence,
                })

                # Check if we should emit
                if not self._should_emit(text, confidence):
                    self._logger.debug(
                        f"Skipping OCR result: conf={confidence:.2f}, len={len(text)}"
                    )
                    continue

                # Check for duplicates
                if self._is_duplicate(text, frame.timestamp_ms):
                    self._texts_deduplicated += 1
                    self._logger.debug(f"Deduplicated OCR text: {text[:50]}...")
                    continue

                # Track this text for future dedupe
                self._recent_texts.append((frame.timestamp_ms, text))
                self._previous_text = text

                # Create and emit transcript
                transcript = Transcript(
                    text=text,
                    segments=[
                        TranscriptSegment(
                            text=text,
                            start_ms=frame.timestamp_ms,
                            end_ms=frame.timestamp_ms,
                            confidence=confidence,
                        )
                    ],
                    is_partial=False,
                    timestamp_ms=frame.timestamp_ms,
                    source="ocr",
                )

                try:
                    self.output_queue.put_nowait(transcript)
                    self._texts_emitted += 1
                    self._publish_ui_event("transcript_emitted", {
                        "text": text[:100],
                        "confidence": confidence,
                        "source": "ocr",
                    })
                    logfire.info(
                        "ocr text emitted",
                        text_length=len(text),
                        confidence=confidence,
                    )
                except Exception:
                    self._logger.debug("Output queue full, dropping OCR result")

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in OCR processing loop")
                await asyncio.sleep(0.1)

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="ocr",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

    async def process_image(
        self, image_bytes: bytes, width: int, height: int, pixel_format: str = "rgb24"
    ) -> dict:
        """Process a single image for testing purposes.

        Args:
            image_bytes: Raw image bytes
            width: Image width
            height: Image height
            pixel_format: Pixel format (rgb24 or gray)

        Returns:
            Dict with text, confidence, and latency_ms
        """
        frame = VideoFrame(
            data=image_bytes,
            width=width,
            height=height,
            pixel_format=pixel_format,
            timestamp_ms=int(time.time() * 1000),
        )

        start_time = time.time()
        text, confidence = await asyncio.get_event_loop().run_in_executor(
            None, self._run_ocr, frame
        )
        latency_ms = (time.time() - start_time) * 1000

        return {
            "text": text,
            "confidence": confidence,
            "latency_ms": latency_ms,
        }

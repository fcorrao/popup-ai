"""Transcriber actor for speech-to-text using mlx-whisper."""

import asyncio
import contextlib
import logging
import time
import wave

import ray
from ray.util.queue import Queue

from popup_ai.audio.preprocessor import AudioPreprocessor
from popup_ai.config import TranscriberConfig
from popup_ai.messages import ActorStatus, AudioChunk, Transcript, TranscriptSegment, UIEvent

logger = logging.getLogger(__name__)


@ray.remote
class TranscriberActor:
    """Actor that transcribes audio chunks using mlx-whisper.

    Accumulates audio chunks and processes them in batches
    for efficient transcription.
    """

    def __init__(
        self,
        config: TranscriberConfig,
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
        self._transcripts_sent = 0
        self._audio_seconds_processed = 0.0
        self._model = None
        self._preprocessor = AudioPreprocessor(config)
        self._audio_buffer: list[bytes] = []
        self._buffer_duration_s = 0.0
        self._last_sample_rate = 16000
        self._chunks_with_speech = 0
        self._chunks_without_speech = 0
        self._logger = logging.getLogger("popup_ai.actors.transcriber")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "transcripts_sent": self._transcripts_sent,
            "audio_seconds_processed": round(self._audio_seconds_processed, 2),
            "buffer_duration_s": round(self._buffer_duration_s, 2),
            "chunks_with_speech": self._chunks_with_speech,
            "chunks_without_speech": self._chunks_without_speech,
        }
        stats.update(self._preprocessor.get_stats())
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="transcriber",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the transcriber."""
        if self._state == "running":
            return

        self._logger.info("Starting transcriber actor")
        self._state = "starting"
        self._error = None

        try:
            await self._load_model()
            # Load VAD model if enabled
            if self.config.vad_enabled:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._preprocessor.load_vad_model)
            self._state = "running"
            self._running = True
            self._start_time = time.time()
            self._process_task = asyncio.create_task(self._process_loop())
            self._publish_ui_event("started", self._preprocessor.get_stats())
            self._logger.info("Transcriber actor started")
        except Exception as e:
            self._state = "error"
            self._error = str(e)
            self._logger.exception("Failed to start transcriber")
            self._publish_ui_event("error", {"message": str(e)})
            raise

    async def stop(self) -> None:
        """Stop the transcriber."""
        if self._state == "stopped":
            return

        self._logger.info("Stopping transcriber actor")
        self._running = False

        if self._process_task:
            self._process_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._process_task

        self._model = None
        self._audio_buffer.clear()
        self._buffer_duration_s = 0.0
        self._state = "stopped"
        self._start_time = None
        self._publish_ui_event("stopped", {})
        self._logger.info("Transcriber actor stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    async def _load_model(self) -> None:
        """Load the whisper model."""
        self._logger.info(f"Loading whisper model: {self.config.model}")
        loop = asyncio.get_event_loop()

        def load():
            import mlx_whisper
            return mlx_whisper

        self._model = await loop.run_in_executor(None, load)
        self._logger.info("Whisper model loaded")

    async def _process_loop(self) -> None:
        """Main processing loop."""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                # Get audio chunk from queue with timeout (in executor to avoid blocking)
                try:
                    chunk = await loop.run_in_executor(
                        None, lambda: self.input_queue.get(block=True, timeout=0.5)
                    )
                except Exception:
                    # Timeout or empty queue
                    # Process buffer if we have enough audio
                    if self._buffer_duration_s >= self.config.chunk_length_s:
                        await self._process_buffer()
                    continue

                if isinstance(chunk, AudioChunk):
                    self._add_to_buffer(chunk)

                    # Process when we have enough audio
                    if self._buffer_duration_s >= self.config.chunk_length_s:
                        await self._process_buffer()

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in transcription loop")
                await asyncio.sleep(0.1)

    def _add_to_buffer(self, chunk: AudioChunk) -> None:
        """Add audio chunk to buffer."""
        self._audio_buffer.append(chunk.data)
        self._last_sample_rate = chunk.sample_rate
        # Calculate duration: bytes / (sample_rate * channels * bytes_per_sample)
        duration_s = len(chunk.data) / (chunk.sample_rate * chunk.channels * 2)
        self._buffer_duration_s += duration_s
        # Publish audio_received event for UI
        self._publish_ui_event("audio_received", {
            "bytes": len(chunk.data),
            "duration_s": round(duration_s, 3),
            "buffer_duration_s": round(self._buffer_duration_s, 2),
        })

    async def _process_buffer(self) -> None:
        """Process accumulated audio buffer."""
        if not self._audio_buffer or not self._model:
            return

        # Combine audio data
        audio_data = b"".join(self._audio_buffer)
        duration_s = self._buffer_duration_s

        # Keep overlap for continuity
        overlap_bytes = int(
            self.config.overlap_s * self._last_sample_rate * 2
        )
        if len(audio_data) > overlap_bytes:
            self._audio_buffer = [audio_data[-overlap_bytes:]]
            self._buffer_duration_s = self.config.overlap_s
        else:
            self._audio_buffer.clear()
            self._buffer_duration_s = 0.0

        # Preprocess audio (VAD, normalization, silence trimming)
        loop = asyncio.get_event_loop()
        preprocess_result = await loop.run_in_executor(
            None,
            lambda: self._preprocessor.process(audio_data, self._last_sample_rate),
        )

        # Track speech detection stats
        if preprocess_result.has_speech:
            self._chunks_with_speech += 1
        else:
            self._chunks_without_speech += 1
            # Skip transcription if no speech detected
            self._logger.debug("No speech detected, skipping transcription")
            return

        # Use preprocessed audio
        processed_audio = preprocess_result.audio_data

        def transcribe():
            import os
            import tempfile

            # Write to temporary WAV file for mlx-whisper
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
                with wave.open(f, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(self._last_sample_rate)
                    wav.writeframes(processed_audio)

            try:
                result = self._model.transcribe(
                    wav_path,
                    path_or_hf_repo=self.config.model,
                    language=self.config.language,
                )
                return result
            finally:
                os.unlink(wav_path)

        try:
            result = await loop.run_in_executor(None, transcribe)
            self._audio_seconds_processed += duration_s

            if result and result.get("text", "").strip():
                # Create transcript
                segments = []
                for seg in result.get("segments", []):
                    segments.append(TranscriptSegment(
                        text=seg.get("text", ""),
                        start_ms=int(seg.get("start", 0) * 1000),
                        end_ms=int(seg.get("end", 0) * 1000),
                    ))

                transcript = Transcript(
                    text=result["text"].strip(),
                    segments=segments,
                    is_partial=False,
                    timestamp_ms=int(time.time() * 1000),
                )

                # Push to output queue
                try:
                    self.output_queue.put_nowait(transcript)
                    self._transcripts_sent += 1
                    self._publish_ui_event("transcript", {"text": transcript.text})
                except Exception:
                    self._logger.debug("Output queue full, dropping transcript")

        except Exception:
            self._logger.exception("Transcription failed")

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="transcriber",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

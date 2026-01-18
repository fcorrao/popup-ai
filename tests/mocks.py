"""Mock actors for testing the popup-ai pipeline."""

import asyncio
import contextlib
import time

import ray
from ray.util.queue import Queue

from popup_ai.messages import ActorStatus, Annotation, AudioChunk, Transcript, UIEvent


@ray.remote
class MockAudioIngestActor:
    """Mock audio ingest actor that generates test audio chunks."""

    def __init__(
        self,
        output_queue: Queue,
        ui_queue: Queue | None = None,
        chunks_to_generate: int = 10,
        chunk_interval_ms: int = 100,
    ) -> None:
        self.output_queue = output_queue
        self.ui_queue = ui_queue
        self.chunks_to_generate = chunks_to_generate
        self.chunk_interval_ms = chunk_interval_ms
        self._state = "stopped"
        self._running = False
        self._chunks_sent = 0
        self._task: asyncio.Task | None = None

    def get_status(self) -> ActorStatus:
        return ActorStatus(
            name="audio_ingest",
            state=self._state,
            stats={"chunks_sent": self._chunks_sent},
        )

    async def start(self) -> None:
        self._state = "running"
        self._running = True
        self._task = asyncio.create_task(self._generate_chunks())
        self._publish_event("started", {})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._state = "stopped"
        self._publish_event("stopped", {})

    def health_check(self) -> bool:
        return self._state == "running"

    async def _generate_chunks(self) -> None:
        for _ in range(self.chunks_to_generate):
            if not self._running:
                break
            # Generate mock PCM data (1600 samples = 100ms at 16kHz)
            chunk = AudioChunk(
                data=b"\x00" * 3200,  # 1600 samples * 2 bytes
                timestamp_ms=int(time.time() * 1000),
                sample_rate=16000,
                channels=1,
            )
            try:
                self.output_queue.put_nowait(chunk)
                self._chunks_sent += 1
            except Exception:
                pass
            await asyncio.sleep(self.chunk_interval_ms / 1000)

    def _publish_event(self, event_type: str, data: dict) -> None:
        if self.ui_queue:
            with contextlib.suppress(Exception):
                self.ui_queue.put_nowait(
                    UIEvent(
                        source="audio_ingest",
                        event_type=event_type,
                        data=data,
                        timestamp_ms=int(time.time() * 1000),
                    )
                )


@ray.remote
class MockTranscriberActor:
    """Mock transcriber actor that returns canned transcripts."""

    def __init__(
        self,
        input_queue: Queue,
        output_queue: Queue,
        ui_queue: Queue | None = None,
        transcript_text: str = "This is a test transcript.",
    ) -> None:
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.ui_queue = ui_queue
        self.transcript_text = transcript_text
        self._state = "stopped"
        self._running = False
        self._transcripts_sent = 0
        self._task: asyncio.Task | None = None

    def get_status(self) -> ActorStatus:
        return ActorStatus(
            name="transcriber",
            state=self._state,
            stats={"transcripts_sent": self._transcripts_sent},
        )

    async def start(self) -> None:
        self._state = "running"
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        self._publish_event("started", {})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._state = "stopped"
        self._publish_event("stopped", {})

    def health_check(self) -> bool:
        return self._state == "running"

    async def _process_loop(self) -> None:
        chunks_received = 0
        while self._running:
            try:
                self.input_queue.get(block=True, timeout=0.5)
                chunks_received += 1
                # Every 5 chunks, emit a transcript
                if chunks_received >= 5:
                    transcript = Transcript(
                        text=self.transcript_text,
                        segments=[],
                        is_partial=False,
                        timestamp_ms=int(time.time() * 1000),
                    )
                    self.output_queue.put_nowait(transcript)
                    self._transcripts_sent += 1
                    self._publish_event("transcript", {"text": self.transcript_text})
                    chunks_received = 0
            except Exception:
                pass

    def _publish_event(self, event_type: str, data: dict) -> None:
        if self.ui_queue:
            with contextlib.suppress(Exception):
                self.ui_queue.put_nowait(
                    UIEvent(
                        source="transcriber",
                        event_type=event_type,
                        data=data,
                        timestamp_ms=int(time.time() * 1000),
                    )
                )


@ray.remote
class MockAnnotatorActor:
    """Mock annotator actor that returns canned annotations."""

    def __init__(
        self,
        input_queue: Queue,
        output_queue: Queue,
        ui_queue: Queue | None = None,
        annotation_term: str = "test term",
        annotation_explanation: str = "This is a test explanation.",
    ) -> None:
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.ui_queue = ui_queue
        self.annotation_term = annotation_term
        self.annotation_explanation = annotation_explanation
        self._state = "stopped"
        self._running = False
        self._annotations_sent = 0
        self._task: asyncio.Task | None = None

    def get_status(self) -> ActorStatus:
        return ActorStatus(
            name="annotator",
            state=self._state,
            stats={"annotations_sent": self._annotations_sent},
        )

    async def start(self) -> None:
        self._state = "running"
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        self._publish_event("started", {})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._state = "stopped"
        self._publish_event("stopped", {})

    def health_check(self) -> bool:
        return self._state == "running"

    async def _process_loop(self) -> None:
        slot = 1
        while self._running:
            try:
                self.input_queue.get(block=True, timeout=0.5)
                annotation = Annotation(
                    term=self.annotation_term,
                    explanation=self.annotation_explanation,
                    display_duration_ms=5000,
                    slot=slot,
                    timestamp_ms=int(time.time() * 1000),
                )
                self.output_queue.put_nowait(annotation)
                self._annotations_sent += 1
                slot = (slot % 4) + 1
                self._publish_event("annotation", {"term": self.annotation_term})
            except Exception:
                pass

    def _publish_event(self, event_type: str, data: dict) -> None:
        if self.ui_queue:
            with contextlib.suppress(Exception):
                self.ui_queue.put_nowait(
                    UIEvent(
                        source="annotator",
                        event_type=event_type,
                        data=data,
                        timestamp_ms=int(time.time() * 1000),
                    )
                )


@ray.remote
class MockOverlayActor:
    """Mock overlay actor that logs received annotations."""

    def __init__(
        self,
        input_queue: Queue,
        ui_queue: Queue | None = None,
    ) -> None:
        self.input_queue = input_queue
        self.ui_queue = ui_queue
        self._state = "stopped"
        self._running = False
        self._annotations_received = 0
        self._task: asyncio.Task | None = None
        self.received_annotations: list[Annotation] = []

    def get_status(self) -> ActorStatus:
        return ActorStatus(
            name="overlay",
            state=self._state,
            stats={"annotations_received": self._annotations_received},
        )

    async def start(self) -> None:
        self._state = "running"
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        self._publish_event("started", {})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._state = "stopped"
        self._publish_event("stopped", {})

    def health_check(self) -> bool:
        return self._state == "running"

    def get_received_annotations(self) -> list[Annotation]:
        return self.received_annotations

    async def _process_loop(self) -> None:
        while self._running:
            try:
                annotation = self.input_queue.get(block=True, timeout=0.5)
                self.received_annotations.append(annotation)
                self._annotations_received += 1
                self._publish_event("displayed", {"term": annotation.term})
            except Exception:
                pass

    def _publish_event(self, event_type: str, data: dict) -> None:
        if self.ui_queue:
            with contextlib.suppress(Exception):
                self.ui_queue.put_nowait(
                    UIEvent(
                        source="overlay",
                        event_type=event_type,
                        data=data,
                        timestamp_ms=int(time.time() * 1000),
                    )
                )

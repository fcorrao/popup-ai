"""Annotator actor for LLM-based term extraction and explanation."""

import asyncio
import contextlib
import hashlib
import logging
import sqlite3
import time
from pathlib import Path

import logfire
import ray
from ray.util.queue import Queue

from popup_ai.config import AnnotatorConfig
from popup_ai.messages import ActorStatus, Annotation, Transcript, UIEvent
from popup_ai.observability import ensure_logfire_configured, get_metrics

logger = logging.getLogger(__name__)


@ray.remote
class AnnotatorActor:
    """Actor that extracts terms and generates explanations using LLM.

    Features:
    - SQLite cache to avoid duplicate LLM calls
    - pydantic-ai for structured LLM output
    - Configurable provider and model
    """

    def __init__(
        self,
        config: AnnotatorConfig,
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
        self._annotations_sent = 0
        self._cache_hits = 0
        self._llm_calls = 0
        self._db_conn: sqlite3.Connection | None = None
        self._agent = None
        self._slot_counter = 0
        self._logger = logging.getLogger("popup_ai.actors.annotator")

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "annotations_sent": self._annotations_sent,
            "cache_hits": self._cache_hits,
            "llm_calls": self._llm_calls,
        }
        if self._start_time:
            stats["uptime_s"] = time.time() - self._start_time
        return ActorStatus(
            name="annotator",
            state=self._state,
            error=self._error,
            stats=stats,
        )

    async def start(self) -> None:
        """Start the annotator."""
        if self._state == "running":
            return

        ensure_logfire_configured()
        with logfire.span("annotator.start"):
            self._logger.info("Starting annotator actor")
            self._state = "starting"
            self._error = None

            try:
                self._init_cache()
                await self._init_agent()

                # Warmup LLM connection
                if self._agent:
                    await self._warmup_llm()

                self._state = "running"
                self._running = True
                self._start_time = time.time()
                self._process_task = asyncio.create_task(self._process_loop())
                self._publish_ui_event("started", {})
                logfire.info(
                    "annotator started",
                    provider=self.config.provider,
                    model=self.config.model,
                )
            except Exception as e:
                self._state = "error"
                self._error = str(e)
                logfire.exception("Failed to start annotator")
                self._publish_ui_event("error", {"message": str(e)})
                raise

    async def stop(self) -> None:
        """Stop the annotator."""
        if self._state == "stopped":
            return

        with logfire.span("annotator.stop"):
            self._logger.info("Stopping annotator actor")
            self._running = False

            if self._process_task:
                self._process_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._process_task

            if self._db_conn:
                self._db_conn.close()
                self._db_conn = None

            self._agent = None
            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            logfire.info("annotator stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    def _init_cache(self) -> None:
        """Initialize SQLite cache."""
        if not self.config.cache_enabled:
            return

        cache_path = Path(self.config.cache_path).expanduser()
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_conn = sqlite3.connect(str(cache_path))
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                text_hash TEXT PRIMARY KEY,
                term TEXT NOT NULL,
                explanation TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        self._db_conn.commit()
        self._logger.info(f"Cache initialized at {cache_path}")

    async def _init_agent(self) -> None:
        """Initialize pydantic-ai agent."""
        try:
            from pydantic import BaseModel
            from pydantic_ai import Agent

            class AnnotationResult(BaseModel):
                term: str
                explanation: str

            self._agent = Agent(
                f"{self.config.provider}:{self.config.model}",
                output_type=list[AnnotationResult],
                system_prompt=(
                    "You are a helpful assistant that extracts key terms from speech transcripts "
                    "and provides brief, educational explanations. Focus on technical terms, "
                    "jargon, or concepts that viewers might not understand. Keep explanations "
                    "concise (1-2 sentences max)."
                ),
            )
            self._logger.info(f"Agent initialized with {self.config.provider}:{self.config.model}")
        except ImportError:
            self._logger.warning("pydantic-ai not installed, annotator will use mock mode")
            self._agent = None
        except Exception as e:
            # Handle missing API keys, invalid config, etc.
            self._logger.warning(f"Failed to initialize LLM agent: {e}")
            self._logger.warning("Annotator will run in mock mode (no LLM calls)")
            self._agent = None

    async def _warmup_llm(self) -> None:
        """Warmup LLM connection with a simple test call."""
        if not self._agent:
            return

        try:
            self._logger.info("Warming up LLM connection...")
            start_time = time.time()
            result = await self._agent.run("Test: hello")
            latency_ms = (time.time() - start_time) * 1000
            self._logger.info(f"LLM warmup complete in {latency_ms:.0f}ms")
        except Exception as e:
            self._logger.warning(f"LLM warmup failed: {e}")
            # Don't raise - allow annotator to start anyway, maybe network is temporarily down

    async def _process_loop(self) -> None:
        """Main processing loop."""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                # Get transcript from queue with timeout (in executor to avoid blocking)
                try:
                    transcript = await loop.run_in_executor(
                        None, lambda: self.input_queue.get(block=True, timeout=0.5)
                    )
                except Exception:
                    continue

                if isinstance(transcript, Transcript) and transcript.text.strip():
                    await self._process_transcript(transcript)

            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Error in annotation loop")
                await asyncio.sleep(0.1)

    async def _process_transcript(self, transcript: Transcript) -> None:
        """Process a transcript and generate annotations."""
        text = transcript.text.strip()
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        # Publish transcript_received event for UI
        self._publish_ui_event("transcript_received", {
            "text": text[:100],
            "text_hash": text_hash,
        })

        # Check cache first
        cached = self._get_cached(text_hash)
        if cached:
            self._cache_hits += 1
            if metrics := get_metrics():
                metrics["llm_cache_hits"].add(1)
            for term, explanation in cached:
                await self._emit_annotation(term, explanation, cache_hit=True)
            return

        # Call LLM
        annotations = await self._call_llm(text)
        self._llm_calls += 1

        # Cache and emit results
        for term, explanation in annotations:
            self._cache_annotation(text_hash, term, explanation)
            await self._emit_annotation(term, explanation, cache_hit=False)

    def _get_cached(self, text_hash: str) -> list[tuple[str, str]] | None:
        """Get cached annotations for text hash."""
        if not self._db_conn:
            return None

        cursor = self._db_conn.execute(
            "SELECT term, explanation FROM annotations WHERE text_hash = ?",
            (text_hash,),
        )
        rows = cursor.fetchall()
        return rows if rows else None

    def _cache_annotation(self, text_hash: str, term: str, explanation: str) -> None:
        """Cache an annotation."""
        if not self._db_conn:
            return

        try:
            self._db_conn.execute(
                "INSERT OR REPLACE INTO annotations "
                "(text_hash, term, explanation, created_at) VALUES (?, ?, ?, ?)",
                (text_hash, term, explanation, time.time()),
            )
            self._db_conn.commit()
        except Exception as e:
            self._logger.warning(f"Failed to cache annotation: {e}")

    async def _call_llm(self, text: str) -> list[tuple[str, str]]:
        """Call LLM to extract annotations."""
        if not self._agent:
            # Mock mode for testing without pydantic-ai
            return []

        try:
            prompt = self.config.prompt_template.format(text=text)
            start_time = time.time()
            result = await self._agent.run(prompt)
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            if metrics := get_metrics():
                metrics["llm_calls"].add(1)
                metrics["llm_latency"].record(latency_ms)

            annotations = []
            for item in result.output:
                annotations.append((item.term, item.explanation))
            return annotations

        except Exception:
            logfire.exception("LLM call failed")
            return []

    async def _emit_annotation(
        self, term: str, explanation: str, cache_hit: bool = False
    ) -> None:
        """Emit an annotation to the output queue."""
        self._slot_counter = (self._slot_counter % 4) + 1

        annotation = Annotation(
            term=term,
            explanation=explanation,
            display_duration_ms=self.config.cache_enabled and 5000 or 5000,
            slot=self._slot_counter,
            timestamp_ms=int(time.time() * 1000),
        )

        try:
            self.output_queue.put_nowait(annotation)
            self._annotations_sent += 1
            self._publish_ui_event("annotation", {
                "term": term,
                "explanation": explanation,
                "cache_hit": cache_hit,
                "slot": self._slot_counter,
            })
        except Exception:
            self._logger.debug("Output queue full, dropping annotation")

    def _publish_ui_event(self, event_type: str, data: dict) -> None:
        """Publish an event to the UI queue."""
        if self.ui_queue is None:
            return
        try:
            event = UIEvent(
                source="annotator",
                event_type=event_type,
                data=data,
                timestamp_ms=int(time.time() * 1000),
            )
            self.ui_queue.put_nowait(event)
        except Exception:
            pass

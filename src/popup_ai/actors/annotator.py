"""Annotator actor for LLM-based term extraction and explanation."""

import asyncio
import contextlib
import hashlib
import logging
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Any

import logfire
import ray
from pydantic import BaseModel
from ray.util.queue import Queue

from popup_ai.config import AnnotatorConfig
from popup_ai.messages import ActorStatus, Annotation, Transcript, UIEvent
from popup_ai.observability import ensure_logfire_configured, get_metrics

logger = logging.getLogger(__name__)


class AnnotationResult(BaseModel):
    """A single annotation result from the LLM."""
    term: str
    explanation: str


class AnnotationResponse(BaseModel):
    """Response from the LLM containing annotations."""
    annotations: list[AnnotationResult]


def get_annotation_schema() -> dict:
    """Get the JSON schema for the annotation output model."""
    return AnnotationResponse.model_json_schema()


# Default system prompt (used if config doesn't specify one)
DEFAULT_SYSTEM_PROMPT = """You are a helpful assistant that extracts key terms from speech transcripts and provides brief, educational explanations.

Focus on "terms of art" - specialized jargon that someone outside programming or computer science would NOT be familiar with. These are words that have specific technical meanings in our field but would confuse a general audience.

GOOD examples of terms to annotate:
- "emacs" → "A text editor like MS Word used by programmers. Very extensible, so it can do much more than edit text."
- "internal fragmentation" → "Renting a storage unit too big for your stuff—you pay for space you can't use."
- "garbage collection" → "Automatic cleanup of memory your program no longer needs, like a self-emptying trash can."
- "race condition" → "When two processes compete to update the same data, like two people editing the same document—whoever saves last wins."

BAD examples (too common/obvious, don't annotate these):
- "computer", "software", "website", "app", "code", "programming"

Guidelines:
- Use metaphors and analogies to everyday objects when possible
- Keep explanations terse - readable in under 5 seconds
- Return 1-3 annotations per transcript depending on content density
- Return an empty annotations list if no terms warrant explanation"""


@ray.remote
class AnnotatorActor:
    """Actor that extracts terms and generates explanations using LLM.

    Features:
    - SQLite cache to avoid duplicate LLM calls
    - pydantic-ai for structured LLM output with multiple providers
    - Supports OpenAI, Anthropic, Cerebras, and OpenAI-compatible endpoints
    - Runtime reconfiguration without restart
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
        self._annotations_deduplicated = 0
        self._cache_hits = 0
        self._llm_calls = 0
        self._db_conn: sqlite3.Connection | None = None
        self._agent: Any = None
        self._http_client: Any = None  # Custom httpx client for providers
        self._slot_counter = 0
        self._logger = logging.getLogger("popup_ai.actors.annotator")

        # Track recently emitted annotations to avoid duplicates
        # Format: (timestamp_ms, term_lower)
        self._recent_annotations: deque[tuple[int, str]] = deque()
        self._annotation_dedupe_window_ms = 60_000  # 60 seconds

        # Configure logfire in __init__ to avoid blocking async event loop
        ensure_logfire_configured()

    def get_status(self) -> ActorStatus:
        """Get current actor status."""
        stats = {
            "annotations_sent": self._annotations_sent,
            "annotations_deduplicated": self._annotations_deduplicated,
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

        with logfire.span("annotator.start"):
            self._logger.info("Starting annotator actor")
            import os

            # Clear proxy env vars - httpx in Ray actors has issues with proxies
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''
            os.environ['http_proxy'] = ''
            os.environ['https_proxy'] = ''

            self._state = "starting"
            self._error = None

            try:
                self._init_cache()
                self._init_agent()

                # Warmup LLM connection
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

            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None

            self._agent = None
            self._state = "stopped"
            self._start_time = None
            self._publish_ui_event("stopped", {})
            logfire.info("annotator stopped")

    def health_check(self) -> bool:
        """Check if actor is healthy."""
        return self._state == "running" and self._running

    def _init_cache(self) -> None:
        """Initialize SQLite cache and prompts storage."""
        cache_path = Path(self.config.cache_path).expanduser()
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_conn = sqlite3.connect(str(cache_path))

        # Annotations cache table
        if self.config.cache_enabled:
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS annotations (
                    text_hash TEXT NOT NULL,
                    term TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (text_hash, term)
                )
            """)
            # Index for term lookups (to check if term was recently annotated)
            self._db_conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_annotations_term
                ON annotations(term COLLATE NOCASE)
            """)

        # Prompts table (always created for prompt persistence)
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (provider, model)
            )
        """)
        self._db_conn.commit()
        self._logger.info(f"Database initialized at {cache_path}")

    def _load_prompts(self, provider: str, model: str) -> tuple[str, str] | None:
        """Load prompts for a specific provider/model from DB.

        Returns (system_prompt, prompt_template) or None if not found.
        """
        if not self._db_conn:
            return None

        try:
            cursor = self._db_conn.execute(
                "SELECT system_prompt, prompt_template FROM prompts WHERE provider = ? AND model = ?",
                (provider, model),
            )
            row = cursor.fetchone()
            if row:
                return (row[0], row[1])
            return None
        except Exception as e:
            self._logger.warning(f"Failed to load prompts: {e}")
            return None

    def _save_prompts(self, provider: str, model: str, system_prompt: str, prompt_template: str) -> bool:
        """Save prompts for a specific provider/model to DB."""
        if not self._db_conn:
            return False

        try:
            self._db_conn.execute(
                """
                INSERT OR REPLACE INTO prompts (provider, model, system_prompt, prompt_template, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (provider, model, system_prompt, prompt_template, time.time()),
            )
            self._db_conn.commit()
            self._logger.info(f"Saved prompts for {provider}:{model}")
            return True
        except Exception as e:
            self._logger.warning(f"Failed to save prompts: {e}")
            return False

    def get_prompts(self, provider: str, model: str) -> dict[str, str]:
        """Get prompts for a provider/model, falling back to config defaults.

        Returns dict with 'system_prompt' and 'prompt_template' keys.
        """
        # Try to load from DB first
        db_prompts = self._load_prompts(provider, model)
        if db_prompts:
            return {
                "system_prompt": db_prompts[0],
                "prompt_template": db_prompts[1],
            }

        # Fall back to config defaults
        return {
            "system_prompt": getattr(self.config, 'system_prompt', None) or DEFAULT_SYSTEM_PROMPT,
            "prompt_template": self.config.prompt_template,
        }

    def set_prompts(self, provider: str, model: str, system_prompt: str, prompt_template: str) -> bool:
        """Save prompts for a provider/model to DB."""
        return self._save_prompts(provider, model, system_prompt, prompt_template)

    def _init_agent(self) -> None:
        """Initialize pydantic-ai agent for LLM calls."""
        from pydantic_ai import Agent

        model = self._create_model()
        if model is None:
            self._logger.warning("No API key configured, annotator will use mock mode")
            return

        # Load prompts from DB, falling back to config defaults
        prompts = self.get_prompts(self.config.provider, self.config.model)
        self._current_system_prompt = prompts["system_prompt"]
        self._current_prompt_template = prompts["prompt_template"]

        self._agent = Agent(
            model,
            output_type=AnnotationResponse,
            system_prompt=self._current_system_prompt,
        )
        self._logger.info(f"Agent initialized for {self.config.provider}:{self.config.model}")

    def _create_model(self) -> Any:
        """Create model instance based on provider config."""
        import os

        import httpx

        # Create async httpx client with explicit proxy disable
        # Ray actors can have stale proxy settings that break connections
        self._http_client = httpx.AsyncClient(
            timeout=30.0,
            proxy=None,
            trust_env=False,  # Don't read proxy settings from environment
        )
        self._logger.info(f"Created httpx client with trust_env=False, proxy=None")

        if self.config.provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            return f"openai:{self.config.model}"

        elif self.config.provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                self._logger.warning("ANTHROPIC_API_KEY not set")
                return None
            from pydantic_ai.models.anthropic import AnthropicModel
            return AnthropicModel(self.config.model)

        elif self.config.provider == "cerebras":
            api_key = os.environ.get("CEREBRAS_API_KEY", "")
            if not api_key:
                self._logger.warning("CEREBRAS_API_KEY not set")
                return None
            from pydantic_ai.models.cerebras import CerebrasModel
            return CerebrasModel(self.config.model)

        elif self.config.provider == "openai_compatible":
            # For local models (Ollama, vLLM, etc.)
            from pydantic_ai.models.openai import OpenAIModel
            from pydantic_ai.providers.openai import OpenAIProvider

            # Get API key from custom env var or default
            api_key_var = self.config.api_key_env_var or "LOCAL_LLM_API_KEY"
            api_key = os.environ.get(api_key_var, "")

            base_url = self.config.base_url or "http://localhost:11434/v1"
            self._logger.info(f"Connecting to openai_compatible: {base_url} model={self.config.model}")

            return OpenAIModel(
                self.config.model,
                provider=OpenAIProvider(
                    base_url=base_url,
                    api_key=api_key or None,
                    http_client=self._http_client,
                ),
            )

        else:
            self._logger.error(f"Unknown provider: {self.config.provider}")
            return None

    async def _warmup_llm(self) -> None:
        """Warmup LLM connection with a simple test call."""
        if not self._agent:
            return

        try:
            self._logger.info("Warming up LLM connection...")
            start_time = time.time()

            # Use a warmup prompt that produces valid AnnotationResponse
            await self._agent.run("The word is: API. Explain it briefly.")
            latency_ms = (time.time() - start_time) * 1000
            self._logger.info(f"LLM warmup complete in {latency_ms:.0f}ms")
        except Exception as e:
            self._logger.warning(f"LLM warmup failed (non-fatal): {e}")
            # Don't raise - allow annotator to start anyway

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

    def _get_cached_term(self, term: str) -> str | None:
        """Look up a term in the cache (case-insensitive).

        Returns the explanation if found, None otherwise.
        """
        if not self._db_conn:
            return None

        cursor = self._db_conn.execute(
            "SELECT explanation FROM annotations WHERE term = ? COLLATE NOCASE "
            "ORDER BY created_at DESC LIMIT 1",
            (term,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    async def _call_llm(self, text: str) -> list[tuple[str, str]]:
        """Call LLM to extract annotations using pydantic-ai agent."""
        if not self._agent:
            # Mock mode - no API key configured
            return []

        # Use current prompt template (from DB or config default)
        prompt_template = getattr(self, '_current_prompt_template', None) or self.config.prompt_template
        prompt = prompt_template.format(text=text)

        # Retry once with agent reinitialization on connection errors
        for attempt in range(2):
            try:
                start_time = time.time()
                result = await self._agent.run(prompt)
                latency_ms = (time.time() - start_time) * 1000

                # Record metrics
                if metrics := get_metrics():
                    metrics["llm_calls"].add(1)
                    metrics["llm_latency"].record(latency_ms)

                # Extract annotations from structured response
                annotations = [(a.term, a.explanation) for a in result.output.annotations]
                return annotations
            except Exception as e:
                error_msg = str(e).lower()
                if attempt == 0 and ("connection" in error_msg or "connect" in error_msg):
                    self._logger.warning(f"Connection error, reinitializing agent: {e}")
                    self._init_agent()
                    continue
                raise

        return []  # Unreachable, but satisfies type checker

    def _is_recently_emitted(self, term: str) -> bool:
        """Check if this term was recently emitted."""
        now_ms = int(time.time() * 1000)
        window_start = now_ms - self._annotation_dedupe_window_ms

        # Clean up old entries
        while self._recent_annotations and self._recent_annotations[0][0] < window_start:
            self._recent_annotations.popleft()

        # Check if term was recently emitted (case-insensitive)
        term_lower = term.lower()
        for _, recent_term in self._recent_annotations:
            if recent_term == term_lower:
                return True

        return False

    async def _emit_annotation(
        self, term: str, explanation: str, cache_hit: bool = False
    ) -> None:
        """Emit an annotation to the output queue."""
        # Check for recently emitted duplicates (term-level dedupe)
        if self._is_recently_emitted(term):
            self._logger.warning(f"Skipping duplicate term (already shown in last 60s): {term}")
            self._annotations_deduplicated += 1
            return

        # Track this emission
        self._recent_annotations.append((int(time.time() * 1000), term.lower()))

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

    async def reconfigure(self, new_config: AnnotatorConfig) -> None:
        """Reconfigure with new settings, reinitialize agent if needed."""
        needs_reinit = (
            new_config.provider != self.config.provider or
            new_config.model != self.config.model or
            new_config.base_url != self.config.base_url or
            new_config.prompt_template != self.config.prompt_template or
            new_config.api_key_env_var != self.config.api_key_env_var
        )

        self.config = new_config

        if needs_reinit and self._state == "running":
            self._logger.info(f"Reinitializing agent for {new_config.provider}:{new_config.model}")

            # Close existing http client
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None

            # Reinitialize agent
            self._init_agent()

            self._publish_ui_event("reconfigured", {
                "provider": new_config.provider,
                "model": new_config.model,
            })
            logfire.info(
                "annotator reconfigured",
                provider=new_config.provider,
                model=new_config.model,
            )

    # ========== Cache Management Methods ==========

    def get_cache_entries(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get cached annotation entries.

        Returns list of dicts with: text_hash, term, explanation, created_at
        """
        if not self._db_conn:
            return []

        try:
            cursor = self._db_conn.execute(
                """SELECT text_hash, term, explanation, created_at
                   FROM annotations
                   ORDER BY created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            return [
                {
                    "text_hash": row[0],
                    "term": row[1],
                    "explanation": row[2],
                    "created_at": row[3],
                }
                for row in cursor.fetchall()
            ]
        except Exception as e:
            self._logger.warning(f"Failed to get cache entries: {e}")
            return []

    def get_cache_count(self) -> int:
        """Get total number of cached entries."""
        if not self._db_conn:
            return 0

        try:
            cursor = self._db_conn.execute("SELECT COUNT(*) FROM annotations")
            return cursor.fetchone()[0]
        except Exception:
            return 0

    def delete_cache_entry(self, text_hash: str) -> bool:
        """Delete a specific cache entry by text_hash."""
        if not self._db_conn:
            return False

        try:
            self._db_conn.execute(
                "DELETE FROM annotations WHERE text_hash = ?",
                (text_hash,),
            )
            self._db_conn.commit()
            self._logger.info(f"Deleted cache entry: {text_hash}")
            return True
        except Exception as e:
            self._logger.warning(f"Failed to delete cache entry: {e}")
            return False

    def clear_cache(self) -> int:
        """Clear all cached annotations. Returns number of entries deleted."""
        if not self._db_conn:
            return 0

        try:
            cursor = self._db_conn.execute("SELECT COUNT(*) FROM annotations")
            count = cursor.fetchone()[0]
            self._db_conn.execute("DELETE FROM annotations")
            self._db_conn.commit()
            self._logger.info(f"Cleared {count} cache entries")
            return count
        except Exception as e:
            self._logger.warning(f"Failed to clear cache: {e}")
            return 0

    def update_cache_entry(
        self, text_hash: str, term: str, explanation: str
    ) -> bool:
        """Update an existing cache entry."""
        if not self._db_conn:
            return False

        try:
            self._db_conn.execute(
                """UPDATE annotations
                   SET term = ?, explanation = ?, created_at = ?
                   WHERE text_hash = ?""",
                (term, explanation, time.time(), text_hash),
            )
            self._db_conn.commit()
            self._logger.info(f"Updated cache entry: {text_hash}")
            return True
        except Exception as e:
            self._logger.warning(f"Failed to update cache entry: {e}")
            return False

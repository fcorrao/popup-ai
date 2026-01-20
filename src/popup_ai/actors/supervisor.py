"""Pipeline supervisor actor for managing child actors."""

import asyncio
import contextlib
import logging

import ray
from ray.util.queue import Queue

from popup_ai.config import AnnotatorConfig, Settings
from popup_ai.messages import ActorStatus, Annotation, AudioChunk, Transcript

logger = logging.getLogger(__name__)


@ray.remote
class PipelineSupervisor:
    """Supervisor actor that manages all pipeline child actors.

    Responsibilities:
    - Spawn and manage child actor lifecycles
    - Health monitoring and automatic restart on failure
    - Graceful shutdown coordination
    - Provide unified API for UI interaction
    """

    def __init__(self, settings: Settings | None = None) -> None:
        from popup_ai.config import load_settings

        self.settings = settings or load_settings()
        self._actors: dict[str, ray.actor.ActorHandle] = {}
        self._queues: dict[str, Queue] = {}
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._logger = logging.getLogger("popup_ai.supervisor")

        # Create queues for inter-actor communication
        self._init_queues()

    def _init_queues(self) -> None:
        """Initialize Ray queues for actor communication."""
        self._queues = {
            "audio": Queue(maxsize=100),
            "transcript": Queue(maxsize=100),
            "annotation": Queue(maxsize=100),
            "ui": Queue(maxsize=1000),
        }

    def get_queues(self) -> dict[str, Queue]:
        """Get all queues for external access."""
        return self._queues

    def get_ui_queue(self) -> Queue:
        """Get the UI event queue."""
        return self._queues["ui"]

    async def start(self) -> None:
        """Start the pipeline with configured actors."""
        if self._running:
            return

        self._logger.info("Starting pipeline supervisor")
        self._running = True

        # Spawn actors based on configuration
        if self.settings.pipeline.audio_enabled:
            await self._spawn_actor("audio_ingest")

        if self.settings.pipeline.transcriber_enabled:
            await self._spawn_actor("transcriber")

        if self.settings.pipeline.annotator_enabled:
            await self._spawn_actor("annotator")

        if self.settings.pipeline.overlay_enabled:
            await self._spawn_actor("overlay")

        # Start health monitor
        self._monitor_task = asyncio.create_task(self._health_monitor())
        self._logger.info("Pipeline supervisor started")

    async def stop(self) -> None:
        """Stop all actors gracefully."""
        if not self._running:
            return

        self._logger.info("Stopping pipeline supervisor")
        self._running = False

        # Cancel health monitor
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        # Stop actors in reverse order (downstream first)
        for name in reversed(list(self._actors.keys())):
            await self._stop_actor(name)

        self._logger.info("Pipeline supervisor stopped")

    async def _spawn_actor(self, actor_type: str) -> None:
        """Spawn a child actor by type."""
        if actor_type in self._actors:
            self._logger.warning(f"Actor {actor_type} already exists")
            return

        self._logger.info(f"Spawning {actor_type} actor")

        try:
            if actor_type == "audio_ingest":
                from popup_ai.actors.audio_ingest import AudioIngestActor

                actor = AudioIngestActor.remote(
                    config=self.settings.audio,
                    output_queue=self._queues["audio"],
                    ui_queue=self._queues["ui"],
                )
            elif actor_type == "transcriber":
                from popup_ai.actors.transcriber import TranscriberActor

                actor = TranscriberActor.remote(
                    config=self.settings.transcriber,
                    input_queue=self._queues["audio"],
                    output_queue=self._queues["transcript"],
                    ui_queue=self._queues["ui"],
                )
            elif actor_type == "annotator":
                from popup_ai.actors.annotator import AnnotatorActor

                actor = AnnotatorActor.remote(
                    config=self.settings.annotator,
                    input_queue=self._queues["transcript"],
                    output_queue=self._queues["annotation"],
                    ui_queue=self._queues["ui"],
                )
            elif actor_type == "overlay":
                from popup_ai.actors.overlay import OverlayActor

                actor = OverlayActor.remote(
                    config=self.settings.overlay,
                    input_queue=self._queues["annotation"],
                    ui_queue=self._queues["ui"],
                )
            else:
                raise ValueError(f"Unknown actor type: {actor_type}")

            self._actors[actor_type] = actor

            try:
                await actor.start.remote()
                self._logger.info(f"{actor_type} actor started")
            except Exception as start_error:
                # Actor was created but failed to start - still track it for status
                self._logger.error(f"{actor_type} actor failed to start: {start_error}")
                # Keep the actor in our list so we can report its error state
                # Don't re-raise - allow other actors to start

        except Exception as e:
            self._logger.exception(f"Failed to spawn {actor_type} actor")
            raise RuntimeError(f"Failed to spawn {actor_type}: {e}") from e

    async def _stop_actor(self, name: str) -> None:
        """Stop a child actor."""
        if name not in self._actors:
            return

        self._logger.info(f"Stopping {name} actor")
        try:
            actor = self._actors[name]
            await actor.stop.remote()
            ray.kill(actor)
        except Exception:
            self._logger.exception(f"Error stopping {name} actor")
        finally:
            del self._actors[name]

    async def restart_actor(self, name: str) -> None:
        """Restart a specific actor."""
        self._logger.info(f"Restarting {name} actor")
        await self._stop_actor(name)
        await self._spawn_actor(name)

    async def _health_monitor(self) -> None:
        """Monitor actor health and restart on failure."""
        while self._running:
            await asyncio.sleep(5)  # Check every 5 seconds

            for name, actor in list(self._actors.items()):
                try:
                    healthy = await actor.health_check.remote()
                    if not healthy:
                        self._logger.warning(f"{name} actor unhealthy, restarting")
                        await self.restart_actor(name)
                except ray.exceptions.RayActorError:
                    self._logger.error(f"{name} actor crashed, restarting")
                    del self._actors[name]
                    await self._spawn_actor(name)
                except Exception:
                    self._logger.exception(f"Health check failed for {name}")

    async def get_status(self) -> dict[str, ActorStatus]:
        """Get status of all actors."""
        # Take a snapshot of actors to avoid "dictionary changed size during iteration"
        actors_snapshot = dict(self._actors)
        self._logger.debug(f"get_status called, actors: {list(actors_snapshot.keys())}")
        statuses = {}
        for name, actor in actors_snapshot.items():
            try:
                status = await actor.get_status.remote()
                statuses[name] = status
                self._logger.debug(f"  {name}: state={status.state}")
            except Exception as e:
                self._logger.warning(f"Failed to get status for {name}: {e}")
                statuses[name] = ActorStatus(
                    name=name,
                    state="unreachable",
                    error="Failed to get status",
                )
        return statuses

    def is_running(self) -> bool:
        """Check if supervisor is running."""
        return self._running

    async def start_actor(self, name: str) -> None:
        """Start a specific actor."""
        await self._spawn_actor(name)

    async def stop_actor(self, name: str) -> None:
        """Stop a specific actor."""
        await self._stop_actor(name)

    def configure(self, settings: Settings) -> None:
        """Update configuration (requires restart of affected actors)."""
        self.settings = settings

    async def reconfigure_annotator(self, new_config: AnnotatorConfig) -> None:
        """Reconfigure annotator without full restart.

        Args:
            new_config: New annotator configuration
        """

        self.settings.annotator = new_config
        if "annotator" in self._actors:
            await self._actors["annotator"].reconfigure.remote(new_config)
            self._logger.info(f"Annotator reconfigured to {new_config.provider}:{new_config.model}")

    def get_annotator_prompts(self, provider: str, model: str) -> dict[str, str]:
        """Get prompts for a provider/model from annotator DB.

        Returns dict with 'system_prompt' and 'prompt_template' keys.
        Falls back to config defaults if not found in DB.
        """
        if "annotator" not in self._actors:
            # Fall back to config defaults
            return {
                "system_prompt": self.settings.annotator.system_prompt,
                "prompt_template": self.settings.annotator.prompt_template,
            }
        return ray.get(self._actors["annotator"].get_prompts.remote(provider, model))

    def set_annotator_prompts(
        self, provider: str, model: str, system_prompt: str, prompt_template: str
    ) -> bool:
        """Save prompts for a provider/model to annotator DB."""
        if "annotator" not in self._actors:
            return False
        return ray.get(
            self._actors["annotator"].set_prompts.remote(
                provider, model, system_prompt, prompt_template
            )
        )

    # ========== Test Injection Methods ==========

    def inject_audio(
        self, pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1
    ) -> None:
        """Inject PCM audio data into the audio queue for testing.

        Args:
            pcm_bytes: Raw PCM audio data (16-bit)
            sample_rate: Sample rate in Hz
            channels: Number of audio channels
        """
        import time

        from popup_ai.audio import chunk_pcm, get_audio_duration_ms

        # Chunk the audio into appropriate sizes
        chunks = chunk_pcm(pcm_bytes, sample_rate, channels, chunk_duration_ms=500)

        timestamp_ms = int(time.time() * 1000)
        for i, chunk_data in enumerate(chunks):
            audio_chunk = AudioChunk(
                data=chunk_data,
                timestamp_ms=timestamp_ms + (i * 500),
                sample_rate=sample_rate,
                channels=channels,
            )
            self._queues["audio"].put(audio_chunk)

        duration_ms = get_audio_duration_ms(pcm_bytes, sample_rate, channels)
        self._logger.info(
            f"Injected {len(chunks)} audio chunks ({duration_ms}ms) into audio queue"
        )

    def inject_transcript(
        self, text: str, segments: list[dict] | None = None
    ) -> None:
        """Inject a transcript directly into the transcript queue for testing.

        Args:
            text: Full transcript text
            segments: Optional list of segment dicts with text, start_ms, end_ms
        """
        import time

        from popup_ai.messages import TranscriptSegment

        timestamp_ms = int(time.time() * 1000)

        # Build segments if provided
        segment_objs = []
        if segments:
            for seg in segments:
                segment_objs.append(
                    TranscriptSegment(
                        text=seg.get("text", ""),
                        start_ms=seg.get("start_ms", 0),
                        end_ms=seg.get("end_ms", 0),
                        confidence=seg.get("confidence", 1.0),
                    )
                )
        else:
            # Create a single segment for the whole text
            segment_objs.append(
                TranscriptSegment(
                    text=text,
                    start_ms=0,
                    end_ms=0,
                    confidence=1.0,
                )
            )

        transcript = Transcript(
            text=text,
            segments=segment_objs,
            is_partial=False,
            timestamp_ms=timestamp_ms,
        )
        self._queues["transcript"].put(transcript)
        self._logger.info(f"Injected transcript into transcript queue: {text[:50]}...")

    def inject_annotation(
        self, term: str, explanation: str, slot: int = 1, duration_ms: int = 5000
    ) -> None:
        """Inject an annotation directly into the annotation queue for testing.

        Args:
            term: Term to display
            explanation: Explanation text
            slot: Display slot (1-4)
            duration_ms: How long to display
        """
        import time

        timestamp_ms = int(time.time() * 1000)
        annotation = Annotation(
            term=term,
            explanation=explanation,
            display_duration_ms=duration_ms,
            slot=slot,
            timestamp_ms=timestamp_ms,
        )
        self._queues["annotation"].put(annotation)
        self._logger.info(f"Injected annotation into annotation queue: {term}")

    # ========== Overlay Proxy Methods ==========

    async def overlay_send_text(self, slot: int, text: str) -> bool:
        """Send text directly to an OBS overlay slot.

        Args:
            slot: Slot number (1-4)
            text: Text to display

        Returns:
            True if successful, False otherwise
        """
        if "overlay" not in self._actors:
            self._logger.warning("Overlay actor not running")
            return False

        try:
            await self._actors["overlay"].send_text.remote(slot, text)
            return True
        except Exception as e:
            self._logger.error(f"Failed to send text to overlay: {e}")
            return False

    async def overlay_clear_slot(self, slot: int) -> bool:
        """Clear a specific overlay slot.

        Args:
            slot: Slot number (1-4)

        Returns:
            True if successful, False otherwise
        """
        if "overlay" not in self._actors:
            self._logger.warning("Overlay actor not running")
            return False

        try:
            await self._actors["overlay"].clear_slot.remote(slot)
            return True
        except Exception as e:
            self._logger.error(f"Failed to clear slot: {e}")
            return False

    async def overlay_clear_all(self) -> bool:
        """Clear all overlay slots.

        Returns:
            True if successful, False otherwise
        """
        if "overlay" not in self._actors:
            self._logger.warning("Overlay actor not running")
            return False

        try:
            await self._actors["overlay"].clear_all.remote()
            return True
        except Exception as e:
            self._logger.error(f"Failed to clear all slots: {e}")
            return False

    async def overlay_reconnect(self) -> bool:
        """Reconnect the overlay to OBS.

        Returns:
            True if successful, False otherwise
        """
        if "overlay" not in self._actors:
            self._logger.warning("Overlay actor not running")
            return False

        try:
            return await self._actors["overlay"].reconnect.remote()
        except Exception as e:
            self._logger.error(f"Failed to reconnect overlay: {e}")
            return False

    async def overlay_ping(self) -> bool:
        """Ping the OBS connection to check status.

        Returns:
            True if connected, False otherwise
        """
        if "overlay" not in self._actors:
            return False

        try:
            status = await self._actors["overlay"].get_status.remote()
            return status.stats.get("obs_connected", False)
        except Exception:
            return False

    async def overlay_inject_annotation(
        self, term: str, explanation: str, duration_ms: int | None = None
    ) -> bool:
        """Inject a test annotation into the overlay queue.

        This sends an Annotation through the normal processing flow, so it will
        be displayed and then auto-cleared after the display duration.

        Args:
            term: The term to display
            explanation: The explanation text
            duration_ms: Display duration in ms (uses default if not specified)

        Returns:
            True if annotation was queued, False otherwise
        """
        if "overlay" not in self._actors:
            self._logger.warning("Overlay actor not running")
            return False

        if "annotation" not in self._queues:
            self._logger.warning("Annotation queue not available")
            return False

        try:
            import time

            annotation = Annotation(
                term=term,
                explanation=explanation,
                display_duration_ms=duration_ms or self.settings.overlay.hold_duration_ms,
                slot=1,  # Will be reassigned by overlay actor
                timestamp_ms=int(time.time() * 1000),
            )
            self._queues["annotation"].put_nowait(annotation)
            self._logger.debug(f"Injected test annotation: {term}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to inject annotation: {e}")
            return False

    def get_annotation_schema(self) -> dict:
        """Get the JSON schema for the annotation output model.

        Returns:
            JSON schema dict for the pydantic model used by pydantic-ai
        """
        from popup_ai.actors.annotator import get_annotation_schema
        return get_annotation_schema()

    def overlay_get_discovered_slots(self) -> list[int]:
        """Get the list of discovered overlay slots.

        Returns:
            List of slot numbers, or empty list if overlay not running
        """
        if "overlay" not in self._actors:
            return []

        try:
            import ray
            return ray.get(self._actors["overlay"].get_discovered_slots.remote())
        except Exception:
            return []

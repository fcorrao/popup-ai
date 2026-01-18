"""Pipeline supervisor actor for managing child actors."""

import asyncio
import contextlib
import logging

import ray
from ray.util.queue import Queue

from popup_ai.config import Settings
from popup_ai.messages import ActorStatus

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

    def get_status(self) -> dict[str, ActorStatus]:
        """Get status of all actors."""
        statuses = {}
        for name, actor in self._actors.items():
            try:
                status = ray.get(actor.get_status.remote(), timeout=2)
                statuses[name] = status
            except Exception:
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

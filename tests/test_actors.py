"""Integration tests for popup-ai Ray actors."""

import asyncio

import pytest
import ray

# Mark all Ray tests as slow since they require Ray initialization
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def requires_ray(func):
    """Decorator to skip test if Ray can't be initialized."""
    return pytest.mark.skipif(
        not _can_init_ray(),
        reason="Ray not available or cannot be initialized"
    )(func)


def _can_init_ray() -> bool:
    """Check if Ray can be initialized."""
    try:
        if ray.is_initialized():
            return True
        # Try a quick init
        ray.init(ignore_reinit_error=True, num_cpus=1, include_dashboard=False)
        return True
    except Exception:
        return False


class TestAudioIngestActor:
    """Tests for AudioIngestActor."""

    def test_ffmpeg_check_available(self):
        """Test ffmpeg availability check (no Ray needed)."""
        from popup_ai.actors.audio_ingest import check_ffmpeg_available

        available, message = check_ffmpeg_available()
        # This test documents the behavior - it may pass or fail based on system
        assert isinstance(available, bool)
        assert isinstance(message, str)
        if available:
            assert "ffmpeg" in message.lower()
        else:
            assert "install" in message.lower()

    @pytest.mark.skip(reason="Requires Ray which has issues in pytest env")
    def test_actor_status_includes_ffmpeg_info(
        self, ray_session, ray_queues, default_audio_config
    ):
        """Test that actor status includes ffmpeg availability info."""
        from popup_ai.actors.audio_ingest import AudioIngestActor

        actor = AudioIngestActor.remote(
            config=default_audio_config,
            output_queue=ray_queues["audio"],
            ui_queue=ray_queues["ui"],
        )

        status = ray.get(actor.get_status.remote())
        assert status.name == "audio_ingest"
        assert "ffmpeg_available" in status.stats


@pytest.mark.skip(reason="Requires Ray which has issues in pytest env")
class TestMockPipeline:
    """Tests for the mock pipeline.

    These tests require Ray to be properly initialized. Run manually with:
        uv run python -c "import ray; ray.init(); exec(open('tests/test_manual.py').read())"
    """

    @pytest.mark.asyncio
    async def test_mock_audio_generates_chunks(self, ray_session, ray_queues):
        """Test that mock audio actor generates chunks."""
        from tests.mocks import MockAudioIngestActor

        actor = MockAudioIngestActor.remote(
            output_queue=ray_queues["audio"],
            ui_queue=ray_queues["ui"],
            chunks_to_generate=5,
            chunk_interval_ms=50,
        )

        await actor.start.remote()
        await asyncio.sleep(0.5)  # Wait for chunks to be generated

        status = ray.get(actor.get_status.remote())
        assert status.state == "running"
        assert status.stats["chunks_sent"] >= 1

        await actor.stop.remote()
        status = ray.get(actor.get_status.remote())
        assert status.state == "stopped"

    @pytest.mark.asyncio
    async def test_mock_transcriber_processes_chunks(self, ray_session, ray_queues):
        """Test that mock transcriber processes audio chunks."""
        from tests.mocks import MockAudioIngestActor, MockTranscriberActor

        audio_actor = MockAudioIngestActor.remote(
            output_queue=ray_queues["audio"],
            ui_queue=ray_queues["ui"],
            chunks_to_generate=10,
            chunk_interval_ms=20,
        )
        transcriber = MockTranscriberActor.remote(
            input_queue=ray_queues["audio"],
            output_queue=ray_queues["transcript"],
            ui_queue=ray_queues["ui"],
        )

        await audio_actor.start.remote()
        await transcriber.start.remote()
        await asyncio.sleep(0.5)

        status = ray.get(transcriber.get_status.remote())
        assert status.state == "running"
        # Should have processed at least some chunks
        assert status.stats["transcripts_sent"] >= 1

        await audio_actor.stop.remote()
        await transcriber.stop.remote()

    @pytest.mark.asyncio
    async def test_full_mock_pipeline(self, ray_session, ray_queues):
        """Test the full mock pipeline from audio to overlay."""
        from tests.mocks import (
            MockAnnotatorActor,
            MockAudioIngestActor,
            MockOverlayActor,
            MockTranscriberActor,
        )

        # Create all mock actors
        audio = MockAudioIngestActor.remote(
            output_queue=ray_queues["audio"],
            ui_queue=ray_queues["ui"],
            chunks_to_generate=20,
            chunk_interval_ms=10,
        )
        transcriber = MockTranscriberActor.remote(
            input_queue=ray_queues["audio"],
            output_queue=ray_queues["transcript"],
            ui_queue=ray_queues["ui"],
        )
        annotator = MockAnnotatorActor.remote(
            input_queue=ray_queues["transcript"],
            output_queue=ray_queues["annotation"],
            ui_queue=ray_queues["ui"],
        )
        overlay = MockOverlayActor.remote(
            input_queue=ray_queues["annotation"],
            ui_queue=ray_queues["ui"],
        )

        # Start all actors
        await audio.start.remote()
        await transcriber.start.remote()
        await annotator.start.remote()
        await overlay.start.remote()

        # Wait for data to flow through
        await asyncio.sleep(1.0)

        # Check that data flowed through the pipeline
        overlay_status = ray.get(overlay.get_status.remote())
        annotations = ray.get(overlay.get_received_annotations.remote())

        assert overlay_status.state == "running"
        assert len(annotations) >= 1, "Expected at least one annotation"

        # Stop all actors
        await audio.stop.remote()
        await transcriber.stop.remote()
        await annotator.stop.remote()
        await overlay.stop.remote()


@pytest.mark.skip(reason="Requires Ray which has issues in pytest env")
class TestActorHealthCheck:
    """Tests for actor health checking."""

    @pytest.mark.asyncio
    async def test_mock_actor_health_check(self, ray_session, ray_queues):
        """Test that health check works for mock actors."""
        from tests.mocks import MockAudioIngestActor

        actor = MockAudioIngestActor.remote(
            output_queue=ray_queues["audio"],
            chunks_to_generate=100,
        )

        # Before start - should be unhealthy
        assert not ray.get(actor.health_check.remote())

        # After start - should be healthy
        await actor.start.remote()
        assert ray.get(actor.health_check.remote())

        # After stop - should be unhealthy
        await actor.stop.remote()
        assert not ray.get(actor.health_check.remote())


@pytest.mark.skip(reason="Requires Ray which has issues in pytest env")
class TestPipelineSupervisor:
    """Tests for PipelineSupervisor."""

    @pytest.mark.asyncio
    async def test_supervisor_creates_queues(self, ray_session, default_settings):
        """Test that supervisor creates required queues."""
        from popup_ai.actors.supervisor import PipelineSupervisor

        # Disable all actors to avoid ffmpeg requirement
        default_settings.pipeline.audio_enabled = False
        default_settings.pipeline.transcriber_enabled = False
        default_settings.pipeline.annotator_enabled = False
        default_settings.pipeline.overlay_enabled = False

        supervisor = PipelineSupervisor.remote(default_settings)
        queues = ray.get(supervisor.get_queues.remote())

        assert "audio" in queues
        assert "transcript" in queues
        assert "annotation" in queues
        assert "ui" in queues

    @pytest.mark.asyncio
    async def test_supervisor_start_stop(self, ray_session, default_settings):
        """Test supervisor start and stop with no actors enabled."""
        from popup_ai.actors.supervisor import PipelineSupervisor

        # Disable all actors
        default_settings.pipeline.audio_enabled = False
        default_settings.pipeline.transcriber_enabled = False
        default_settings.pipeline.annotator_enabled = False
        default_settings.pipeline.overlay_enabled = False

        supervisor = PipelineSupervisor.remote(default_settings)

        # Start supervisor
        await supervisor.start.remote()
        assert ray.get(supervisor.is_running.remote())

        # Stop supervisor
        await supervisor.stop.remote()
        assert not ray.get(supervisor.is_running.remote())


@pytest.mark.skip(reason="Requires Ray which has issues in pytest env")
class TestUIEvents:
    """Tests for UI event flow."""

    @pytest.mark.asyncio
    async def test_ui_events_flow_to_queue(self, ray_session, ray_queues):
        """Test that UI events are published to the UI queue."""
        from tests.mocks import MockAudioIngestActor

        actor = MockAudioIngestActor.remote(
            output_queue=ray_queues["audio"],
            ui_queue=ray_queues["ui"],
            chunks_to_generate=5,
        )

        await actor.start.remote()
        await asyncio.sleep(0.2)

        # Check UI queue for events
        events = []
        while True:
            try:
                event = ray_queues["ui"].get_nowait()
                events.append(event)
            except Exception:
                break

        assert len(events) >= 1
        assert any(e.event_type == "started" for e in events)

        await actor.stop.remote()


class TestCLIActorFlags:
    """Tests for CLI actor control flags."""

    def test_parse_actors_valid(self):
        """Test parsing valid actor list."""
        from popup_ai.cli import parse_actors

        result = parse_actors("audio,transcriber")
        assert result == {"audio", "transcriber"}

    def test_parse_actors_with_spaces(self):
        """Test parsing actor list with spaces."""
        from popup_ai.cli import parse_actors

        result = parse_actors(" audio , transcriber , annotator ")
        assert result == {"audio", "transcriber", "annotator"}

    def test_parse_actors_all(self):
        """Test parsing all actors."""
        from popup_ai.cli import parse_actors

        result = parse_actors("audio,transcriber,annotator,overlay")
        assert result == {"audio", "transcriber", "annotator", "overlay"}

    def test_parse_actors_none(self):
        """Test parsing None returns None."""
        from popup_ai.cli import parse_actors

        result = parse_actors(None)
        assert result is None

    def test_apply_actor_flags_with_actors_list(self, default_settings):
        """Test applying --actors flag."""
        from popup_ai.cli import apply_actor_flags

        actors = {"audio", "transcriber"}
        no_flags: dict[str, bool] = {}

        apply_actor_flags(default_settings, actors, no_flags)

        assert default_settings.pipeline.audio_enabled is True
        assert default_settings.pipeline.transcriber_enabled is True
        assert default_settings.pipeline.annotator_enabled is False
        assert default_settings.pipeline.overlay_enabled is False

    def test_apply_actor_flags_with_no_flags(self, default_settings):
        """Test applying --no-<actor> flags."""
        from popup_ai.cli import apply_actor_flags

        actors = None
        no_flags = {"overlay": True, "annotator": True}

        apply_actor_flags(default_settings, actors, no_flags)

        assert default_settings.pipeline.audio_enabled is True
        assert default_settings.pipeline.transcriber_enabled is True
        assert default_settings.pipeline.annotator_enabled is False
        assert default_settings.pipeline.overlay_enabled is False

"""Pytest configuration and fixtures for popup-ai tests."""

import asyncio
import logging
import os

import pytest
import ray

# Suppress Ray warnings during tests
os.environ.setdefault("RAY_DEDUP_LOGS", "0")


@pytest.fixture(scope="session")
def ray_session():
    """Initialize Ray for the test session."""
    # Simple init for testing - no local_mode (deprecated in Ray 2.x)
    if not ray.is_initialized():
        ray.init(
            ignore_reinit_error=True,
            num_cpus=2,
            logging_level=logging.WARNING,
            include_dashboard=False,
        )
    yield
    ray.shutdown()


@pytest.fixture
def ray_queues(ray_session):
    """Create Ray queues for testing."""
    from ray.util.queue import Queue

    queues = {
        "audio": Queue(maxsize=100),
        "transcript": Queue(maxsize=100),
        "annotation": Queue(maxsize=100),
        "ui": Queue(maxsize=1000),
    }
    return queues


@pytest.fixture
def default_audio_config():
    """Default audio ingest configuration for testing."""
    from popup_ai.config import AudioIngestConfig

    return AudioIngestConfig(
        srt_port=19998,  # Use non-default port for testing
        srt_latency_ms=100,
        sample_rate=16000,
        channels=1,
        chunk_duration_ms=100,
    )


@pytest.fixture
def default_transcriber_config():
    """Default transcriber configuration for testing."""
    from popup_ai.config import TranscriberConfig

    return TranscriberConfig(
        model="mlx-community/whisper-base-mlx",
        chunk_length_s=2.0,  # Shorter for testing
        overlap_s=0.1,
        language="en",
    )


@pytest.fixture
def default_annotator_config(tmp_path):
    """Default annotator configuration for testing."""
    from popup_ai.config import AnnotatorConfig

    return AnnotatorConfig(
        provider="openai",
        model="gpt-4o-mini",
        cache_enabled=True,
        cache_path=tmp_path / "test_cache.db",
    )


@pytest.fixture
def default_overlay_config():
    """Default overlay configuration for testing."""
    from popup_ai.config import OverlayConfig

    return OverlayConfig(
        obs_host="localhost",
        obs_port=14455,  # Non-default port for testing
        obs_password=None,
        scene_name="test-scene",
        hold_duration_ms=1000,
        max_slots=4,
    )


@pytest.fixture
def default_settings(tmp_path):
    """Default settings for testing."""
    from popup_ai.config import (
        AnnotatorConfig,
        AudioIngestConfig,
        OverlayConfig,
        PipelineConfig,
        Settings,
        TranscriberConfig,
    )

    return Settings(
        pipeline=PipelineConfig(
            audio_enabled=True,
            transcriber_enabled=True,
            annotator_enabled=True,
            overlay_enabled=True,
            headless=True,
            log_level="DEBUG",
        ),
        audio=AudioIngestConfig(srt_port=19998),
        transcriber=TranscriberConfig(chunk_length_s=2.0),
        annotator=AnnotatorConfig(cache_path=tmp_path / "test_cache.db"),
        overlay=OverlayConfig(obs_port=14455),
    )


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

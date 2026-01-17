"""Run mode definitions for modular pipeline execution.

Defines architecture for running the pipeline in parts:
- ingest: SRT → PCM stream (audio capture only)
- record: SRT → audio file (save to disk)
- transcribe: file → transcript (STT processing)
- annotate: text → annotation (LLM processing)
- full: complete pipeline (all stages)

Services are decoupled and composable for flexible operation.
"""

from enum import Enum


class RunMode(str, Enum):
    """Pipeline execution modes.

    Each mode represents a different stage or combination of stages
    in the audio processing pipeline. Modes are composable and can
    be run independently.
    """

    INGEST = "ingest"
    """Ingest-only: SRT → PCM stream. Captures audio without processing."""

    RECORD = "record"
    """Record-only: SRT → audio file. Saves raw audio to disk."""

    TRANSCRIBE = "transcribe"
    """Transcribe-only: file → transcript. Processes existing audio files."""

    ANNOTATE = "annotate"
    """Annotate-only: text → annotation. Processes existing transcripts."""

    FULL = "full"
    """Full pipeline: SRT → PCM → transcript → annotation → overlay."""


# Mode capabilities for runtime introspection
MODE_CAPABILITIES: dict[RunMode, dict[str, bool]] = {
    RunMode.INGEST: {
        "needs_audio_source": True,
        "needs_stt": False,
        "needs_llm": False,
        "needs_obs": False,
        "outputs_pcm": True,
        "outputs_file": False,
        "outputs_transcript": False,
        "outputs_overlay": False,
    },
    RunMode.RECORD: {
        "needs_audio_source": True,
        "needs_stt": False,
        "needs_llm": False,
        "needs_obs": False,
        "outputs_pcm": True,
        "outputs_file": True,
        "outputs_transcript": False,
        "outputs_overlay": False,
    },
    RunMode.TRANSCRIBE: {
        "needs_audio_source": False,
        "needs_stt": True,
        "needs_llm": False,
        "needs_obs": False,
        "outputs_pcm": False,
        "outputs_file": False,
        "outputs_transcript": True,
        "outputs_overlay": False,
    },
    RunMode.ANNOTATE: {
        "needs_audio_source": False,
        "needs_stt": False,
        "needs_llm": True,
        "needs_obs": True,
        "outputs_pcm": False,
        "outputs_file": False,
        "outputs_transcript": False,
        "outputs_overlay": True,
    },
    RunMode.FULL: {
        "needs_audio_source": True,
        "needs_stt": True,
        "needs_llm": True,
        "needs_obs": True,
        "outputs_pcm": True,
        "outputs_file": False,
        "outputs_transcript": True,
        "outputs_overlay": True,
    },
}


def get_mode_requirements(mode: RunMode) -> dict[str, bool]:
    """Get the capability requirements for a given run mode."""
    return MODE_CAPABILITIES[mode]


def validate_mode_config(mode: RunMode, available_services: set[str]) -> list[str]:
    """Validate that required services are available for the given mode.

    Args:
        mode: The run mode to validate
        available_services: Set of available service names

    Returns:
        List of missing required services (empty if valid)
    """
    requirements = get_mode_requirements(mode)
    missing = []

    service_map = {
        "needs_audio_source": "audio_source",
        "needs_stt": "stt",
        "needs_llm": "llm",
        "needs_obs": "obs",
    }

    for req_key, service_name in service_map.items():
        if requirements.get(req_key) and service_name not in available_services:
            missing.append(service_name)

    return missing

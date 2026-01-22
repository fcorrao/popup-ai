"""Logfire observability configuration for popup-ai.

Provides high-value traces (LLM calls via pydantic-ai) while being
judicious with quota. Dashboard: https://logfire-us.pydantic.dev/fcorrao/popup-ai

Note: Ray actors run in separate processes, so each actor must call
ensure_logfire_configured() to set up Logfire in its process.
"""

import os
from typing import Any

import logfire
from logfire import SamplingOptions

_metrics: dict[str, Any] | None = None
_configured = False

# Environment variable names for passing config to actor processes
_ENV_SAMPLE_RATE = "POPUP_LOGFIRE_SAMPLE_RATE"
_ENV_ENVIRONMENT = "POPUP_LOGFIRE_ENVIRONMENT"


def configure_logfire(
    service_name: str = "popup-ai",
    environment: str = "development",
    sample_rate: float = 0.5,
) -> None:
    """Configure Logfire observability in the main process.

    Also sets environment variables so actor processes can pick up the same config.

    Args:
        service_name: Service name for traces
        environment: Environment name (development, production, etc.)
        sample_rate: Head sampling rate (0.0-1.0). Errors always captured.
    """
    global _configured

    # Set env vars for actor processes to inherit
    os.environ[_ENV_SAMPLE_RATE] = str(sample_rate)
    os.environ[_ENV_ENVIRONMENT] = environment

    logfire.configure(
        service_name=service_name,
        environment=environment,
        sampling=SamplingOptions(head=sample_rate),
    )
    # Instrument pydantic-ai for automatic LLM call tracing
    logfire.instrument_pydantic_ai()
    # Instrument httpx for custom client tracing (used by openai_compatible provider)
    logfire.instrument_httpx(capture_all=True)
    _init_metrics()
    _configured = True


def ensure_logfire_configured() -> None:
    """Ensure Logfire is configured in this process.

    Safe to call multiple times - will only configure once per process.
    Reads config from environment variables set by the main process.

    Call this in Ray actor __init__() methods to enable observability.
    """
    global _configured

    if _configured:
        return

    # Read config from environment (set by main process)
    sample_rate = float(os.environ.get(_ENV_SAMPLE_RATE, "0.5"))
    environment = os.environ.get(_ENV_ENVIRONMENT, "development")

    logfire.configure(
        service_name="popup-ai",
        environment=environment,
        sampling=SamplingOptions(head=sample_rate),
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx(capture_all=True)
    _init_metrics()
    _configured = True


def _init_metrics() -> None:
    """Initialize custom metrics for LLM monitoring."""
    global _metrics
    _metrics = {
        "llm_calls": logfire.metric_counter(
            "popup_ai.llm_calls",
            unit="1",
            description="Total LLM API calls",
        ),
        "llm_cache_hits": logfire.metric_counter(
            "popup_ai.llm_cache_hits",
            unit="1",
            description="LLM cache hits",
        ),
        "llm_latency": logfire.metric_histogram(
            "popup_ai.llm_latency_ms",
            unit="ms",
            description="LLM call latency in milliseconds",
        ),
    }


def get_metrics() -> dict[str, Any] | None:
    """Get the metrics dictionary.

    Returns:
        Dictionary of metric instruments, or None if not initialized.
    """
    return _metrics

"""Logfire observability configuration for popup-ai.

Provides high-value traces (LLM calls via pydantic-ai) while being
judicious with quota. Dashboard: https://logfire-us.pydantic.dev/fcorrao/popup-ai
"""

from typing import Any

import logfire
from logfire import SamplingOptions

_metrics: dict[str, Any] | None = None


def configure_logfire(
    service_name: str = "popup-ai",
    environment: str = "development",
    sample_rate: float = 0.5,
) -> None:
    """Configure Logfire observability.

    Args:
        service_name: Service name for traces
        environment: Environment name (development, production, etc.)
        sample_rate: Head sampling rate (0.0-1.0). Errors always captured.
    """
    logfire.configure(
        service_name=service_name,
        environment=environment,
        sampling=SamplingOptions(head=sample_rate),
    )
    # Instrument pydantic-ai for automatic LLM call tracing
    logfire.instrument_pydantic_ai()
    _init_metrics()


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

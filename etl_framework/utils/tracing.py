from __future__ import annotations
import contextlib
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace as _otel_trace
    _OTEL_AVAILABLE = True
except (ImportError, TypeError):
    _OTEL_AVAILABLE = False


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None):
    if _OTEL_AVAILABLE:
        tracer = _otel_trace.get_tracer("etl_framework")
        with tracer.start_as_current_span(name, attributes=attributes or {}) as otel_span:
            yield otel_span
    else:
        yield _NoOpSpan(name)


def is_tracing_enabled() -> bool:
    return _OTEL_AVAILABLE


class _NoOpSpan:
    def __init__(self, name: str) -> None:
        self.name = name

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

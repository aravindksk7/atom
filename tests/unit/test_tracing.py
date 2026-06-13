import pytest
from etl_framework.utils.tracing import span, is_tracing_enabled


def test_span_is_context_manager():
    with span("test.operation", attributes={"key": "value"}) as s:
        pass  # must not raise


def test_span_with_no_attributes():
    with span("test.operation") as s:
        pass  # must not raise


def test_span_returns_something():
    with span("test.operation") as s:
        assert s is not None


def test_span_exception_propagates():
    with pytest.raises(ValueError):
        with span("test.operation"):
            raise ValueError("expected error")


def test_is_tracing_enabled_returns_bool():
    result = is_tracing_enabled()
    assert isinstance(result, bool)


def test_noop_span_has_set_attribute():
    from etl_framework.utils.tracing import _NoOpSpan
    s = _NoOpSpan("test")
    s.set_attribute("k", "v")  # must not raise
    s.record_exception(ValueError("x"))  # must not raise

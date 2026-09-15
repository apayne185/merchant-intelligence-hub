"""
Tests for src/copilot/tracing.py — span creation, the request-scoped span
buffer, and error handling. Structure/attributes only, never timing values
(duration_ms is a real wall-clock measurement, so asserting on its exact
value would be flaky by construction — see the module docstring).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from src.copilot.tracing import JsonLinesFileExporter, _RequestSpanBuffer, get_trace, traced


def test_traced_yields_a_span_with_the_given_name() -> None:
    with traced("test.span") as span:
        assert span.is_recording()


def test_traced_sets_attributes() -> None:
    with traced("test.span", foo="bar", count=3) as span:
        assert span.attributes["foo"] == "bar"
        assert span.attributes["count"] == 3


def test_traced_reraises_exceptions() -> None:
    with pytest.raises(ValueError, match="boom"), traced("test.span"):
        raise ValueError("boom")


def test_get_trace_returns_spans_for_matching_trace_id_only() -> None:
    with traced("outer") as root:
        trace_id = root.get_span_context().trace_id
        with traced("inner"):
            pass

    spans = get_trace(trace_id)
    names = {s["name"] for s in spans}
    assert names == {"outer", "inner"}
    for s in spans:
        assert s["duration_ms"] >= 0


def test_get_trace_pops_spans_so_a_second_read_is_empty() -> None:
    with traced("once") as root:
        trace_id = root.get_span_context().trace_id

    assert len(get_trace(trace_id)) == 1
    assert get_trace(trace_id) == []


def test_get_trace_does_not_return_a_different_traces_spans() -> None:
    with traced("trace_a") as span_a:
        trace_id_a = span_a.get_span_context().trace_id
    with traced("trace_b"):
        pass

    spans = get_trace(trace_id_a)
    assert {s["name"] for s in spans} == {"trace_a"}


def test_request_span_buffer_evicts_oldest_trace_past_max_traces() -> None:
    # Unit-level test of the eviction backstop directly on the buffer
    # class, independent of the process-wide tracer/cache — constructing a
    # small buffer directly and feeding it 3 real, independently-traced
    # spans (each its own trace_id) avoids needing 256+ requests to
    # exercise the max_traces=256 default.
    buffer = _RequestSpanBuffer(max_traces=2)
    trace_ids = []
    for name in ("first", "second", "third"):
        with traced(name) as span:
            trace_ids.append(span.get_span_context().trace_id)
        # Exported after the span ends, matching how a real SpanProcessor
        # calls export() only on finished spans.
        buffer.export([span])

    assert len(buffer._by_trace) == 2
    # The first trace_id should have been evicted once a third distinct
    # one arrived.
    assert buffer.pop(format(trace_ids[0], "032x")) == []
    assert len(buffer.pop(format(trace_ids[2], "032x"))) == 1


# -----------------------------------------------------------------------------
# JsonLinesFileExporter — size cap/rotation. A prior bug: no cap existed at
# all, so COPILOT_TRACE_EXPORTER=file grew outputs/traces.jsonl forever
# over a long-running process, and the file wasn't even gitignored (a
# broad `git add outputs/` — this repo's own convention for eval reports —
# would have swept it in). See DECISIONS.md.
# -----------------------------------------------------------------------------
def test_json_lines_file_exporter_appends_below_cap(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    exporter = JsonLinesFileExporter(path)
    with traced("span_a") as span:
        pass
    exporter.export([span])
    with traced("span_b") as span:
        pass
    exporter.export([span])

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert not path.with_suffix(".jsonl.1").exists()


def test_json_lines_file_exporter_rotates_past_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.copilot.tracing._MAX_TRACE_FILE_BYTES", 10)
    path = tmp_path / "traces.jsonl"
    exporter = JsonLinesFileExporter(path)

    with traced("first_span") as span:
        pass
    exporter.export([span])  # writes past the tiny 10-byte cap immediately
    first_content = path.read_text()

    with traced("second_span") as span:
        pass
    exporter.export([span])  # this export should rotate before writing

    backup = path.with_suffix(".jsonl.1")
    assert backup.exists()
    assert backup.read_text() == first_content
    # The live file now holds only what was written after rotation.
    assert "second_span" in path.read_text()
    assert "first_span" not in path.read_text()


def test_json_lines_file_exporter_rotation_overwrites_prior_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.copilot.tracing._MAX_TRACE_FILE_BYTES", 10)
    path = tmp_path / "traces.jsonl"
    exporter = JsonLinesFileExporter(path)

    for name in ("span_1", "span_2", "span_3"):
        with traced(name) as span:
            pass
        exporter.export([span])

    backup = path.with_suffix(".jsonl.1")
    assert backup.exists()
    # Only one generation of backup is kept — a single .1, not .1/.2/.3.
    assert not path.with_suffix(".jsonl.2").exists()

"""Render a TestRun and its results as JUnit XML for CI test-report ingestion."""
from __future__ import annotations

import re
from datetime import timezone
from xml.etree import ElementTree as ET

# XML 1.0 disallows most C0 control characters. Tab (\x09), newline (\x0A),
# and carriage return (\x0D) are valid; everything else in \x00-\x1F is not.
# ElementTree does not sanitize these, so error/failure text sourced from
# arbitrary upstream messages (e.g. raw DB driver errors) can produce XML
# that downstream parsers (or ET.fromstring itself) reject.
_ILLEGAL_XML_CHARS_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize(text) -> str:
    if text is None:
        return text
    return _ILLEGAL_XML_CHARS_RE.sub("", str(text))


def _failure_message(result) -> str:
    return (
        f"value_mismatches={result.value_mismatch_count or 0} "
        f"missing_in_target={result.missing_in_target_count or 0} "
        f"missing_in_source={result.missing_in_source_count or 0}"
    )


def render_junit_xml(run) -> str:
    results = list(run.results)
    failures = sum(1 for r in results if r.effective_status == "FAILED")
    errors = sum(1 for r in results if r.effective_status == "ERROR")
    skipped = sum(1 for r in results if r.effective_status == "CANCELLED")
    total_time = sum(float(r.duration_seconds or 0.0) for r in results)

    suite = ET.Element("testsuite", {
        "name": f"atom-run-{run.run_id}",
        "tests": str(len(results)),
        "failures": str(failures),
        "errors": str(errors),
        "skipped": str(skipped),
        "time": f"{total_time:.3f}",
    })
    if run.started_at is not None:
        started_at = run.started_at
        # SQLite has no native tz-aware datetime type, so DateTime(timezone=True)
        # columns come back naive after a round-trip even when stored as UTC.
        # Assume naive timestamps are UTC so the rendered timestamp is always
        # offset-qualified, matching what a tz-aware backend (e.g. Postgres) returns.
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        suite.set("timestamp", started_at.isoformat())

    classname = f"atom.{run.source_env or 'run'}"
    for result in results:
        case = ET.SubElement(suite, "testcase", {
            "name": result.query_name,
            "classname": classname,
            "time": f"{float(result.duration_seconds or 0.0):.3f}",
        })
        status = result.effective_status
        if status == "FAILED":
            message = _sanitize(_failure_message(result))
            node = ET.SubElement(case, "failure", {
                "message": message,
                "type": "ReconciliationFailure",
            })
            node.text = _sanitize(result.error_message) or message
        elif status == "ERROR":
            message = _sanitize(result.error_message) or "execution error"
            node = ET.SubElement(case, "error", {
                "message": message,
                "type": "ExecutionError",
            })
            node.text = message
        elif status == "CANCELLED":
            ET.SubElement(case, "skipped")

    root = ET.Element("testsuites")
    root.append(suite)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)

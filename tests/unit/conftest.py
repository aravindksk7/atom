"""Unit-test fixtures and private-sample redaction hooks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Generator

import pytest

_REPO_SAMPLES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bo_layout"
_REDACTED_NOTICE = (
    "real-sample assertion failed; details in tests/fixtures/bo_layout/_local_failures.log (local only)"
)
_REAL_SAMPLE_FIXTURES = ("real_pdf", "corpus", "bo_expected")
# Tag on every real-sample skip reason. The summary counts skips by this tag
# from the reports the controller receives, so it also works under xdist
# (`-n auto`), where a module-level counter only grows in worker processes.
_REAL_SAMPLE_SKIP_TAG = "[real-sample]"


def _sample_dirs() -> list[Path]:
    dirs = [_REPO_SAMPLES_DIR]
    env_dir = os.environ.get("ATOM_BO_SAMPLES_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir))
    return dirs


def _find_sample_file(rel_path: str) -> Path | None:
    for base in _sample_dirs():
        candidate = base / rel_path
        if candidate.is_file():
            return candidate
    return None


def _missing_sample(reason: str) -> Any:
    if os.environ.get("ATOM_REQUIRE_REAL_SAMPLES") == "1":
        pytest.fail(reason)
    pytest.skip(f"{_REAL_SAMPLE_SKIP_TAG} {reason}")


def _load_expected_optional() -> dict[str, Any] | None:
    path = _find_sample_file("expected.json")
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def bo_expected() -> dict[str, Any]:
    """Load private BO sample expectations from expected.json."""
    data = _load_expected_optional()
    if data is None:
        _missing_sample("real-sample expected.json not available locally")
    return data


@pytest.fixture
def real_pdf() -> Callable[[str], Path]:
    """Resolve a real sample alias or filename to a local Path."""
    expected = _load_expected_optional() or {}
    files_map = expected.get("files") or {}

    def _resolve(name: str) -> Path:
        mapped = files_map.get(name, name)
        path = _find_sample_file(mapped)
        if path is None:
            _missing_sample(f"real sample '{name}' not available locally")
        return path

    return _resolve


def _resolve_corpus_entry(entry: dict[str, Any], corpus_dir: Path, sample_dir: Path) -> dict[str, Any]:
    resolved = dict(entry)
    for key in ("file", "twin", "xlsx"):
        val = resolved.get(key)
        if not val:
            resolved[key] = None
            continue
        candidate = corpus_dir / val
        if not candidate.is_file():
            alt = sample_dir / val
            candidate = alt if alt.is_file() else candidate
        if not candidate.is_file():
            _missing_sample(f"corpus sample '{key}' entry not available locally")
        resolved[key] = candidate.resolve()
    return resolved


@pytest.fixture
def corpus() -> Any:
    """Load corpus/manifest.json with file/twin/xlsx resolved to absolute Paths."""
    manifest_path = _find_sample_file("corpus/manifest.json")
    if manifest_path is None:
        _missing_sample("real-sample corpus/manifest.json not available locally")
    assert manifest_path is not None
    corpus_dir = manifest_path.parent
    sample_dir = corpus_dir.parent
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [_resolve_corpus_entry(item, corpus_dir, sample_dir) for item in data]
    if isinstance(data, dict):
        if any(k in data for k in ("file", "twin", "xlsx")):
            return _resolve_corpus_entry(data, corpus_dir, sample_dir)
        out = dict(data)
        for key, value in data.items():
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                out[key] = [_resolve_corpus_entry(item, corpus_dir, sample_dir) for item in value]
        return out
    return data


@pytest.fixture
def pdf_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate ATOM_PDF_SLOT_DIR and ATOM_PDF_CACHE_DIR per test."""
    slot_dir = tmp_path / "pdf_slots"
    cache_dir = tmp_path / "pdf_cache"
    slot_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATOM_PDF_SLOT_DIR", str(slot_dir))
    monkeypatch.setenv("ATOM_PDF_CACHE_DIR", str(cache_dir))
    return {"slot_dir": slot_dir, "cache_dir": cache_dir}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Generator[None, None, None]:
    outcome = yield
    report = outcome.get_result()
    fixturenames = getattr(item, "fixturenames", ())
    if not any(name in fixturenames for name in _REAL_SAMPLE_FIXTURES):
        return
    if report.failed:
        log_path = _REPO_SAMPLES_DIR / "_local_failures.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"=== {report.nodeid} ({report.when}) ===\n")
            fh.write(f"{report.longreprtext}\n")
            for title, content in report.sections:
                fh.write(f"--- {title} ---\n{content}\n")
        report.longrepr = _REDACTED_NOTICE
    report.sections.clear()


def _is_real_sample_skip(report: Any) -> bool:
    longrepr = getattr(report, "longrepr", None)
    # Skip reports carry longrepr as (path, lineno, "Skipped: <reason>").
    reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr or "")
    return _REAL_SAMPLE_SKIP_TAG in str(reason)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    skipped = sum(
        1 for report in terminalreporter.stats.get("skipped", []) if _is_real_sample_skip(report)
    )
    if skipped > 0:
        terminalreporter.write_line(
            f"{skipped} real-sample tests skipped "
            "(set ATOM_REQUIRE_REAL_SAMPLES=1 to fail instead)"
        )

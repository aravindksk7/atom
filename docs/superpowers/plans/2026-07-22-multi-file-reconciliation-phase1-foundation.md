# Multi-File Reconciliation — Phase 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a single reconciliation job discover N source files and M target files via glob/token patterns, pair them (1:1, 1:M-shard-collapse, or M:N across distinct keys), run the existing per-pair comparison engine once per pair, and roll the results up into one aggregate pass/fail with a per-pair breakdown — with zero DB migration and zero breaking change to any existing job.

**Architecture:** See the companion design document `2026-07-22-multi-file-reconciliation-architecture.md` for the full ASCII diagram, config schema, and phased roadmap. This plan implements only **Phase 1** of that roadmap: a new pure module (`etl_framework/reconciliation/file_mapping.py`) providing token-pattern discovery + explicit pairing + result aggregation, wired into `JobDefinition` validation, `validate_job_definition`, and `RunExecutor`. Local filesystem only; S3/SFTP, automated similarity-based mapping, parallel pair execution, and first-class reporting UI are later phases.

**Tech Stack:** Python 3, pytest, Pydantic (existing `JobDefinition`), pandas, existing `ReconciliationEngine`/`FrameEngine`.

**Spec coverage in this phase** (against the 4 requirement areas in the original request):
1. *Smart file mapping & pattern matching* — done for local filesystem: named-token regex (`{region}`, `{date:%Y%m%d}`) and bare glob (`*`, `?`) patterns, explicit key-based pairing (Tasks 1-3).
2. *Multi-source ingestion* — local filesystem only in this phase; live-spool (`bo_live`) and S3/SFTP discovery are Phase 3/5 (see roadmap doc §7).
3. *Config UX* — declarative `params.file_mapping` schema usable via the existing REST API (Task 6) and validated the same way whether it arrives from the CLI-triggered flow or the (not-yet-built) UI repeater (Phase 6). "Automated Mapping" (guessing pairs without explicit patterns) is Phase 2.
4. *Multi-file comparison engine + aggregated reporting* — done: loop over mapped pairs, existing `ReconciliationEngine` unchanged, new `aggregate_reconciliation_results` produces the "N of M pairs matched" roll-up (Tasks 5, 8). First-class UI/JUnit/HTML surfacing of the per-pair breakdown (beyond the JSON blob) is Phase 4.

---

### Task 1: Token-pattern compiler

**Files:**
- Create: `etl_framework/reconciliation/file_mapping.py`
- Test: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_file_mapping.py
from __future__ import annotations

from etl_framework.reconciliation.file_mapping import compile_token_pattern


def test_compile_token_pattern_matches_named_tokens() -> None:
    regex = compile_token_pattern("sales_data_{region}_{date:%Y%m%d}.csv")
    match = regex.match("sales_data_east_20260101.csv")
    assert match is not None
    assert match.group("region") == "east"
    assert match.group("date") == "20260101"


def test_compile_token_pattern_rejects_non_matching_names() -> None:
    regex = compile_token_pattern("sales_data_{region}_{date:%Y%m%d}.csv")
    assert regex.match("sales_data_east_2026-01-01.csv") is None
    assert regex.match("financials_east_20260101.dat") is None


def test_compile_token_pattern_supports_bare_glob_wildcards() -> None:
    regex = compile_token_pattern("sales_data_*.csv")
    assert regex.match("sales_data_20260101.csv") is not None
    assert regex.match("sales_data_20260101.csv").groupdict() == {}
    assert regex.match("sales_data_20260101.dat") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'etl_framework.reconciliation.file_mapping'`

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/reconciliation/file_mapping.py
"""Shared file discovery, pairing, and result-aggregation for multi-file
(1:M / M:N) reconciliation jobs.

This module owns the file-mapping logic that ``api/schemas.py``,
``etl_framework/runner/job_validation.py``, and ``api/services/run_executor.py``
all need, so those three call sites share one implementation instead of each
re-deriving the same "source_mode" file-path rules (see the architecture doc
in docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md
for why that triplication existed before this module).
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\{(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)(?::(?P<spec>[^}]*))?\}")

_STRFTIME_DIGIT_WIDTH = {"%Y": 4, "%m": 2, "%d": 2, "%H": 2, "%M": 2, "%S": 2}


def _spec_to_regex(spec: str | None) -> str:
    if not spec:
        return r"[^_./\\]+"
    out: list[str] = []
    i = 0
    while i < len(spec):
        two = spec[i:i + 2]
        if two in _STRFTIME_DIGIT_WIDTH:
            out.append(r"\d{%d}" % _STRFTIME_DIGIT_WIDTH[two])
            i += 2
        else:
            out.append(re.escape(spec[i]))
            i += 1
    return "".join(out)


def _glob_segment_to_regex(segment: str) -> str:
    """Translate bare glob characters (``*``, ``?``) outside any ``{token}``
    into regex, escaping everything else literally."""
    out: list[str] = []
    for ch in segment:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def compile_token_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a filename pattern into a named-group regex.

    Two placeholder kinds are supported and may be mixed:
    - ``{token}`` / ``{token:%Y%m%d}`` -- a named capture group used for
      pairing (see ``pair_files``). ``%Y``/``%m``/``%d``/``%H``/``%M``/``%S``
      in the spec become fixed-width digit groups; any other spec text is
      matched literally.
    - bare ``*`` / ``?`` -- plain glob wildcards, for patterns that need
      dynamic discovery but no pairing key (see ``FileMappingSpec``).
    """
    regex_parts: list[str] = []
    pos = 0
    for match in _TOKEN_RE.finditer(pattern):
        regex_parts.append(_glob_segment_to_regex(pattern[pos:match.start()]))
        name = match.group("name")
        spec = match.group("spec")
        regex_parts.append(f"(?P<{name}>{_spec_to_regex(spec)})")
        pos = match.end()
    regex_parts.append(_glob_segment_to_regex(pattern[pos:]))
    return re.compile("^" + "".join(regex_parts) + "$")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): add token-pattern compiler for multi-file discovery"
```

---

### Task 2: Local file discovery

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_file_mapping.py`:

```python
from etl_framework.reconciliation.file_mapping import discover_local_files


def test_discover_local_files_matches_pattern_and_extracts_tokens(tmp_path) -> None:
    (tmp_path / "sales_data_east_20260101.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    (tmp_path / "sales_data_west_20260102.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("not a match", encoding="utf-8")

    discovered = discover_local_files(tmp_path, "sales_data_{region}_{date:%Y%m%d}.csv")

    assert [f.file_name for f in discovered] == [
        "sales_data_east_20260101.csv",
        "sales_data_west_20260102.csv",
    ]
    assert discovered[0].tokens == {"region": "east", "date": "20260101"}


def test_discover_local_files_returns_empty_list_when_nothing_matches(tmp_path) -> None:
    (tmp_path / "unrelated.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    assert discover_local_files(tmp_path, "sales_data_{region}.csv") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_local_files'`

- [ ] **Step 3: Write minimal implementation**

Append to `etl_framework/reconciliation/file_mapping.py`:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredFile:
    path: str
    file_name: str
    tokens: dict[str, str]


def discover_local_files(root: Path, pattern: str) -> list[DiscoveredFile]:
    """Match every file directly under ``root`` against ``pattern``.

    ``root`` must already be a trusted, resolved directory -- callers outside
    this module (e.g. ``RunExecutor``) are responsible for allow-listing it
    first (see ``api.services.file_source.resolve_allowed_path``), the same
    way every other file-backed job resolves paths today.
    """
    regex = compile_token_pattern(pattern)
    discovered: list[DiscoveredFile] = []
    for candidate in sorted(Path(root).iterdir()):
        if not candidate.is_file():
            continue
        match = regex.match(candidate.name)
        if match is None:
            continue
        discovered.append(DiscoveredFile(
            path=str(candidate),
            file_name=candidate.name,
            tokens=match.groupdict(),
        ))
    return discovered
```

Move the `from dataclasses import dataclass` and `from pathlib import Path` imports up to the top of the file, alongside the existing `import re`, so the module has one import block. The top of `etl_framework/reconciliation/file_mapping.py` should read:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): add local filesystem discovery for multi-file jobs"
```

---

### Task 3: Grouping and pairing (1:1, shard collapse, M:N)

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_file_mapping.py`:

```python
import pytest

from etl_framework.reconciliation.file_mapping import pair_files


def _df(name: str, **tokens: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens=tokens)


def test_pair_files_matches_one_to_one() -> None:
    sources = [_df("sales_east_20260101.csv", region="east", date="20260101")]
    targets = [_df("fin_east_20260101.dat", region="east", date="20260101")]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert mapping.pairs[0].key == ("east", "20260101")
    assert not mapping.unmatched_sources
    assert not mapping.unmatched_targets


def test_pair_files_collapses_shards_sharing_a_key_into_one_group() -> None:
    sources = [
        _df("sales_east_p1_20260101.csv", region="east", date="20260101"),
        _df("sales_east_p2_20260101.csv", region="east", date="20260101"),
    ]
    targets = [_df("fin_east_20260101.dat", region="east", date="20260101")]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert len(mapping.pairs[0].source.files) == 2
    assert len(mapping.pairs[0].target.files) == 1


def test_pair_files_reports_unmatched_groups_on_either_side() -> None:
    sources = [
        _df("sales_east_20260101.csv", region="east", date="20260101"),
        _df("sales_north_20260101.csv", region="north", date="20260101"),
    ]
    targets = [
        _df("fin_east_20260101.dat", region="east", date="20260101"),
        _df("fin_west_20260101.dat", region="west", date="20260101"),
    ]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert [g.key for g in mapping.unmatched_sources] == [("north", "20260101")]
    assert [g.key for g in mapping.unmatched_targets] == [("west", "20260101")]


def test_pair_files_raises_when_a_file_is_missing_a_match_on_token() -> None:
    sources = [_df("sales_east.csv", region="east")]  # no "date" token captured

    with pytest.raises(ValueError, match="missing match_on token"):
        pair_files(sources, [], ["region", "date"])


def test_pair_files_with_empty_match_on_collapses_every_file_into_one_group_per_side() -> None:
    sources = [_df("sales_data_20260101.csv"), _df("sales_data_20260102.csv")]
    targets = [_df("fin_data_20260101.dat")]

    mapping = pair_files(sources, targets, [])

    assert len(mapping.pairs) == 1
    assert mapping.pairs[0].key == ()
    assert len(mapping.pairs[0].source.files) == 2
    assert len(mapping.pairs[0].target.files) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ImportError: cannot import name 'pair_files'`

- [ ] **Step 3: Write minimal implementation**

Append to `etl_framework/reconciliation/file_mapping.py`:

```python
from typing import Sequence


@dataclass(frozen=True)
class FileGroup:
    key: tuple[str, ...]
    files: list[DiscoveredFile]


@dataclass(frozen=True)
class FilePair:
    key: tuple[str, ...]
    source: FileGroup
    target: FileGroup


@dataclass(frozen=True)
class FileMappingResult:
    match_on: tuple[str, ...]
    pairs: list[FilePair]
    unmatched_sources: list[FileGroup]
    unmatched_targets: list[FileGroup]


def _group_by_key(
    files: list[DiscoveredFile], match_on: Sequence[str]
) -> dict[tuple[str, ...], FileGroup]:
    buckets: dict[tuple[str, ...], list[DiscoveredFile]] = {}
    for discovered in files:
        try:
            key = tuple(discovered.tokens[name] for name in match_on)
        except KeyError as exc:
            raise ValueError(
                f"file '{discovered.file_name}' matched the pattern but is "
                f"missing match_on token {exc}"
            ) from exc
        buckets.setdefault(key, []).append(discovered)
    return {
        key: FileGroup(key=key, files=sorted(group, key=lambda d: d.file_name))
        for key, group in buckets.items()
    }


def pair_files(
    source_files: list[DiscoveredFile],
    target_files: list[DiscoveredFile],
    match_on: Sequence[str],
) -> FileMappingResult:
    """Group each side's discovered files by the values of ``match_on``
    tokens, then join the two sides on that key. A key present on both
    sides becomes one ``FilePair`` (possibly many files per side, if several
    shards share a key); a key present on only one side is reported as
    unmatched. An empty ``match_on`` collapses every discovered file on a
    side into a single group (key ``()``), for patterns that need dynamic
    discovery but no pairing key at all.
    """
    source_groups = _group_by_key(source_files, match_on)
    target_groups = _group_by_key(target_files, match_on)

    pairs: list[FilePair] = []
    unmatched_sources: list[FileGroup] = []
    unmatched_targets: list[FileGroup] = []

    for key in sorted(set(source_groups) | set(target_groups)):
        source_group = source_groups.get(key)
        target_group = target_groups.get(key)
        if source_group is not None and target_group is not None:
            pairs.append(FilePair(key=key, source=source_group, target=target_group))
        elif source_group is not None:
            unmatched_sources.append(source_group)
        else:
            assert target_group is not None
            unmatched_targets.append(target_group)

    return FileMappingResult(
        match_on=tuple(match_on),
        pairs=pairs,
        unmatched_sources=unmatched_sources,
        unmatched_targets=unmatched_targets,
    )
```

Add `from typing import Sequence` to the top import block (next to the existing `from pathlib import Path`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): add file grouping and pairing for 1:M and M:N jobs"
```

---

### Task 4: Declarative config parsing (`FileMappingSpec`)

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_file_mapping.py`:

```python
from etl_framework.reconciliation.file_mapping import FileMappingSpec, FileSourceSpec


def test_file_mapping_spec_parses_valid_explicit_config() -> None:
    params = {
        "file_mapping": {
            "strategy": "explicit",
            "match_on": ["region", "date"],
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}_{date}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}_{date}.dat"},
        }
    }

    spec = FileMappingSpec.from_params(params)

    assert spec.match_on == ("region", "date")
    assert spec.source == FileSourceSpec(kind="local", root="/spool", pattern="sales_{region}_{date}.csv")
    assert spec.target == FileSourceSpec(kind="local", root="/baseline", pattern="fin_{region}_{date}.dat")
    assert spec.unmatched_policy == "fail"


def test_file_mapping_spec_defaults_match_on_to_empty_tuple() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_data_*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_data_*.dat"},
        }
    })

    assert spec.match_on == ()


def test_file_mapping_spec_requires_file_mapping_object() -> None:
    with pytest.raises(ValueError, match="require a 'file_mapping' object"):
        FileMappingSpec.from_params({})


def test_file_mapping_spec_requires_source_and_target() -> None:
    with pytest.raises(ValueError, match="file_mapping.source requires"):
        FileMappingSpec.from_params({
            "file_mapping": {"target": {"kind": "local", "root": "/baseline", "pattern": "fin.csv"}}
        })


def test_file_mapping_spec_rejects_unknown_unmatched_policy() -> None:
    with pytest.raises(ValueError, match="unmatched_policy must be"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
                "unmatched_policy": "retry",
            }
        })


def test_file_mapping_spec_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="is not supported yet"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            }
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ImportError: cannot import name 'FileMappingSpec'`

- [ ] **Step 3: Write minimal implementation**

Append to `etl_framework/reconciliation/file_mapping.py`:

```python
from typing import Any


@dataclass(frozen=True)
class FileSourceSpec:
    kind: str
    root: str
    pattern: str


@dataclass(frozen=True)
class FileMappingSpec:
    strategy: str
    match_on: tuple[str, ...]
    source: FileSourceSpec
    target: FileSourceSpec
    unmatched_policy: str = "fail"

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "FileMappingSpec":
        raw = params.get("file_mapping")
        if not isinstance(raw, dict):
            raise ValueError(
                "multi_file reconciliation jobs require a 'file_mapping' object in params"
            )
        strategy = raw.get("strategy", "explicit")
        if strategy != "explicit":
            raise ValueError(
                f"file_mapping.strategy '{strategy}' is not supported yet; "
                "only 'explicit' is implemented"
            )
        match_on = tuple(raw.get("match_on") or [])
        source = _parse_file_source(raw.get("source"), "source")
        target = _parse_file_source(raw.get("target"), "target")
        unmatched_policy = raw.get("unmatched_policy", "fail")
        if unmatched_policy not in ("fail", "warn", "ignore"):
            raise ValueError(
                "file_mapping.unmatched_policy must be 'fail', 'warn', or "
                f"'ignore', got {unmatched_policy!r}"
            )
        return cls(
            strategy=strategy,
            match_on=match_on,
            source=source,
            target=target,
            unmatched_policy=unmatched_policy,
        )


def _parse_file_source(raw: Any, side: str) -> FileSourceSpec:
    if not isinstance(raw, dict):
        raise ValueError(
            f"file_mapping.{side} requires an object with 'kind', 'root', and 'pattern'"
        )
    kind = raw.get("kind", "local")
    if kind != "local":
        raise ValueError(
            f"file_mapping.{side}.kind '{kind}' is not supported yet; "
            "only 'local' is implemented in this phase"
        )
    root = raw.get("root")
    pattern = raw.get("pattern")
    if not root or not pattern:
        raise ValueError(f"file_mapping.{side} requires both 'root' and 'pattern'")
    return FileSourceSpec(kind=kind, root=root, pattern=pattern)
```

Add `from typing import Any, Sequence` (merge with the existing `Sequence` import) to the top import block.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): parse declarative file_mapping config from job params"
```

---

### Task 5: Aggregate rollup (`aggregate_reconciliation_results`)

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_file_mapping.py`:

```python
from datetime import datetime, timezone

from etl_framework.reconciliation.file_mapping import (
    FileGroup,
    FileMappingResult,
    FilePair,
    aggregate_reconciliation_results,
)
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


def _pair_result(status: TestStatus, mismatches: list[MismatchRecord] | None = None) -> ReconciliationResult:
    return ReconciliationResult(
        query_name="pair",
        source_env="source",
        target_env="target",
        source_row_count=2,
        target_row_count=2,
        matched_count=2 if status == TestStatus.PASSED else 1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=0 if status == TestStatus.PASSED else 1,
        mismatches=mismatches or [],
        status=status,
        executed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        duration_seconds=0.1,
    )


def test_aggregate_reconciliation_results_rolls_up_pairs() -> None:
    east_source = FileGroup(key=("east",), files=[_df("e.csv", region="east")])
    east_target = FileGroup(key=("east",), files=[_df("e.dat", region="east")])
    west_source = FileGroup(key=("west",), files=[_df("w.csv", region="west")])
    west_target = FileGroup(key=("west",), files=[_df("w.dat", region="west")])
    mapping = FileMappingResult(
        match_on=("region",),
        pairs=[
            FilePair(key=("east",), source=east_source, target=east_target),
            FilePair(key=("west",), source=west_source, target=west_target),
        ],
        unmatched_sources=[],
        unmatched_targets=[],
    )
    mismatch = MismatchRecord(
        key_values={"id": 1}, column_name="value", source_value="charlie",
        target_value="zulu", mismatch_type="value_diff",
    )
    pair_results = [_pair_result(TestStatus.PASSED), _pair_result(TestStatus.FAILED, [mismatch])]

    aggregate = aggregate_reconciliation_results("regional_sales_recon", mapping, pair_results)

    assert aggregate.status == TestStatus.FAILED
    assert aggregate.source_row_count == 4
    assert aggregate.mismatch_summary["pairs_total"] == 2
    assert aggregate.mismatch_summary["pairs_passed"] == 1
    assert aggregate.mismatch_summary["pairs_failed"] == 1
    assert aggregate.mismatches[0].key_values["__pair__"] == {"region": "west"}
    assert aggregate.source_file_name == "2 file(s) across 2 pair(s)"

    pair_summary_by_region = {p["key"]["region"]: p for p in aggregate.mismatch_summary["file_pairs"]}
    assert pair_summary_by_region["east"]["status"] == "PASSED"
    assert pair_summary_by_region["west"]["status"] == "FAILED"


def test_aggregate_reconciliation_results_rejects_length_mismatch() -> None:
    mapping = FileMappingResult(match_on=("region",), pairs=[], unmatched_sources=[], unmatched_targets=[])

    with pytest.raises(ValueError, match="one result per mapped pair"):
        aggregate_reconciliation_results("job", mapping, [_pair_result(TestStatus.PASSED)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate_reconciliation_results'`

- [ ] **Step 3: Write minimal implementation**

Append to `etl_framework/reconciliation/file_mapping.py`:

```python
import dataclasses
from datetime import datetime, timezone

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


def _group_summary(group: FileGroup, match_on: tuple[str, ...]) -> dict[str, Any]:
    return {
        "key": dict(zip(match_on, group.key)),
        "files": [f.file_name for f in group.files],
    }


def aggregate_reconciliation_results(
    job_name: str,
    mapping: FileMappingResult,
    pair_results: list[ReconciliationResult],
) -> ReconciliationResult:
    """Roll a list of per-pair ``ReconciliationResult``s (one per
    ``mapping.pairs`` entry, in the same order) up into a single aggregate
    result, the same shape ``RunExecutor`` already persists as one
    ``TestResult`` row per job. The per-pair breakdown -- and the unmatched
    groups from ``mapping`` -- are embedded in ``mismatch_summary`` so no
    database migration or change to any existing report consumer is needed
    in this phase.
    """
    if len(pair_results) != len(mapping.pairs):
        raise ValueError(
            f"aggregate_reconciliation_results requires one result per mapped "
            f"pair, got {len(pair_results)} results for {len(mapping.pairs)} pairs"
        )

    all_mismatches: list[MismatchRecord] = []
    pair_summaries: list[dict[str, Any]] = []
    pairs_passed = 0
    for pair, result in zip(mapping.pairs, pair_results):
        pair_key = dict(zip(mapping.match_on, pair.key))
        for mismatch in result.mismatches:
            all_mismatches.append(dataclasses.replace(
                mismatch,
                key_values={"__pair__": pair_key, **mismatch.key_values},
            ))
        if result.status == TestStatus.PASSED:
            pairs_passed += 1
        pair_summaries.append({
            "key": pair_key,
            "status": result.status.value,
            "source_files": [f.file_name for f in pair.source.files],
            "target_files": [f.file_name for f in pair.target.files],
            "source_row_count": result.source_row_count,
            "target_row_count": result.target_row_count,
            "matched_count": result.matched_count,
            "missing_in_target_count": result.missing_in_target_count,
            "missing_in_source_count": result.missing_in_source_count,
            "value_mismatch_count": result.value_mismatch_count,
        })

    total_pairs = len(mapping.pairs)
    total_source_files = sum(len(p.source.files) for p in mapping.pairs)
    total_target_files = sum(len(p.target.files) for p in mapping.pairs)

    return ReconciliationResult(
        query_name=job_name,
        source_env=pair_results[0].source_env if pair_results else "",
        target_env=pair_results[0].target_env if pair_results else "",
        source_row_count=sum(r.source_row_count for r in pair_results),
        target_row_count=sum(r.target_row_count for r in pair_results),
        matched_count=sum(r.matched_count for r in pair_results),
        missing_in_target_count=sum(r.missing_in_target_count for r in pair_results),
        missing_in_source_count=sum(r.missing_in_source_count for r in pair_results),
        value_mismatch_count=sum(r.value_mismatch_count for r in pair_results),
        mismatches=all_mismatches,
        status=TestStatus.PASSED if pairs_passed == total_pairs else TestStatus.FAILED,
        executed_at=min((r.executed_at for r in pair_results), default=datetime.now(timezone.utc)),
        duration_seconds=sum(r.duration_seconds for r in pair_results),
        mismatch_summary={
            "file_pairs": pair_summaries,
            "unmatched_sources": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_sources],
            "unmatched_targets": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_targets],
            "pairs_total": total_pairs,
            "pairs_passed": pairs_passed,
            "pairs_failed": total_pairs - pairs_passed,
        },
        source_file_name=f"{total_source_files} file(s) across {total_pairs} pair(s)",
        target_file_name=f"{total_target_files} file(s) across {total_pairs} pair(s)",
    )
```

`import pytest` is already at the top of `tests/unit/test_file_mapping.py` from Task 3 — no import changes needed for this task's test code.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): aggregate per-pair results into one job-level rollup"
```

---

### Task 6: Wire `JobDefinition` schema validation

**Files:**
- Modify: `api/schemas.py:455-478` (inside `validate_reconciliation_contract`)
- Test: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_multi_file_jobs.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def test_multi_file_job_requires_file_mapping() -> None:
    with pytest.raises(ValidationError, match="require a 'file_mapping' object"):
        JobDefinition(
            name="regional_sales_recon",
            job_type="reconciliation",
            query="",
            key_columns=["id"],
            params={"source_mode": "multi_file"},
        )


def test_multi_file_job_accepts_valid_file_mapping() -> None:
    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            },
        },
    )

    assert job.params["source_mode"] == "multi_file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: FAIL — `test_multi_file_job_requires_file_mapping` raises nothing (falls through to the generic `else` branch and passes cleanly instead of raising), `test_multi_file_job_accepts_valid_file_mapping`'s job also fails today because the `else` branch demands a non-empty `query`.

- [ ] **Step 3: Write minimal implementation**

In `api/schemas.py`, inside `validate_reconciliation_contract` (around line 456-467), add a `multi_file` branch between the `bo_live` branch and the `files` branch:

```python
        elif self.job_type == "reconciliation":
            source_mode = self.params.get("source_mode")
            if source_mode == "bo_live":
                if not self.params.get("report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'report_id' in params")
                if not self.params.get("bo_report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'bo_report_id' in params")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "target"):
                    raise ValueError("bo_live reconciliation jobs require a target file")
                # key_columns is optional: RunExecutor infers a shared ID column,
                # or falls back to positional row matching.
            elif source_mode == "multi_file":
                from etl_framework.reconciliation.file_mapping import FileMappingSpec
                FileMappingSpec.from_params(self.params)
            elif (
                source_mode == "files"
                or _has_job_file_source(self.params, "source")
                or _has_job_file_source(self.params, "target")
            ):
```

(The rest of the `elif` chain — the `files` branch and the final `else` — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(api): validate multi_file source_mode via shared FileMappingSpec"
```

---

### Task 7: Wire `validate_job_definition` (job-validation endpoint)

**Files:**
- Modify: `etl_framework/runner/job_validation.py:29-52`
- Modify: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_multi_file_jobs.py`:

```python
from etl_framework.runner.job_validation import validate_job_definition


def test_validate_job_definition_flags_missing_file_mapping() -> None:
    issues = validate_job_definition({
        "name": "regional_sales_recon",
        "job_type": "reconciliation",
        "params": {"source_mode": "multi_file"},
    })

    assert any("file_mapping" in issue.field for issue in issues)


def test_validate_job_definition_accepts_valid_multi_file_config() -> None:
    issues = validate_job_definition({
        "name": "regional_sales_recon",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            },
        },
    })

    assert issues == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: FAIL — `test_validate_job_definition_flags_missing_file_mapping` gets an empty `issues` list instead (falls through to the generic `else` branch, which only checks `query`/`key_columns` and passes because `key_columns` isn't checked when `query` is also empty... in this case it raises a *different*, misleading issue about `query`/`key_columns` rather than about `file_mapping`), so the `any(...)` assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `etl_framework/runner/job_validation.py`, inside `validate_job_definition` (around line 29-41), add a `multi_file` branch between the `bo_live` branch and the `files` branch:

```python
    if job_type == "reconciliation":
        source_mode = params.get("source_mode")
        if source_mode == "bo_live":
            if not params.get("report_id"):
                issues.append(ValidationIssue("params.report_id", "bo_live reconciliation jobs require report_id"))
            if not params.get("bo_report_id"):
                issues.append(ValidationIssue("params.bo_report_id", "bo_live reconciliation jobs require bo_report_id"))
            _validate_file_source(params, "target", issues)
            if not _has_file_source(params, "target"):
                issues.append(ValidationIssue("params", "bo_live reconciliation jobs require a target file"))
            # key_columns is optional -- RunExecutor infers a shared ID column
            # or falls back to positional row matching.
        elif source_mode == "multi_file":
            from etl_framework.reconciliation.file_mapping import FileMappingSpec
            try:
                FileMappingSpec.from_params(params)
            except ValueError as exc:
                issues.append(ValidationIssue("params.file_mapping", str(exc)))
        elif source_mode == "files" or _has_file_source(params, "source") or _has_file_source(params, "target"):
```

(The rest of the `elif` chain is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(runner): validate multi_file jobs via the job-validation endpoint"
```

---

### Task 8: Wire `RunExecutor` end-to-end

**Files:**
- Modify: `api/services/file_source.py` (add a public wrapper next to `_resolve_allowed_path`, line 459)
- Modify: `api/services/run_executor.py:441-467` (`_build_case` dispatch) and after line 581 (new method)
- Modify: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test (public path resolver)**

Append to `tests/unit/test_multi_file_jobs.py`:

```python
def test_resolve_allowed_path_is_publicly_importable(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    resolved = file_source.resolve_allowed_path(str(tmp_path / "sub"))

    assert resolved == (tmp_path / "sub").resolve()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: FAIL with `AttributeError: module 'api.services.file_source' has no attribute 'resolve_allowed_path'`

- [ ] **Step 3: Add the public wrapper**

In `api/services/file_source.py`, immediately after the `_resolve_allowed_path` function (ends at line 459, right before `def read_tabular`), add:

```python
def resolve_allowed_path(path: str) -> Path:
    """Public entry point for callers outside this module (e.g. the
    multi-file discovery resolver in etl_framework.reconciliation.file_mapping)
    that need the same server-side directory allow-listing ``read_tabular``
    already enforces, without duplicating it."""
    return _resolve_allowed_path(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing end-to-end test**

Append to `tests/unit/test_multi_file_jobs.py`:

```python
from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.runner.state import TestStatus


def test_run_executor_multi_file_reconciliation_two_pairs(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")
    (source_dir / "sales_data_west_20260101.csv").write_text("id,value\n1,charlie\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")
    (target_dir / "financials_west_20260101.dat").write_text("id,value\n1,zulu\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local",
                    "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                },
                "target": {
                    "kind": "local",
                    "root": str(target_dir),
                    "pattern": "financials_{region}_{date:%Y%m%d}.dat",
                },
                "unmatched_policy": "fail",
            },
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.FAILED
    assert result.mismatch_summary["pairs_total"] == 2
    assert result.mismatch_summary["pairs_passed"] == 1
    assert result.mismatch_summary["pairs_failed"] == 1
    by_region = {p["key"]["region"]: p for p in result.mismatch_summary["file_pairs"]}
    assert by_region["east"]["status"] == "PASSED"
    assert by_region["west"]["status"] == "FAILED"
    assert result.source_file_name == "2 file(s) across 2 pair(s)"


def test_run_executor_multi_file_reconciliation_fails_fast_on_unmatched_by_default(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    # No matching target file for "east" -- unmatched source group.

    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local",
                    "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                },
                "target": {
                    "kind": "local",
                    "root": str(target_dir),
                    "pattern": "financials_{region}_{date:%Y%m%d}.dat",
                },
            },
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    with pytest.raises(ValueError, match="unmatched source group"):
        executor._build_case(job)()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: FAIL — `_build_case` never reaches a `multi_file`-aware branch, so it falls into the generic SQL `run_job()` closure and errors with an unrelated `KeyError`/empty-query failure rather than producing the expected aggregate result.

- [ ] **Step 7: Wire `RunExecutor`**

In `api/services/run_executor.py`, inside `_build_case` (line 441), add a branch immediately before the existing `bo_live` check (line 458):

```python
    def _build_case(self, job: JobDefinition):
        if job.job_type == "freshness":
            return self._build_case_freshness(job)
        if job.job_type == "schema_snapshot":
            return self._build_case_schema_snapshot(job)
        if job.job_type == "profile":
            return self._build_case_profile(job)
        if job.job_type == "cross_job_assertion":
            return self._build_case_cross_job(job)
        if job.job_type == "dbt_artifact":
            return self._build_case_dbt(job)
        if job.job_type == "bo_report" and self._settings.use_live_connections:
            return self._build_case_bo_report(job)
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)
        if job.job_type == "api_reconciliation" and self._settings.use_live_connections:
            return self._build_case_api_reconciliation(job)
        if job.job_type == "reconciliation" and job.params.get("source_mode") == "multi_file":
            return self._build_case_multi_file_reconciliation(job)
        if job.job_type == "reconciliation" and job.params.get("source_mode") == "bo_live":
```

(Everything from the `bo_live` `if` onward is unchanged.)

Then add the new method directly after `_build_case_bo_live_recon` ends (after line 581, before `_run_reconciliation_job`):

```python
    def _build_case_multi_file_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from api.services.file_source import read_tabular, resolve_allowed_path
            from etl_framework.reconciliation.file_mapping import (
                FileMappingSpec,
                aggregate_reconciliation_results,
                discover_local_files,
                pair_files,
            )

            spec = FileMappingSpec.from_params(job.params)
            source_root = resolve_allowed_path(spec.source.root)
            target_root = resolve_allowed_path(spec.target.root)
            source_files = discover_local_files(source_root, spec.source.pattern)
            target_files = discover_local_files(target_root, spec.target.pattern)
            mapping = pair_files(source_files, target_files, spec.match_on)

            if mapping.unmatched_sources or mapping.unmatched_targets:
                if spec.unmatched_policy == "fail":
                    raise ValueError(
                        f"multi_file reconciliation for '{job.name}' has "
                        f"{len(mapping.unmatched_sources)} unmatched source group(s) and "
                        f"{len(mapping.unmatched_targets)} unmatched target group(s); "
                        "set file_mapping.unmatched_policy to 'warn' or 'ignore' to proceed anyway"
                    )
                if spec.unmatched_policy == "warn":
                    logger.warning(
                        "multi_file reconciliation for '%s' proceeding with %d unmatched "
                        "source group(s) and %d unmatched target group(s)",
                        job.name, len(mapping.unmatched_sources), len(mapping.unmatched_targets),
                    )

            if not mapping.pairs:
                raise ValueError(f"multi_file reconciliation for '{job.name}' matched zero file pairs")

            pair_results: list[ReconciliationResult] = []
            for pair in mapping.pairs:
                source_df = pd.concat(
                    [read_tabular(path=f.path, file_name=f.file_name) for f in pair.source.files],
                    ignore_index=True,
                )
                target_df = pd.concat(
                    [read_tabular(path=f.path, file_name=f.file_name) for f in pair.target.files],
                    ignore_index=True,
                )
                source_df, target_df, resolved_keys = resolve_key_columns(
                    source_df,
                    target_df,
                    job.key_columns or self._settings.key_columns,
                    job.exclude_columns or [],
                )
                pair_job = job.model_copy(update={"key_columns": resolved_keys})
                source_label = "/".join(f.file_name for f in pair.source.files)
                target_label = "/".join(f.file_name for f in pair.target.files)
                source_engine = FrameEngine(source_df, source_label)
                target_engine = FrameEngine(target_df, target_label)
                pair_results.append(self._run_reconciliation_job(
                    pair_job,
                    source_engine,
                    target_engine,
                    query=FILE_SOURCE_QUERY,
                    params={},
                    chunk_size=0,
                    use_hash_precheck=False,
                ))

            return aggregate_reconciliation_results(job.name, mapping, pair_results)
        return run_job
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (6 tests)

- [ ] **Step 9: Run the full unit suite to confirm no regression**

Run: `pytest tests/unit -v`
Expected: PASS — all previously passing tests (including `tests/unit/test_file_backed_jobs.py` and `tests/unit/test_bo_live_reconciliation.py`) still pass unchanged, since the new branch in `_build_case` only triggers for `source_mode == "multi_file"` and every other dispatch condition is untouched.

- [ ] **Step 10: Commit**

```bash
git add api/services/file_source.py api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(run-executor): execute multi_file reconciliation jobs end to end"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` (already exists — add a pointer to Phase 1's completion)
- Create: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Write the user-facing doc**

```markdown
# Multi-file reconciliation jobs

A reconciliation job can compare more than one file per side by setting
`params.source_mode` to `"multi_file"` and providing a `params.file_mapping`
block. See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
for the full design; this page is the quick reference.

## Minimal example

\`\`\`json
{
  "name": "regional_sales_recon",
  "job_type": "reconciliation",
  "key_columns": ["id"],
  "params": {
    "source_mode": "multi_file",
    "file_mapping": {
      "match_on": ["region", "date"],
      "source": {
        "kind": "local",
        "root": "/spool/bo_exports",
        "pattern": "sales_data_{region}_{date:%Y%m%d}.csv"
      },
      "target": {
        "kind": "local",
        "root": "/exports/finance/sales",
        "pattern": "financials_{region}_{date:%Y%m%d}.dat"
      },
      "unmatched_policy": "fail"
    }
  }
}
\`\`\`

## How pairing works

- Every `{token}` in a pattern becomes a named capture group; `{date:%Y%m%d}`
  additionally constrains it to 8 digits.
- Files are grouped per side by the tuple of `match_on` token values. A key
  present on both sides becomes one comparison pair; several files sharing a
  key on one side (e.g. sharded exports) are concatenated into a single
  dataset for that side before comparison.
- `match_on` may be omitted (or empty) for jobs that only need dynamic
  discovery, not pairing — every matched file on a side collapses into one
  group, e.g. `pattern: "sales_data_*.csv"`.
- `unmatched_policy` controls what happens when a key exists on only one
  side: `fail` (default) aborts the job, `warn` proceeds and logs it, `ignore`
  proceeds silently.

## Result shape

The job produces one aggregate result, same as any other reconciliation job.
`mismatch_summary` carries the per-pair breakdown:

\`\`\`json
{
  "pairs_total": 2,
  "pairs_passed": 1,
  "pairs_failed": 1,
  "file_pairs": [
    {"key": {"region": "east", "date": "20260101"}, "status": "PASSED", "...": "..."},
    {"key": {"region": "west", "date": "20260101"}, "status": "FAILED", "...": "..."}
  ],
  "unmatched_sources": [],
  "unmatched_targets": []
}
\`\`\`

## Current limitations (Phase 1)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- `strategy: "explicit"` only — automated/similarity-based pairing without a
  `match_on` key is on the roadmap.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships.
```

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document multi_file reconciliation jobs"
```

---

## Self-review notes

- **Spec coverage:** every Phase 1 bullet in the header's "Spec coverage" section maps to a task above (Tasks 1-3 = pattern matching/discovery, Task 4 = declarative config, Task 5 = aggregation engine, Tasks 6-8 = wiring through all three validation/execution call sites, Task 9 = docs). Automated mapping, S3/SFTP, parallel execution, and UI/JUnit/HTML reporting are explicitly out of scope for Phase 1 and tracked in the architecture doc's roadmap (§7) instead of half-implemented here.
- **Backward compatibility:** no existing test, schema field, or DB column changes. `tests/unit/test_file_backed_jobs.py` and `tests/unit/test_bo_live_reconciliation.py` are unmodified and re-run in Task 8 Step 9 to confirm.
- **Type/name consistency check:** `FileMappingSpec`, `FileSourceSpec`, `DiscoveredFile`, `FileGroup`, `FilePair`, `FileMappingResult`, `discover_local_files`, `pair_files`, `aggregate_reconciliation_results`, and `compile_token_pattern` are named identically at every call site across Tasks 1-8 (module definition, schema validation, job validation, and `RunExecutor`) — no renamed variants were introduced between tasks.

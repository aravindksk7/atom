# Multi-File Reconciliation — Phase 2: Smart Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `multi_file` reconciliation job guess source-to-target file pairs by structural similarity (filename overlap, column signature, row-count ratio) when the QA engineer hasn't configured explicit `match_on` tokens, and write a lineage manifest recording every discovery/pairing decision for audit.

**Architecture:** This is Phase 2 of the roadmap in `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7. Phase 1 (merged to master at commit `80bffe0`) built `etl_framework/reconciliation/file_mapping.py` with `strategy: "explicit"` key-based pairing (`pair_files`) and wired it through `JobDefinition` validation, `validate_job_definition`, and `RunExecutor`, all via one shared `FileMappingSpec.from_params(params)`. Because that single choke point already exists, Phase 2 adds a second strategy (`"automated"`) to the *same* module and the *same* parser — **zero changes are needed to `api/schemas.py` or `etl_framework/runner/job_validation.py`**, since both already just call `FileMappingSpec.from_params` and propagate whatever `ValueError` it raises. The new automated pairing function returns the exact same `FileMappingResult`/`FilePair`/`FileGroup` types Phase 1 already consumes, so the per-pair execution loop in `RunExecutor._build_case_multi_file_reconciliation` needs only a small branch to choose which pairing function to call — the loop that reads, concats, and reconciles each pair is untouched.

**Tech Stack:** Python 3, pytest, Hypothesis (already a dependency — see `tests/property/`), pandas, the existing `read_tabular`/`FrameEngine`/`ReconciliationEngine` stack.

**Spec coverage in this phase** (against the original request's requirement 3, "Automated Mapping... guesses the pairs based on structural similarity if explicit patterns aren't provided," and the Phase 2 roadmap line "automated/similarity-based fallback matcher, lineage manifest, property-based tests"):
1. *Automated mapping* — done: `pair_files_automated` combines three signals (filename token similarity, column-name Jaccard similarity, row-count ratio) into one score per candidate (source, target) pair and greedily assigns matches above a configurable threshold.
2. *Lineage manifest* — done: `FileMappingManifestWriter` writes `logs/file_mapping_manifest_{run_id}_{job_name}.json` recording every pair (method, score breakdown) and every unmatched group, for both `explicit` and `automated` strategies.
3. *Property-based pairing correctness* — done: Hypothesis tests in `tests/property/test_file_mapping_property.py` covering the `pair_files` (explicit) partition invariant and the `pair_files_automated` no-double-match/threshold invariant.

**Explicitly out of scope for Phase 2** (deferred to later phases per the roadmap): S3/SFTP discovery, per-pair parallel execution, first-class UI/JUnit/HTML surfacing of the manifest (it's a JSON file on disk in this phase, same as `MetricsWriter`'s `logs/metrics_{run_id}.json` today), and combining automated matching with shard-collapse (each automated match is exactly one file per side — grouping several same-side shards under automated matching is not implemented here; use `strategy: "explicit"` with `match_on` for that case, as today).

---

### Task 1: Filename similarity signal

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Create: `tests/unit/test_file_mapping_similarity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_file_mapping_similarity.py
from __future__ import annotations

from etl_framework.reconciliation.file_mapping import _filename_similarity


def test_filename_similarity_identical_stems_scores_one() -> None:
    assert _filename_similarity("sales_east_20260101.csv", "sales_east_20260101.dat") == 1.0


def test_filename_similarity_unrelated_names_scores_low() -> None:
    score = _filename_similarity("sales_east_20260101.csv", "zzz_completely_different.dat")
    assert score < 0.5


def test_filename_similarity_partial_overlap_scores_between() -> None:
    high = _filename_similarity("sales_east_20260101.csv", "financials_east_20260101.dat")
    low = _filename_similarity("sales_east_20260101.csv", "zzz_completely_different.dat")
    assert high > low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: FAIL with `ImportError: cannot import name '_filename_similarity'`

- [ ] **Step 3: Write minimal implementation**

Update the top import block of `etl_framework/reconciliation/file_mapping.py`. It currently reads:

```python
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus
```

Change it to add `difflib`:

```python
from __future__ import annotations

import dataclasses
import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus
```

Then APPEND to the end of the file:

```python
def _filename_similarity(source_name: str, target_name: str) -> float:
    """Compare two filenames by their stem (name minus extension) using
    ``difflib``'s ratio, so ``sales_east_20260101.csv`` and
    ``financials_east_20260101.dat`` score higher than two unrelated names,
    regardless of extension.
    """
    source_stem = Path(source_name).stem
    target_stem = Path(target_name).stem
    return difflib.SequenceMatcher(None, source_stem, target_stem).ratio()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping_similarity.py
git commit -m "feat(reconciliation): add filename similarity signal for automated mapping"
```

---

### Task 2: Column-signature and row-count-ratio signals

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping_similarity.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_file_mapping_similarity.py`:

```python
from etl_framework.reconciliation.file_mapping import _column_signature_similarity, _row_count_ratio


def test_column_signature_similarity_identical_columns_scores_one() -> None:
    assert _column_signature_similarity(["id", "value"], ["id", "value"]) == 1.0


def test_column_signature_similarity_disjoint_columns_scores_zero() -> None:
    assert _column_signature_similarity(["id", "value"], ["foo", "bar"]) == 0.0


def test_column_signature_similarity_partial_overlap_is_jaccard() -> None:
    # intersection={id}, union={id,value,extra} -> 1/3
    assert _column_signature_similarity(["id", "value"], ["id", "extra"]) == 1 / 3


def test_column_signature_similarity_both_empty_scores_one() -> None:
    assert _column_signature_similarity([], []) == 1.0


def test_row_count_ratio_equal_counts_scores_one() -> None:
    assert _row_count_ratio(10, 10) == 1.0


def test_row_count_ratio_uses_min_over_max() -> None:
    assert _row_count_ratio(5, 20) == 0.25


def test_row_count_ratio_both_zero_scores_one() -> None:
    assert _row_count_ratio(0, 0) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: FAIL with `ImportError: cannot import name '_column_signature_similarity'`

- [ ] **Step 3: Write minimal implementation**

APPEND to the end of `etl_framework/reconciliation/file_mapping.py`:

```python
def _column_signature_similarity(source_columns: Sequence[str], target_columns: Sequence[str]) -> float:
    """Jaccard similarity of two column-name sets. Two schemas with no
    columns at all are considered a perfect match (there's nothing to
    disagree on); otherwise |intersection| / |union|.
    """
    source_set = set(source_columns)
    target_set = set(target_columns)
    if not source_set and not target_set:
        return 1.0
    union = source_set | target_set
    if not union:
        return 1.0
    return len(source_set & target_set) / len(union)


def _row_count_ratio(source_rows: int, target_rows: int) -> float:
    """Ratio of the smaller row count to the larger, in [0, 1]. Two empty
    datasets are considered a perfect match.
    """
    if source_rows == 0 and target_rows == 0:
        return 1.0
    return min(source_rows, target_rows) / max(source_rows, target_rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping_similarity.py
git commit -m "feat(reconciliation): add column-signature and row-count-ratio signals"
```

---

### Task 3: `AutomatedMappingSpec` and combined scoring

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping_similarity.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_file_mapping_similarity.py`:

```python
from etl_framework.reconciliation.file_mapping import _combined_similarity, KNOWN_SIMILARITY_SIGNALS


def test_known_similarity_signals_are_the_three_documented_signals() -> None:
    assert KNOWN_SIMILARITY_SIGNALS == ("filename_tokens", "column_signature", "row_count_ratio")


def test_combined_similarity_averages_all_signals_by_default() -> None:
    signal_scores = {"filename_tokens": 1.0, "column_signature": 0.5, "row_count_ratio": 0.0}
    score = _combined_similarity(signal_scores, KNOWN_SIMILARITY_SIGNALS)
    assert score == (1.0 + 0.5 + 0.0) / 3


def test_combined_similarity_averages_only_selected_signals() -> None:
    signal_scores = {"filename_tokens": 1.0, "column_signature": 0.5, "row_count_ratio": 0.0}
    score = _combined_similarity(signal_scores, ("filename_tokens", "column_signature"))
    assert score == (1.0 + 0.5) / 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: FAIL with `ImportError: cannot import name '_combined_similarity'`

- [ ] **Step 3: Write minimal implementation**

APPEND to the end of `etl_framework/reconciliation/file_mapping.py`:

```python
KNOWN_SIMILARITY_SIGNALS: tuple[str, ...] = ("filename_tokens", "column_signature", "row_count_ratio")


def _combined_similarity(signal_scores: dict[str, float], signals: Sequence[str]) -> float:
    """Average the named ``signals`` out of ``signal_scores`` (which always
    carries all of ``KNOWN_SIMILARITY_SIGNALS`` -- ``signals`` just selects
    which ones count toward the final score, so the manifest can still show
    every signal even when only some are configured to matter).
    """
    selected = [signal_scores[name] for name in signals]
    return sum(selected) / len(selected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: PASS (13 tests total)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping_similarity.py
git commit -m "feat(reconciliation): add combined similarity scoring for automated mapping"
```

---

### Task 4: Extend `FileMappingSpec` to accept `strategy: "automated"`

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_file_mapping.py`:

```python
from etl_framework.reconciliation.file_mapping import AutomatedMappingSpec


def test_file_mapping_spec_parses_automated_strategy_with_defaults() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "strategy": "automated",
            "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
        }
    })

    assert spec.strategy == "automated"
    assert spec.automated == AutomatedMappingSpec(similarity_threshold=0.7, signals=("filename_tokens", "column_signature", "row_count_ratio"))


def test_file_mapping_spec_parses_automated_strategy_with_overrides() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "strategy": "automated",
            "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
            "automated_mapping": {
                "similarity_threshold": 0.9,
                "signals": ["filename_tokens", "row_count_ratio"],
            },
        }
    })

    assert spec.automated == AutomatedMappingSpec(similarity_threshold=0.9, signals=("filename_tokens", "row_count_ratio"))


def test_file_mapping_spec_explicit_strategy_has_no_automated_config() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        }
    })

    assert spec.automated is None


def test_file_mapping_spec_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="is not supported yet"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "ml_based",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
            }
        })


def test_file_mapping_spec_rejects_bad_similarity_threshold() -> None:
    with pytest.raises(ValueError, match="similarity_threshold must be"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
                "automated_mapping": {"similarity_threshold": 1.5},
            }
        })


def test_file_mapping_spec_rejects_unknown_signal() -> None:
    with pytest.raises(ValueError, match="unknown signal"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
                "automated_mapping": {"signals": ["made_up_signal"]},
            }
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL — `ImportError: cannot import name 'AutomatedMappingSpec'`, and `test_file_mapping_spec_parses_automated_strategy_with_defaults` / `..._with_overrides` fail because the current code rejects any strategy other than `"explicit"`.

- [ ] **Step 3: Write minimal implementation**

In `etl_framework/reconciliation/file_mapping.py`, find the `FileMappingSpec` dataclass:

```python
@dataclass(frozen=True)
class FileMappingSpec:
    strategy: str
    match_on: tuple[str, ...]
    source: FileSourceSpec
    target: FileSourceSpec
    unmatched_policy: str = "fail"
```

Add an `automated` field:

```python
@dataclass(frozen=True)
class FileMappingSpec:
    strategy: str
    match_on: tuple[str, ...]
    source: FileSourceSpec
    target: FileSourceSpec
    unmatched_policy: str = "fail"
    automated: "AutomatedMappingSpec | None" = None
```

Find the `from_params` classmethod body:

```python
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
```

Replace it with:

```python
        strategy = raw.get("strategy", "explicit")
        if strategy not in ("explicit", "automated"):
            raise ValueError(
                f"file_mapping.strategy '{strategy}' is not supported yet; "
                "'explicit' and 'automated' are implemented"
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
        automated = (
            _parse_automated_mapping(raw.get("automated_mapping"))
            if strategy == "automated"
            else None
        )
        return cls(
            strategy=strategy,
            match_on=match_on,
            source=source,
            target=target,
            unmatched_policy=unmatched_policy,
            automated=automated,
        )
```

Then APPEND the following to the very END of `etl_framework/reconciliation/file_mapping.py` (i.e. after everything Tasks 1-3 already appended there, including `KNOWN_SIMILARITY_SIGNALS` and `_combined_similarity` -- do NOT insert this earlier in the file, e.g. near `_parse_file_source`):

```python
@dataclass(frozen=True)
class AutomatedMappingSpec:
    similarity_threshold: float = 0.7
    signals: tuple[str, ...] = KNOWN_SIMILARITY_SIGNALS


def _parse_automated_mapping(raw: Any) -> AutomatedMappingSpec:
    if raw is None:
        return AutomatedMappingSpec()
    if not isinstance(raw, dict):
        raise ValueError("file_mapping.automated_mapping must be an object")
    threshold = raw.get("similarity_threshold", 0.7)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not (0.0 <= threshold <= 1.0):
        raise ValueError(
            "file_mapping.automated_mapping.similarity_threshold must be a "
            f"number between 0.0 and 1.0, got {threshold!r}"
        )
    signals = tuple(raw.get("signals") or KNOWN_SIMILARITY_SIGNALS)
    unknown = [name for name in signals if name not in KNOWN_SIMILARITY_SIGNALS]
    if unknown:
        raise ValueError(
            f"file_mapping.automated_mapping.signals has unknown signal(s): {unknown}"
        )
    if not signals:
        raise ValueError("file_mapping.automated_mapping.signals must not be empty")
    return AutomatedMappingSpec(similarity_threshold=float(threshold), signals=signals)
```

Important ordering constraint: `AutomatedMappingSpec.signals` uses `KNOWN_SIMILARITY_SIGNALS` as its dataclass field default, and **a dataclass field default is evaluated when the class body executes** (at import time), not lazily. `KNOWN_SIMILARITY_SIGNALS` must therefore already be defined earlier in the file than this class. Because Task 3 appended `KNOWN_SIMILARITY_SIGNALS` (and `_combined_similarity`) at the end of the file, and this step appends `AutomatedMappingSpec` after that, the ordering is correct as long as this code goes at the end of the file, not inserted mid-file near `_parse_file_source`.

This does mean the earlier edit to `FileMappingSpec.from_params` (which now calls `_parse_automated_mapping`, a function defined *after* `FileMappingSpec` in the file) works fine too -- `from_params` is a method body, and Python only resolves `_parse_automated_mapping` when the method is *called*, long after the whole module has finished importing, so forward-referencing a later-defined function from inside a method body is safe (unlike the dataclass-default case above, which evaluates at class-definition/import time).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (22 tests total)

- [ ] **Step 5: Run the similarity test file too, to confirm no import breakage**

Run: `python -m pytest tests/unit/test_file_mapping_similarity.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): accept strategy=automated in FileMappingSpec"
```

---

### Task 5: `pair_files_automated` — greedy similarity-based pairing

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Create: `tests/unit/test_pair_files_automated.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pair_files_automated.py
from __future__ import annotations

import pandas as pd

from etl_framework.reconciliation.file_mapping import (
    AutomatedMappingSpec,
    DiscoveredFile,
    pair_files_automated,
)


def _df_file(name: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens={})


def test_pair_files_automated_matches_best_scoring_candidates() -> None:
    source_files = [_df_file("sales_east.csv"), _df_file("sales_west.csv")]
    target_files = [_df_file("financials_east.dat"), _df_file("financials_west.dat")]
    source_frames = {
        "/x/sales_east.csv": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}),
        "/x/sales_west.csv": pd.DataFrame({"id": [1], "value": ["c"]}),
    }
    target_frames = {
        "/x/financials_east.dat": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}),
        "/x/financials_west.dat": pd.DataFrame({"id": [1], "value": ["z"]}),
    }

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.5),
    )

    assert len(mapping.pairs) == 2
    assert not mapping.unmatched_sources
    assert not mapping.unmatched_targets
    assert len(scores) == 2
    for score in scores:
        assert score.score >= 0.5

    paired_names = {
        (pair.source.files[0].file_name, pair.target.files[0].file_name)
        for pair in mapping.pairs
    }
    assert ("sales_east.csv", "financials_east.dat") in paired_names
    assert ("sales_west.csv", "financials_west.dat") in paired_names


def test_pair_files_automated_leaves_low_scoring_files_unmatched() -> None:
    source_files = [_df_file("sales_east.csv")]
    target_files = [_df_file("zzz_totally_unrelated.dat")]
    source_frames = {"/x/sales_east.csv": pd.DataFrame({"id": [1]})}
    target_frames = {"/x/zzz_totally_unrelated.dat": pd.DataFrame({"other": [1]})}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9),
    )

    assert not mapping.pairs
    assert len(mapping.unmatched_sources) == 1
    assert len(mapping.unmatched_targets) == 1
    assert scores == []


def test_pair_files_automated_never_uses_a_file_twice() -> None:
    # Two source files that would both score well against the same single
    # target file if matching weren't exclusive.
    source_files = [_df_file("sales_east_1.csv"), _df_file("sales_east_2.csv")]
    target_files = [_df_file("financials_east.dat")]
    frame = pd.DataFrame({"id": [1], "value": ["a"]})
    source_frames = {"/x/sales_east_1.csv": frame, "/x/sales_east_2.csv": frame}
    target_frames = {"/x/financials_east.dat": frame}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.3),
    )

    assert len(mapping.pairs) == 1
    assert len(mapping.unmatched_sources) == 1
    assert len(scores) == 1


def test_pair_files_automated_respects_selected_signals_only() -> None:
    # Identical filenames but completely different columns and row counts.
    # With only "filename_tokens" selected, they should still match at 1.0;
    # with all signals (default), the score should be lower.
    source_files = [_df_file("data.csv")]
    target_files = [_df_file("data.dat")]
    source_frames = {"/x/data.csv": pd.DataFrame({"a": [1, 2, 3]})}
    target_frames = {"/x/data.dat": pd.DataFrame({"z": [1]})}

    mapping_filename_only, scores_filename_only = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9, signals=("filename_tokens",)),
    )
    assert len(mapping_filename_only.pairs) == 1
    assert scores_filename_only[0].score == 1.0

    mapping_all_signals, _ = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9),
    )
    assert not mapping_all_signals.pairs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_pair_files_automated.py -v`
Expected: FAIL with `ImportError: cannot import name 'pair_files_automated'`

- [ ] **Step 3: Write minimal implementation**

Update the top import block of `etl_framework/reconciliation/file_mapping.py` once more. It currently reads (after Task 1's edit):

```python
from __future__ import annotations

import dataclasses
import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus
```

Add `pandas`:

```python
from __future__ import annotations

import dataclasses
import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus
```

Then APPEND to the end of `etl_framework/reconciliation/file_mapping.py`:

```python
@dataclass(frozen=True)
class SimilarityScore:
    source: DiscoveredFile
    target: DiscoveredFile
    score: float
    signal_scores: dict[str, float]


def pair_files_automated(
    source_files: list[DiscoveredFile],
    source_frames: dict[str, pd.DataFrame],
    target_files: list[DiscoveredFile],
    target_frames: dict[str, pd.DataFrame],
    automated: AutomatedMappingSpec,
) -> tuple[FileMappingResult, list[SimilarityScore]]:
    """Guess source-to-target file pairs by structural similarity instead of
    shared key tokens. Every source file is scored against every target file
    using ``automated.signals``; candidates are matched greedily from the
    highest combined score down, each file used at most once, stopping once
    the remaining best candidate scores below ``automated.similarity_threshold``.

    Unlike ``pair_files``, this always produces single-file ``FileGroup``s
    (one file per side per pair) -- guessing which *shards* belong together
    from structural similarity alone is not attempted in this phase; use
    ``strategy: "explicit"`` with ``match_on`` for shard collapsing.
    """
    candidates: list[SimilarityScore] = []
    for source_file in source_files:
        source_df = source_frames[source_file.path]
        for target_file in target_files:
            target_df = target_frames[target_file.path]
            signal_scores = {
                "filename_tokens": _filename_similarity(source_file.file_name, target_file.file_name),
                "column_signature": _column_signature_similarity(
                    list(source_df.columns), list(target_df.columns)
                ),
                "row_count_ratio": _row_count_ratio(len(source_df), len(target_df)),
            }
            combined = _combined_similarity(signal_scores, automated.signals)
            candidates.append(SimilarityScore(
                source=source_file, target=target_file, score=combined, signal_scores=signal_scores,
            ))

    candidates.sort(key=lambda c: (-c.score, c.source.file_name, c.target.file_name))

    used_sources: set[str] = set()
    used_targets: set[str] = set()
    pairs: list[FilePair] = []
    scores: list[SimilarityScore] = []
    for candidate in candidates:
        if candidate.score < automated.similarity_threshold:
            break
        if candidate.source.path in used_sources or candidate.target.path in used_targets:
            continue
        used_sources.add(candidate.source.path)
        used_targets.add(candidate.target.path)
        pairs.append(FilePair(
            key=(candidate.source.file_name, candidate.target.file_name),
            source=FileGroup(key=(candidate.source.file_name,), files=[candidate.source]),
            target=FileGroup(key=(candidate.target.file_name,), files=[candidate.target]),
        ))
        scores.append(candidate)

    unmatched_sources = [
        FileGroup(key=(f.file_name,), files=[f]) for f in source_files if f.path not in used_sources
    ]
    unmatched_targets = [
        FileGroup(key=(f.file_name,), files=[f]) for f in target_files if f.path not in used_targets
    ]

    return FileMappingResult(
        match_on=(),
        pairs=pairs,
        unmatched_sources=unmatched_sources,
        unmatched_targets=unmatched_targets,
    ), scores
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_pair_files_automated.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole `file_mapping` module's tests to confirm nothing else broke**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_file_mapping_similarity.py tests/unit/test_pair_files_automated.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_pair_files_automated.py
git commit -m "feat(reconciliation): add greedy similarity-based automated pairing"
```

---

### Task 6: File-mapping lineage manifest

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Create: `tests/unit/test_file_mapping_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_file_mapping_manifest.py
from __future__ import annotations

import json

from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileGroup,
    FileMappingResult,
    FileMappingManifestWriter,
    FileMappingSpec,
    FilePair,
    FileSourceSpec,
    SimilarityScore,
)


def _spec(strategy: str = "explicit") -> FileMappingSpec:
    return FileMappingSpec(
        strategy=strategy,
        match_on=("region",) if strategy == "explicit" else (),
        source=FileSourceSpec(kind="local", root="/spool", pattern="sales_{region}.csv"),
        target=FileSourceSpec(kind="local", root="/baseline", pattern="fin_{region}.csv"),
    )


def test_manifest_writer_records_explicit_pairs_and_unmatched(tmp_path) -> None:
    east_source = FileGroup(key=("east",), files=[DiscoveredFile("/s/e.csv", "e.csv", {"region": "east"})])
    east_target = FileGroup(key=("east",), files=[DiscoveredFile("/t/e.dat", "e.dat", {"region": "east"})])
    north_unmatched = FileGroup(key=("north",), files=[DiscoveredFile("/s/n.csv", "n.csv", {"region": "north"})])
    mapping = FileMappingResult(
        match_on=("region",),
        pairs=[FilePair(key=("east",), source=east_source, target=east_target)],
        unmatched_sources=[north_unmatched],
        unmatched_targets=[],
    )

    output_path = tmp_path / "manifest.json"
    FileMappingManifestWriter(str(output_path)).write(
        run_id="run-1", job_name="regional_sales_recon", spec=_spec(), mapping=mapping, similarity_scores=None,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["job_name"] == "regional_sales_recon"
    assert payload["strategy"] == "explicit"
    assert len(payload["pairs"]) == 1
    assert payload["pairs"][0]["mapping_method"] == "explicit"
    assert payload["pairs"][0]["similarity_score"] is None
    assert payload["pairs"][0]["source_files"] == ["e.csv"]
    assert payload["pairs"][0]["target_files"] == ["e.dat"]
    assert len(payload["unmatched_sources"]) == 1
    assert payload["unmatched_sources"][0]["files"] == ["n.csv"]


def test_manifest_writer_records_automated_pairs_with_scores(tmp_path) -> None:
    source_file = DiscoveredFile("/s/a.csv", "a.csv", {})
    target_file = DiscoveredFile("/t/b.dat", "b.dat", {})
    source_group = FileGroup(key=("a.csv",), files=[source_file])
    target_group = FileGroup(key=("b.dat",), files=[target_file])
    mapping = FileMappingResult(
        match_on=(),
        pairs=[FilePair(key=("a.csv", "b.dat"), source=source_group, target=target_group)],
        unmatched_sources=[],
        unmatched_targets=[],
    )
    scores = [SimilarityScore(
        source=source_file, target=target_file, score=0.83,
        signal_scores={"filename_tokens": 0.9, "column_signature": 1.0, "row_count_ratio": 0.6},
    )]

    output_path = tmp_path / "manifest.json"
    FileMappingManifestWriter(str(output_path)).write(
        run_id="run-2", job_name="auto_job", spec=_spec(strategy="automated"), mapping=mapping,
        similarity_scores=scores,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["strategy"] == "automated"
    assert payload["pairs"][0]["mapping_method"] == "automated"
    assert payload["pairs"][0]["similarity_score"] == 0.83
    assert payload["pairs"][0]["signal_scores"]["column_signature"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping_manifest.py -v`
Expected: FAIL with `ImportError: cannot import name 'FileMappingManifestWriter'`

- [ ] **Step 3: Write minimal implementation**

Update the top import block once more (after Task 5's edit, which added `import pandas as pd`). Add `json` and `os`:

```python
from __future__ import annotations

import dataclasses
import difflib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus
```

Then APPEND to the end of `etl_framework/reconciliation/file_mapping.py`:

```python
class FileMappingManifestWriter:
    """Writes a lineage artifact recording every file-mapping decision for
    one multi_file job execution -- which files were discovered, how they
    were paired (and with what similarity score, for automated matches), and
    which groups were left unmatched. Mirrors the existing
    ``etl_framework.reporting.metrics.MetricsWriter`` pattern (a small class
    holding an output path, with a single ``write`` method).
    """

    def __init__(self, output_path: str) -> None:
        self._output_path = output_path

    def write(
        self,
        run_id: str,
        job_name: str,
        spec: FileMappingSpec,
        mapping: FileMappingResult,
        similarity_scores: list[SimilarityScore] | None,
    ) -> None:
        parent = os.path.dirname(self._output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        scores_by_pair_key = (
            {(s.source.path, s.target.path): s for s in similarity_scores}
            if similarity_scores is not None
            else {}
        )

        pairs_payload = []
        for pair in mapping.pairs:
            score = None
            if pair.source.files and pair.target.files:
                score = scores_by_pair_key.get((pair.source.files[0].path, pair.target.files[0].path))
            pairs_payload.append({
                "mapping_method": spec.strategy,
                "similarity_score": score.score if score is not None else None,
                "signal_scores": score.signal_scores if score is not None else None,
                "source_files": [f.file_name for f in pair.source.files],
                "target_files": [f.file_name for f in pair.target.files],
            })

        payload = {
            "run_id": run_id,
            "job_name": job_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "strategy": spec.strategy,
            "match_on": list(mapping.match_on),
            "pairs": pairs_payload,
            "unmatched_sources": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_sources],
            "unmatched_targets": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_targets],
        }

        with open(self._output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
```

Note: `_group_summary(group, match_on)` builds `{"key": dict(zip(match_on, group.key)), "files": [...]}`. For automated-strategy unmatched groups, `mapping.match_on` is `()`, so `"key"` will be `{}` in the manifest even though `group.key` internally holds the filename -- the `"files"` list is what carries the real identifying information for automated mode, which is sufficient for the lineage record. This is consistent with how `aggregate_reconciliation_results` already treats automated-mode pair keys (see Task 5 above).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping_manifest.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping_manifest.py
git commit -m "feat(reconciliation): write a file-mapping lineage manifest"
```

---

### Task 7: Wire automated strategy and manifest writing into `RunExecutor`

**Files:**
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_multi_file_jobs.py`:

```python
import json


def test_run_executor_multi_file_automated_strategy_pairs_and_writes_manifest(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.chdir(tmp_path)

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")
    (target_dir / "financials_east.dat").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")

    job = JobDefinition(
        name="auto_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": str(source_dir), "pattern": "*.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "*.dat"},
                "automated_mapping": {"similarity_threshold": 0.3},
            },
        },
    )
    executor = RunExecutor(
        db=None, run_id="test-run-auto", source_env="source", target_env="target",
        job_sequence=[], run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["pairs_total"] == 1

    manifest_path = tmp_path / "logs" / "file_mapping_manifest_test-run-auto_auto_sales_recon.json"
    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["strategy"] == "automated"
    assert payload["pairs"][0]["mapping_method"] == "automated"
    assert payload["pairs"][0]["similarity_score"] >= 0.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py::test_run_executor_multi_file_automated_strategy_pairs_and_writes_manifest -v`
Expected: FAIL — `FileMappingSpec.from_params` currently accepts `strategy: "automated"` (after Task 4) but `_build_case_multi_file_reconciliation` doesn't yet branch on it, and no manifest is written yet, so the job either errors trying to use `spec.match_on` for grouping in a way that doesn't produce the expected pair, or the manifest file assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `api/services/run_executor.py`, find `_build_case_multi_file_reconciliation` (currently starting at line 585):

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
```

Replace the import block and the discovery/pairing lines (everything shown above, up to but NOT including the `if mapping.unmatched_sources or mapping.unmatched_targets:` line) with:

```python
    def _build_case_multi_file_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from api.services.file_source import read_tabular, resolve_allowed_path
            from etl_framework.reconciliation.file_mapping import (
                FileMappingManifestWriter,
                FileMappingSpec,
                aggregate_reconciliation_results,
                discover_local_files,
                pair_files,
                pair_files_automated,
            )

            spec = FileMappingSpec.from_params(job.params)
            source_root = resolve_allowed_path(spec.source.root)
            target_root = resolve_allowed_path(spec.target.root)
            source_files = discover_local_files(source_root, spec.source.pattern)
            target_files = discover_local_files(target_root, spec.target.pattern)

            if spec.strategy == "automated":
                source_frames = {
                    f.path: read_tabular(path=f.path, file_name=f.file_name) for f in source_files
                }
                target_frames = {
                    f.path: read_tabular(path=f.path, file_name=f.file_name) for f in target_files
                }
                mapping, similarity_scores = pair_files_automated(
                    source_files, source_frames, target_files, target_frames, spec.automated,
                )
            else:
                mapping = pair_files(source_files, target_files, spec.match_on)
                similarity_scores = None

            FileMappingManifestWriter(
                f"logs/file_mapping_manifest_{self._run_id}_{job.name}.json"
            ).write(self._run_id, job.name, spec, mapping, similarity_scores)

            if mapping.unmatched_sources or mapping.unmatched_targets:
```

Everything from `if mapping.unmatched_sources or mapping.unmatched_targets:` through the end of the method (the `unmatched_policy` handling, the zero-pairs guard, the per-pair loop, and the final `return aggregate_reconciliation_results(...)`) is UNCHANGED -- it already operates purely on the `mapping`/`FileMappingResult` object regardless of which pairing function produced it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (all tests in the file, including the new automated-strategy one)

- [ ] **Step 5: Run the full multi-file test surface to confirm no regression**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_file_mapping_similarity.py tests/unit/test_pair_files_automated.py tests/unit/test_file_mapping_manifest.py tests/unit/test_multi_file_jobs.py tests/unit/test_file_backed_jobs.py tests/unit/test_bo_live_reconciliation.py tests/unit/test_file_source.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(run-executor): support automated strategy and write mapping manifest"
```

---

### Task 8: Property-based tests for pairing correctness

**Files:**
- Create: `tests/property/test_file_mapping_property.py`

- [ ] **Step 1: Write the property tests**

```python
# tests/property/test_file_mapping_property.py
"""Property-based tests for file-mapping pairing correctness using hypothesis."""
from __future__ import annotations

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from etl_framework.reconciliation.file_mapping import (
    AutomatedMappingSpec,
    DiscoveredFile,
    pair_files,
    pair_files_automated,
)

_KEY_ALPHABET = st.sampled_from(["alpha", "bravo", "charlie", "delta"])


def _discovered_files(prefix: str):
    return st.lists(
        st.builds(
            lambda key, idx: DiscoveredFile(
                path=f"/{prefix}/{key}_{idx}.csv",
                file_name=f"{key}_{idx}.csv",
                tokens={"region": key},
            ),
            key=_KEY_ALPHABET,
            idx=st.integers(min_value=0, max_value=1000),
        ),
        min_size=0,
        max_size=8,
        unique_by=lambda f: f.path,
    )


# ── pair_files (explicit) partition invariant ─────────────────────────────────

@given(sources=_discovered_files("src"), targets=_discovered_files("tgt"))
@settings(max_examples=60)
def test_pair_files_partitions_every_discovered_file_exactly_once(sources, targets) -> None:
    """Every discovered file ends up in exactly one place: a pair's source
    group, a pair's target group, an unmatched-source group, or an
    unmatched-target group -- on the correct side, never duplicated, never
    dropped.
    """
    mapping = pair_files(sources, targets, ["region"])

    seen_source_paths: list[str] = []
    seen_target_paths: list[str] = []
    for pair in mapping.pairs:
        seen_source_paths.extend(f.path for f in pair.source.files)
        seen_target_paths.extend(f.path for f in pair.target.files)
    for group in mapping.unmatched_sources:
        seen_source_paths.extend(f.path for f in group.files)
    for group in mapping.unmatched_targets:
        seen_target_paths.extend(f.path for f in group.files)

    assert sorted(seen_source_paths) == sorted(f.path for f in sources)
    assert sorted(seen_target_paths) == sorted(f.path for f in targets)
    # No file appears twice across all groups on its own side.
    assert len(seen_source_paths) == len(set(seen_source_paths))
    assert len(seen_target_paths) == len(set(seen_target_paths))


@given(sources=_discovered_files("src"), targets=_discovered_files("tgt"))
@settings(max_examples=60)
def test_pair_files_every_pair_key_present_on_both_sides(sources, targets) -> None:
    """A key only becomes a pair if at least one file with that key exists
    on both the source and target side."""
    mapping = pair_files(sources, targets, ["region"])

    source_keys = {f.tokens["region"] for f in sources}
    target_keys = {f.tokens["region"] for f in targets}
    for pair in mapping.pairs:
        region = pair.key[0]
        assert region in source_keys
        assert region in target_keys


# ── pair_files_automated invariants ───────────────────────────────────────────

@given(
    row_counts=st.lists(st.integers(min_value=1, max_value=5), min_size=1, max_size=4, unique=True),
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=40)
def test_pair_files_automated_never_reuses_a_file(row_counts, threshold) -> None:
    """No source or target file is ever used in more than one pair,
    regardless of how many candidates tie on score."""
    source_files = [
        DiscoveredFile(path=f"/s/f{i}.csv", file_name=f"f{i}.csv", tokens={}) for i in range(len(row_counts))
    ]
    target_files = [
        DiscoveredFile(path=f"/t/f{i}.dat", file_name=f"f{i}.dat", tokens={}) for i in range(len(row_counts))
    ]
    source_frames = {f.path: pd.DataFrame({"id": range(n)}) for f, n in zip(source_files, row_counts)}
    target_frames = {f.path: pd.DataFrame({"id": range(n)}) for f, n in zip(target_files, row_counts)}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=threshold),
    )

    used_sources = [f.path for pair in mapping.pairs for f in pair.source.files]
    used_targets = [f.path for pair in mapping.pairs for f in pair.target.files]
    assert len(used_sources) == len(set(used_sources))
    assert len(used_targets) == len(set(used_targets))
    assert len(mapping.pairs) == len(scores)
    for score in scores:
        assert score.score >= threshold


@given(threshold=st.floats(min_value=0.0, max_value=1.0))
@settings(max_examples=20)
def test_pair_files_automated_empty_inputs_produce_no_pairs(threshold) -> None:
    mapping, scores = pair_files_automated(
        [], {}, [], {}, AutomatedMappingSpec(similarity_threshold=threshold),
    )
    assert mapping.pairs == []
    assert mapping.unmatched_sources == []
    assert mapping.unmatched_targets == []
    assert scores == []
```

- [ ] **Step 2: Run to verify all property tests pass**

Run: `python -m pytest tests/property/test_file_mapping_property.py -v`
Expected: PASS (5 tests, each running its configured number of Hypothesis examples)

- [ ] **Step 3: Commit**

```bash
git add tests/property/test_file_mapping_property.py
git commit -m "test(reconciliation): add property-based tests for file-mapping pairing"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Update the doc**

In `docs/multi_file_reconciliation.md`, find the "Current limitations (Phase 1)" section:

```markdown
## Current limitations (Phase 1)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- `strategy: "explicit"` only — automated/similarity-based pairing without a
  `match_on` key is on the roadmap.
- Pairs are compared sequentially; per-pair parallelism and per-pair failure
  isolation are on the roadmap.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
```

Replace it with:

```markdown
## Automated mapping (no `match_on` needed)

Set `strategy: "automated"` to have the framework guess pairs by structural
similarity instead of matching on filename tokens:

```json
{
  "file_mapping": {
    "strategy": "automated",
    "source": {"kind": "local", "root": "/spool/exports", "pattern": "*.csv"},
    "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
    "automated_mapping": {
      "similarity_threshold": 0.7,
      "signals": ["filename_tokens", "column_signature", "row_count_ratio"]
    }
  }
}
```

Every source file is scored against every target file using the selected
signals (filename similarity, column-name overlap, row-count ratio),
averaged into one score per candidate pair. Pairs are assigned greedily from
the highest-scoring candidate down, each file used at most once; anything
left over when no remaining candidate clears `similarity_threshold` is
reported as unmatched, same as the explicit strategy. Automated matching
always pairs single files (it does not guess which shards belong together
across several files sharing a key on one side) — use `strategy: "explicit"`
with `match_on` for that.

## Lineage manifest

Every multi_file job execution (explicit or automated) writes
`logs/file_mapping_manifest_{run_id}_{job_name}.json`, recording each pair's
mapping method and (for automated pairs) its similarity score breakdown, plus
every unmatched group -- an audit trail for why files were or weren't paired.

## Current limitations (Phase 2)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- Automated matching pairs single files only; shard-collapsing (many files
  on one side sharing a key) is `strategy: "explicit"` only.
- Pairs are compared sequentially; per-pair parallelism and per-pair failure
  isolation are on the roadmap.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships. The lineage manifest is a JSON file on disk, not yet surfaced in
  the UI or run report.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
```

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document automated mapping and the lineage manifest"
```

---

## Self-review notes

- **Spec coverage:** Task 1-3 build the three signals and combiner from the architecture doc's YAML (`signals: [filename_tokens, column_signature, row_count_ratio]`). Task 4 extends the single existing config parser (no duplicate validation logic introduced anywhere). Task 5 is the actual "guess pairs" algorithm. Task 6-7 deliver the lineage manifest and wire everything into execution. Task 8 delivers the property-based tests called for by the Phase 2 roadmap line. Task 9 documents it.
- **No new validation call sites:** confirmed `api/schemas.py` and `etl_framework/runner/job_validation.py` need zero changes — both already delegate entirely to `FileMappingSpec.from_params`, which Task 4 extends in place.
- **Backward compatibility:** every `strategy: "explicit"` job (Phase 1) is untouched — `pair_files` itself is not modified anywhere in this plan, only called conditionally alongside the new `pair_files_automated`. The manifest write is unconditional (fires for `explicit` jobs too, per the architecture doc's original design intent), so Phase 1 jobs gain manifest output as a side effect but no behavior change to their `ReconciliationResult`.
- **Type/name consistency:** `AutomatedMappingSpec`, `SimilarityScore`, `KNOWN_SIMILARITY_SIGNALS`, `pair_files_automated`, `FileMappingManifestWriter` are named identically at every definition and call site across Tasks 3-7.
- **Deferred, not silently dropped:** S3/SFTP, per-pair parallelism, shard-collapse-under-automated-matching, and UI/manifest surfacing are named explicitly in the "out of scope" section above and in the doc update — not left as unstated gaps.

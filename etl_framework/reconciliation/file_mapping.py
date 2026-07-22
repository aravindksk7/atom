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

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus

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

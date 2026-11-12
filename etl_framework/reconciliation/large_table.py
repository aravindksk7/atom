"""Reconciliation for tables too large to hold in memory.

Two strategies, both ending in the same exact comparison inside an on-disk
DuckDB database:

``streaming``
    Read every row of both sides in cursor batches, normalise each batch and
    write it to a Parquet file, then let DuckDB join the two sides. Memory
    stays near one batch on the Python side; DuckDB spills to disk past its
    memory limit.

``pushdown``
    First ask each database for a per-bucket row count and hash sum (see
    ``pushdown_sql``). Buckets that agree are counted as matched without
    moving a row; only the rows of buckets that differ are streamed into
    DuckDB and compared. Falls back to ``streaming`` when either side is not
    Oracle, PostgreSQL or SQL Server, or when the summary query fails.

``auto`` picks ``pushdown`` where it is available and ``streaming`` elsewhere.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import duckdb
import pandas as pd
from sqlalchemy import text

from etl_framework.reconciliation import pushdown_sql as pd_sql
from etl_framework.reconciliation.backends.duckdb_backend import DuckDBBackend, duckdb_type_kind
from etl_framework.reconciliation.compare_utils import build_mismatch_summary
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus

if TYPE_CHECKING:  # pragma: no cover
    from etl_framework.reconciliation.engine import ReconciliationEngine

logger = logging.getLogger("etl_framework.reconciliation.large_table")

STRATEGIES = ("in_memory", "streaming", "pushdown", "auto")

SRC_REL = "atom_src"
TGT_REL = "atom_tgt"
SRC_MARKER = "__atom_in_src__"
TGT_MARKER = "__atom_in_tgt__"

# Above this many differing buckets the fetch reads the whole query once and
# drops unwanted buckets client-side, instead of sending a huge IN list.
MAX_SERVER_SIDE_BUCKETS = 5000


class PushdownUnavailable(Exception):
    """Pushdown cannot run for this query; the caller streams instead."""


@dataclass
class _Side:
    label: str
    engine: Any
    rel: str
    marker: str
    query: str = ""
    files: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    # Loaded column name -> name used in the comparison, for target columns
    # whose spelling differs from the source's only by case.
    rename: dict[str, str] = field(default_factory=dict)
    fetched_rows: int = 0


@dataclass
class _Summary:
    buckets: dict[int, tuple[int, int, int]]

    @property
    def row_count(self) -> int:
        return sum(entry[0] for entry in self.buckets.values())


class LargeTableReconciler:
    """Runs one reconciliation for a ``ReconciliationEngine`` whose strategy
    is not ``in_memory``. Reads the engine's settings directly so the two
    paths cannot drift on keys, exclusions, tolerances or schema policy."""

    def __init__(self, engine: "ReconciliationEngine") -> None:
        self._e = engine
        self._params: dict | None = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def reconcile(
        self,
        query: str,
        query_name: str,
        params: dict | None,
        executed_at: datetime,
        target_query: str | None = None,
    ) -> ReconciliationResult:
        """``target_query`` runs on the target when the two sides need
        different SQL (SQL Compare); otherwise both sides run ``query``."""
        self._params = params
        workdir = tempfile.mkdtemp(prefix="atom-recon-", dir=self._e._spill_dir or None)
        con = duckdb.connect(os.path.join(workdir, "recon.duckdb"))
        try:
            self._configure(con, workdir)
            src = _Side("source", self._e._source_engine, SRC_REL, SRC_MARKER, query=query)
            tgt = _Side("target", self._e._target_engine, TGT_REL, TGT_MARKER, query=target_query or query)

            strategy = self._e._strategy
            if strategy in ("pushdown", "auto"):
                try:
                    return self._pushdown(con, workdir, src, tgt, query, query_name, params, executed_at)
                except PushdownUnavailable as exc:
                    log = logger.warning if strategy == "pushdown" else logger.info
                    log("Reconciliation '%s': pushdown unavailable (%s); streaming instead.", query_name, exc)
            return self._streaming(con, workdir, src, tgt, query, query_name, params, executed_at)
        finally:
            con.close()
            shutil.rmtree(workdir, ignore_errors=True)

    def _configure(self, con, workdir: str) -> None:
        spill = os.path.join(workdir, "spill").replace("\\", "/")
        con.execute("SET TimeZone = 'UTC'")
        con.execute(f"SET temp_directory = '{spill}'")
        con.execute("SET preserve_insertion_order = false")
        if self._e._duckdb_memory_limit:
            limit = str(self._e._duckdb_memory_limit).replace("'", "")
            con.execute(f"SET memory_limit = '{limit}'")
        else:
            # DuckDB defaults to 80% of RAM, which leaves too little for the two
            # database reads and pandas batches beside it on a small server.
            # A third of that default (~27% of RAM) plus spilling is safer.
            row = con.execute("SELECT current_setting('memory_limit')").fetchone()
            default_bytes = _parse_size(row[0]) if row else None
            if default_bytes:
                con.execute(f"SET memory_limit = '{max(256, default_bytes // 3 // 2**20)}MB'")

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _streaming(self, con, workdir, src, tgt, query, query_name, params, executed_at):
        self._load_sides(con, workdir, src, tgt, lambda side: (side.query, None))
        schema_diff, common = self._schema(src, tgt, query_name)
        self._create_views(con, src, tgt, common)
        result = self._compare(con, src, tgt, common)
        src_count = self._count(con, SRC_REL)
        tgt_count = self._count(con, TGT_REL)
        logger.info(
            "Reconciliation '%s' (streaming): src=%d tgt=%d matched=%d mit=%d mis=%d vmc=%d",
            query_name, src_count, tgt_count, result.matched_count,
            result.missing_in_target_count, result.missing_in_source_count, result.value_mismatch_count,
        )
        return self._result(
            query_name, executed_at, schema_diff,
            src_count, tgt_count,
            result.matched_count, result.missing_in_target_count,
            result.missing_in_source_count, result.value_mismatch_count,
            result.mismatches, result.mismatch_summary,
        )

    # ------------------------------------------------------------------
    # Pushdown
    # ------------------------------------------------------------------

    def _pushdown(self, con, workdir, src, tgt, query, query_name, params, executed_at):
        dialects = {side.label: pd_sql.engine_dialect(side.engine) for side in (src, tgt)}
        unsupported = {k: v for k, v in dialects.items() if v not in pd_sql.SUPPORTED_DIALECTS}
        if unsupported:
            raise PushdownUnavailable(f"unsupported database dialect(s): {unsupported}")

        try:
            described = _parallel(
                lambda side: self._describe(side, dialects[side.label], side.query, params), [src, tgt],
            )
        except Exception as exc:
            raise PushdownUnavailable(f"could not read the query's columns: {exc}") from exc
        src_cols = {c.name: c for c in described[0] if not self._e._is_excluded_column(c.name)}
        tgt.rename = _case_alignment(list(src_cols), [c.name for c in described[1]])
        tgt_cols = {}
        for c in described[1]:
            if self._e._is_excluded_column(c.name):
                continue
            aligned = tgt.rename.get(c.name, c.name)
            tgt_cols[aligned] = pd_sql.ColumnInfo(aligned, c.db_name, c.kind)
        schema_diff = self._e._validate_schemas(
            pd.DataFrame(columns=list(src_cols)), pd.DataFrame(columns=list(tgt_cols)), query_name,
        )
        common = [name for name in src_cols if name in tgt_cols]
        keys = list(self._e._key_columns)
        missing_keys = [k for k in keys if k not in common]
        if missing_keys:
            raise KeyError(f"key column(s) {missing_keys} not in both sides of '{query_name}'")
        for name in common:
            src_cols[name], tgt_cols[name] = pd_sql.align_kinds(src_cols[name], tgt_cols[name])
            for side_cols, label in ((src_cols, "source"), (tgt_cols, "target")):
                if side_cols[name].kind == pd_sql.UNSUPPORTED:
                    raise PushdownUnavailable(f"{label} column {name!r} has a type pushdown cannot hash")
        # Large objects are fetched as their MD5 unless a text option has to
        # see the real text first.
        text_options = self._e._case_insensitive_columns | self._e._whitespace_normalize_columns
        hashed = frozenset(
            name for name in common
            if name not in keys
            and (src_cols[name].kind == pd_sql.BINARY or tgt_cols[name].kind == pd_sql.BINARY
                 or (name not in text_options
                     and src_cols[name].kind in pd_sql.HASHED_KINDS
                     and tgt_cols[name].kind in pd_sql.HASHED_KINDS))
        )

        values = [name for name in common if name not in keys]
        scale = pd_sql.numeric_scale_for_tolerance(self._smallest_tolerance())
        buckets = self._e._pushdown_buckets
        layout = {
            "source": ([src_cols[k] for k in keys], [src_cols[v] for v in values], [src_cols[c] for c in common]),
            "target": ([tgt_cols[k] for k in keys], [tgt_cols[v] for v in values], [tgt_cols[c] for c in common]),
        }

        def summarise(side: _Side) -> _Summary:
            key_info, value_info, _ = layout[side.label]
            dialect = dialects[side.label]
            sql = pd_sql.summary_query(dialect, side.query, key_info, value_info, buckets, scale)
            frame = _execute(side.engine, sql, params)
            frame.columns = [str(c).lower() for c in frame.columns]
            return _Summary({
                int(row.bucket): (int(row.row_count), int(row.hash_a or 0), int(row.hash_b or 0))
                for row in frame.itertuples(index=False)
            })

        try:
            src_summary, tgt_summary = _parallel(summarise, [src, tgt])
        except Exception as exc:
            hint = ""
            if "DBMS_CRYPTO" in str(exc).upper():
                hint = (" (hashing BLOB/CLOB columns on Oracle needs "
                        "GRANT EXECUTE ON SYS.DBMS_CRYPTO to the connecting user)")
            raise PushdownUnavailable(f"bucket summary query failed{hint}: {exc}") from exc

        all_buckets = set(src_summary.buckets) | set(tgt_summary.buckets)
        differing = sorted(
            b for b in all_buckets if src_summary.buckets.get(b) != tgt_summary.buckets.get(b)
        )
        src_count, tgt_count = src_summary.row_count, tgt_summary.row_count
        unchanged_rows = sum(
            entry[0] for b, entry in src_summary.buckets.items() if b not in set(differing)
        )
        logger.info(
            "Reconciliation '%s' (pushdown): src=%d tgt=%d buckets=%d differing=%d",
            query_name, src_count, tgt_count, len(all_buckets), len(differing),
        )
        if not differing:
            return self._result(
                query_name, executed_at, schema_diff, src_count, tgt_count,
                src_count, 0, 0, 0, [], None, compared_columns=values,
            )

        only = differing if len(differing) <= MAX_SERVER_SIDE_BUCKETS else None
        wanted = set(differing)

        def fetch_for(side: _Side) -> tuple[str, set[int] | None]:
            key_info, _, all_info = layout[side.label]
            sql = pd_sql.fetch_query(
                dialects[side.label], side.query, all_info, key_info, buckets, scale, only, hashed,
            )
            return sql, (None if only else wanted)

        self._load_sides(con, workdir, src, tgt, fetch_for)
        self._create_views(con, src, tgt, common)
        result = self._compare(con, src, tgt, common)

        matched = result.matched_count + unchanged_rows
        by_column = {
            col: count
            for col, count in ((result.mismatch_summary or {}).get("by_column") or {}).items()
            if col != "<row>"
        }
        summary = build_mismatch_summary(
            result.missing_in_target_count,
            result.missing_in_source_count,
            result.value_mismatch_count,
            by_column,
            {str(col): matched for col in values},
        )
        logger.info(
            "Reconciliation '%s' (pushdown): fetched src=%d tgt=%d rows from %d bucket(s); "
            "matched=%d mit=%d mis=%d vmc=%d",
            query_name, src.fetched_rows, tgt.fetched_rows, len(differing), matched,
            result.missing_in_target_count, result.missing_in_source_count, result.value_mismatch_count,
        )
        return self._result(
            query_name, executed_at, schema_diff, src_count, tgt_count,
            matched, result.missing_in_target_count, result.missing_in_source_count,
            result.value_mismatch_count, result.mismatches, summary,
        )

    def _describe(self, side: _Side, dialect: str, query: str, params: dict | None) -> list[pd_sql.ColumnInfo]:
        sql = pd_sql.sample_query(dialect, query, 0)
        with side.engine._engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            try:
                names = [str(name) for name in result.keys()]
                description = result.cursor.description or []
            finally:
                result.close()
        names = self._configured_names(names)
        return [
            pd_sql.ColumnInfo(name=name, db_name=str(desc[0]), kind=pd_sql.column_kind(dialect, desc[1]))
            for name, desc in zip(names, description)
        ]

    def _configured_names(self, names: list[str]) -> list[str]:
        """Respell columns that match a configured name (key, text option,
        segment, tolerance, change tracking) up to case, as configured.

        Oracle reports upper-case names that SQLAlchemy lower-cases, so a key
        typed as ``CUST_ID`` must still find ``cust_id``. Ambiguous matches
        (two columns differing only by case) are left alone.
        """
        e = self._e
        configured = [
            *e._key_columns, *e._case_insensitive_columns, *e._whitespace_normalize_columns,
            *e._segment_columns, *e._column_tolerances,
        ]
        if e._change_tracking_column:
            configured.append(e._change_tracking_column)
        spellings: dict[str, set[str]] = {}
        for name in configured:
            spellings.setdefault(str(name).lower(), set()).add(str(name))
        lowered = [str(n).lower() for n in names]
        out = []
        for name, low in zip(names, lowered):
            options = spellings.get(low)
            if options and len(options) == 1 and lowered.count(low) == 1:
                out.append(next(iter(options)))
            else:
                out.append(str(name))
        return out

    def _smallest_tolerance(self) -> float:
        tolerances = [self._e._float_tolerance, *self._e._column_tolerances.values()]
        positive = [t for t in tolerances if t and t > 0]
        return min(positive) if positive else self._e._float_tolerance

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_sides(self, con, workdir, src: _Side, tgt: _Side, plan) -> None:
        """Stream both sides into Parquet files concurrently.

        ``plan(side)`` returns ``(sql, keep_buckets)``: the query to stream and,
        when not None, the bucket numbers whose rows are kept.
        """
        def load(side: _Side) -> None:
            sql, keep = plan(side)
            cursor = con.cursor()
            try:
                self._load_side(cursor, workdir, side, sql, keep)
            finally:
                cursor.close()

        _parallel(load, [src, tgt])

    def _load_side(self, cursor, workdir: str, side: _Side, sql: str, keep: set[int] | None) -> None:
        bucket_col = pd_sql.BUCKET_ALIAS
        for batch in _iter_batches(side.engine, sql, self._params, self._e._stream_batch_size):
            if bucket_col in batch.columns:
                if keep is not None:
                    batch = batch[batch[bucket_col].astype("int64").isin(keep)]
                batch = batch.drop(columns=[bucket_col])
            respelled = self._configured_names([str(c) for c in batch.columns])
            if respelled != [str(c) for c in batch.columns]:
                batch = batch.set_axis(respelled, axis=1)
            drop = [c for c in batch.columns if self._e._is_excluded_column(c)]
            if drop:
                batch = batch.drop(columns=drop)
            if not side.columns:
                side.columns = [str(c) for c in batch.columns]
            if side.files and batch.empty:
                continue
            batch = self._e._preprocess_frame(self._e._normalizer.normalize(batch.reset_index(drop=True)))
            path = os.path.join(workdir, f"{side.label}_{len(side.files):06d}.parquet").replace("\\", "/")
            view = f"atom_batch_{side.label}"
            cursor.register(view, batch)
            try:
                cursor.execute(f"COPY (SELECT * FROM {view}) TO '{path}' (FORMAT parquet)")
            finally:
                cursor.unregister(view)
            side.files.append(path)
            side.fetched_rows += len(batch)

    # ------------------------------------------------------------------
    # Comparing
    # ------------------------------------------------------------------

    def _schema(self, src: _Side, tgt: _Side, query_name: str) -> tuple[dict | None, list[str]]:
        tgt.rename = _case_alignment(src.columns, tgt.columns)
        tgt_columns = [tgt.rename.get(c, c) for c in tgt.columns]
        schema_diff = self._e._validate_schemas(
            pd.DataFrame(columns=src.columns), pd.DataFrame(columns=tgt_columns), query_name,
        )
        common = [c for c in src.columns if c in tgt_columns]
        missing_keys = [k for k in self._e._key_columns if k not in common]
        if missing_keys:
            raise KeyError(f"key column(s) {missing_keys} not in both sides of '{query_name}'")
        return schema_diff, common

    def _create_views(self, con, src: _Side, tgt: _Side, common: list[str]) -> None:
        for side in (src, tgt):
            loaded = {aligned: original for original, aligned in side.rename.items()}
            cols = ", ".join(
                f"{_q(loaded[c])} AS {_q(c)}" if c in loaded else _q(c) for c in common
            )
            files = ", ".join(f"'{f}'" for f in side.files)
            con.execute(
                f"CREATE OR REPLACE VIEW {side.rel} AS "
                f"SELECT {cols}, TRUE AS {_q(side.marker)} "
                f"FROM read_parquet([{files}], union_by_name = true)"
                f"{self._incremental_filter(common)}"
            )

    def _incremental_filter(self, common: list[str]) -> str:
        col, since = self._e._change_tracking_column, self._e._since
        if col is None or since is None:
            return ""
        if col not in common:
            logger.warning("change_tracking_column '%s' not in result; incremental filter skipped.", col)
            return ""
        since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        literal = since_utc.astimezone(timezone.utc).isoformat()
        return f" WHERE {_q(col)} IS NULL OR CAST({_q(col)} AS TIMESTAMPTZ) >= TIMESTAMPTZ '{literal}'"

    def _compare(self, con, src: _Side, tgt: _Side, common: list[str]):
        self._warn_duplicates(con)
        types = {row[0]: row[1] for row in con.execute(f"DESCRIBE {SRC_REL}").fetchall()}
        kinds = {str(c): duckdb_type_kind(types.get(c, "")) for c in common}
        backend = DuckDBBackend(
            key_columns=list(self._e._key_columns),
            float_tolerance=self._e._float_tolerance,
            null_equals_null=self._e._null_equals_null,
            mismatch_row_limit=self._e._mismatch_row_limit,
            column_tolerances=self._e._column_tolerances,
            datetime_tolerance_seconds=self._e._datetime_tolerance.total_seconds(),
        )
        result = backend.compare_relations(
            con, SRC_REL, TGT_REL,
            source_columns=common, target_columns=common,
            src_marker=SRC_MARKER, tgt_marker=TGT_MARKER, kinds=kinds,
        )
        if self._e._segment_columns and result.mismatches:
            self._attach_segments(con, result.mismatches, common)
        return result

    def _warn_duplicates(self, con) -> None:
        keys = ", ".join(_q(k) for k in self._e._key_columns)
        for rel, label in ((SRC_REL, "source"), (TGT_REL, "target")):
            dups = con.execute(
                f"SELECT COALESCE(SUM(n - 1), 0) FROM "
                f"(SELECT COUNT(*) AS n FROM {rel} GROUP BY {keys} HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            if dups:
                logger.warning(
                    "%s has %d duplicate key rows — counts will be inflated. Deduplicate the query.",
                    label, int(dups),
                )

    def _attach_segments(self, con, mismatches: list[MismatchRecord], common: list[str]) -> None:
        try:
            seg_cols = [c for c in self._e._segment_columns if c in common]
            keys = list(self._e._key_columns)
            if not seg_cols:
                return
            wanted = pd.DataFrame(
                [tuple(m.key_values.get(k) for k in keys) for m in mismatches], columns=keys,
            ).drop_duplicates()
            con.register("atom_seg_keys", wanted)
            try:
                lookup: dict[tuple, dict] = {}
                select = ", ".join(f"r.{_q(c)}" for c in keys + seg_cols)
                join = " AND ".join(f"r.{_q(k)} = w.{_q(k)}" for k in keys)
                for rel in (TGT_REL, SRC_REL):  # source wins: written last
                    rows = DuckDBBackend._rows(
                        con, f"SELECT {select} FROM {rel} r JOIN atom_seg_keys w ON {join}",
                    )
                    for row in rows:
                        lookup[tuple(row[:len(keys)])] = dict(zip(seg_cols, row[len(keys):]))
            finally:
                con.unregister("atom_seg_keys")
            for m in mismatches:
                values = lookup.get(tuple(m.key_values.get(k) for k in keys))
                if values is not None:
                    m.segment_values = values
        except Exception:  # pragma: no cover - drill-down enrichment must not fail a run
            logger.warning("segment value enrichment failed; skipping", exc_info=True)

    @staticmethod
    def _count(con, rel: str) -> int:
        return int(con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0])

    def _result(
        self, query_name, executed_at, schema_diff, src_count, tgt_count,
        matched, mit, mis, value_count, mismatches, summary, compared_columns=None,
    ) -> ReconciliationResult:
        total = mit + mis + value_count
        if summary is None:
            summary = build_mismatch_summary(
                mit, mis, value_count, {}, {str(c): matched for c in (compared_columns or [])},
            )
        return ReconciliationResult(
            query_name=query_name,
            source_env=getattr(getattr(self._e._source_engine, "_env", None), "name", "source"),
            target_env=getattr(getattr(self._e._target_engine, "_env", None), "name", "target"),
            source_row_count=int(src_count),
            target_row_count=int(tgt_count),
            matched_count=int(matched),
            missing_in_target_count=int(mit),
            missing_in_source_count=int(mis),
            value_mismatch_count=int(value_count),
            mismatches=list(mismatches)[: self._e._mismatch_row_limit],
            status=TestStatus.PASSED if total == 0 else TestStatus.FAILED,
            executed_at=executed_at,
            duration_seconds=0.0,
            schema_diff=schema_diff,
            mismatch_summary=summary,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_SIZE_UNITS = {
    "B": 1, "BYTES": 1,
    "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
}


def _parse_size(value: object) -> int | None:
    """Bytes in a DuckDB size string such as ``'12.7 GiB'``."""
    parts = str(value).strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(float(parts[0]) * _SIZE_UNITS[parts[1].upper()])
    except (KeyError, ValueError):
        return None


def _case_alignment(source_names: list[str], target_names: list[str]) -> dict[str, str]:
    """Target column -> source spelling, for names that differ only by case.

    Mirrors the SQL Compare tab's in-memory alignment: skipped when the match
    is ambiguous or the source spelling already exists on the target.
    """
    by_lower: dict[str, list[str]] = {}
    for name in source_names:
        by_lower.setdefault(str(name).lower(), []).append(str(name))
    target_lowered = [str(n).lower() for n in target_names]
    existing = {str(n) for n in target_names}
    rename: dict[str, str] = {}
    for name, low in zip(target_names, target_lowered):
        options = by_lower.get(low)
        if not options or len(options) != 1 or target_lowered.count(low) != 1:
            continue
        wanted = options[0]
        if wanted != str(name) and wanted not in existing:
            rename[str(name)] = wanted
    return rename


def _q(identifier: object) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _execute(engine, sql: str, params: dict | None) -> pd.DataFrame:
    if params is not None:
        return engine.execute_query(sql, params)
    return engine.execute_query(sql)


def _iter_batches(engine, sql: str, params: dict | None, batch_size: int):
    iter_batches = getattr(engine, "iter_query_batches", None)
    if iter_batches is None:
        yield _execute(engine, sql, params)
        return
    yield from iter_batches(sql, params, batch_size=batch_size)


def _parallel(fn, sides: list) -> list:
    """Run ``fn`` for both sides at once; re-raise the first failure."""
    with ThreadPoolExecutor(max_workers=len(sides)) as pool:
        futures = [pool.submit(fn, side) for side in sides]
        return [f.result() for f in futures]

"""Isolated subprocess worker for BO PDF reading, previewing, and self-test."""
from __future__ import annotations

import base64
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from etl_framework.bo_layout.assemble import assemble, candidate_tables
from etl_framework.bo_layout.extract import extract_tables
from etl_framework.bo_layout.model import ColumnInfo, PdfReadError, PdfReadOptions, PdfSourceInfo
from etl_framework.bo_layout.values import to_frame

# Generated once via build_bo_pdf(tables=[{"header": ["Id", "V"], "rows": [["A1", "1.50"], ["A2", "2.50"]]}])
SELFTEST_PDF_B64 = (
    "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwKL0Yx"
    "IDIgMCBSIC9GMiAzIDAgUgo+PgplbmRvYmoKMiAwIG9iago8PAovQmFzZUZvbnQgL0hlbHZldGljYSAvRW5jb2RpbmcgL1dpbkFu"
    "c2lFbmNvZGluZyAvTmFtZSAvRjEgL1N1YnR5cGUgL1R5cGUxIC9UeXBlIC9Gb250Cj4+CmVuZG9iagozIDAgb2JqCjw8Ci9CYXNl"
    "Rm9udCAvSGVsdmV0aWNhLUJvbGQgL0VuY29kaW5nIC9XaW5BbnNpRW5jb2RpbmcgL05hbWUgL0YyIC9TdWJ0eXBlIC9UeXBlMSAv"
    "VHlwZSAvRm9udAo+PgplbmRvYmoKNCAwIG9iago8PAovQ29udGVudHMgOCAwIFIgL01lZGlhQm94IFsgMCAwIDYxMiA3OTIgXSAv"
    "UGFyZW50IDcgMCBSIC9SZXNvdXJjZXMgPDwKL0ZvbnQgMSAwIFIgL1Byb2NTZXQgWyAvUERGIC9UZXh0IC9JbWFnZUIgL0ltYWdl"
    "QyAvSW1hZ2VJIF0KPj4gL1JvdGF0ZSAwIC9UcmFucyA8PAoKPj4gCiAgL1R5cGUgL1BhZ2UKPj4KZW5kb2JqCjUgMCBvYmoKPDwK"
    "L1BhZ2VNb2RlIC9Vc2VOb25lIC9QYWdlcyA3IDAgUiAvVHlwZSAvQ2F0YWxvZwo+PgplbmRvYmoKNiAwIG9iago8PAovQXV0aG9y"
    "IChhbm9ueW1vdXMpIC9DcmVhdGlvbkRhdGUgKEQ6MjAyNjExMjcyMDUyMTErMTEnMDAnKSAvQ3JlYXRvciAoYW5vbnltb3VzKSAv"
    "S2V5d29yZHMgKCkgL01vZERhdGUgKEQ6MjAyNjExMjcyMDUyMTErMTEnMDAnKSAvUHJvZHVjZXIgKFJlcG9ydExhYiBQREYgTGli"
    "cmFyeSAtIFwob3BlbnNvdXJjZVwpKSAKICAvU3ViamVjdCAodW5zcGVjaWZpZWQpIC9UaXRsZSAodW50aXRsZWQpIC9UcmFwcGVk"
    "IC9GYWxzZQo+PgplbmRvYmoKNyAwIG9iago8PAovQ291bnQgMSAvS2lkcyBbIDQgMCBSIF0gL1R5cGUgL1BhZ2VzCj4+CmVuZG9i"
    "ago4IDAgb2JqCjw8Ci9GaWx0ZXIgWyAvQVNDSUk4NURlY29kZSAvRmxhdGVEZWNvZGUgXSAvTGVuZ3RoIDMxMAo+PgpzdHJlYW0K"
    "R2FzMkNiQStwSyY0UT9pTVJ1UWc6aFRGaUEoYStPIWExNU41TDtHN1RoXjFHb180OFxgQWc/ODdPJ21aUjxvYlIhWT1hRCRaI3NM"
    "KnQxJmgiNSU7bWcjNjQ5Y08mRSklMDZSbiteaFxhSlc1PTckcURdZDZYJFNiYzBhNkArZkczVW8pXnA+ZWtiJj9jaS1qV08kQGM8"
    "WGBgYCZlN0NrMzg2XWJjWS8rUEswJlJdWmJxJjE6QWkpQjdbWydvWFpqIydEVi9eNipGS1h0LDw1KFRRQF5aMCZXUW1qLlBrcGkm"
    "KCk8JW0jb29uSUYrLytEMTNESlYnVlk9QUJEZVZAQ0pRXChuUFc5NGEpUmM0RWYrbDU6bE5lVVUrNz1DLUROUHM5TDxhM1BaM1RO"
    "Vy4wMWpdXFd+PmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDkKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDYxIDAwMDAwIG4g"
    "CjAwMDAwMDAxMDIgMDAwMDAgbiAKMDAwMDAwMDIwOSAwMDAwMCBuIAowMDAwMDAwMzIxIDAwMDAwIG4gCjAwMDAwMDA1MTQgMDAw"
    "MDAgbiAKMDAwMDAwMDU4MiAwMDAwMCBuIAowMDAwMDAwODQzIDAwMDAwIG4gCjAwMDAwMDA5MDIgMDAwMDAgbiAKdHJhaWxlcgo8"
    "PAovSUQgCls8NjhiMjg3ZjAxMWUzNDUzOThkY2U1ZTVhOWQzNWVlMzg+PDY4YjI4N2YwMTFlMzQ1Mzk4ZGNlNWU1YTlkMzVlZTM4"
    "Pl0KJSBSZXBvcnRMYWIgZ2VuZXJhdGVkIFBERiBkb2N1bWVudCAtLSBkaWdlc3QgKG9wZW5zb3VyY2UpCgovSW5mbyA2IDAgUgov"
    "Um9vdCA1IDAgUgovU2l6ZSA5Cj4+CnN0YXJ0eHJlZgoxMzAyCiUlRU9GCg=="
)

_PREVIEW_PASSTHROUGH_ERRORS = frozenset({"no_data_table", "several_tables", "table_not_found"})


def _apply_memory_limit() -> None:
    try:
        import resource

        if hasattr(resource, "RLIMIT_DATA"):
            limit = 2 * 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    except (ImportError, ValueError, OSError):
        pass


def _max_pages() -> int:
    raw = os.environ.get("ATOM_PDF_MAX_PAGES", "1000")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1000


def _column_type_str(ci: ColumnInfo) -> str:
    if ci.kind == "number":
        decimals = ci.displayed_decimals if ci.displayed_decimals is not None else 0
        return f"number:{decimals}dp"
    if ci.kind == "date":
        return f"date:{ci.date_format}"
    return "text"


def _column_widths_map(header: list[str], col_x: list[float]) -> dict[str, float]:
    widths: dict[str, float] = {}
    for idx, name in enumerate(header):
        if idx + 1 < len(col_x):
            widths[name] = round(float(col_x[idx + 1] - col_x[idx]), 2)
        else:
            widths[name] = 0.0
    return widths


def _is_missing(val: Any) -> bool:
    return val is None or val is pd.NA or (isinstance(val, float) and math.isnan(val))


def _df_to_json_rows(df: pd.DataFrame, column_info: dict[str, ColumnInfo]) -> list[list[Any]]:
    if len(df) == 0 or len(df.columns) == 0:
        return []
    col_lists: list[list[Any]] = []
    for col in df.columns:
        ci = column_info.get(str(col))
        raw_vals = df[col].tolist()
        if ci is not None and ci.kind == "number":
            col_lists.append([None if math.isnan(v) else float(v) for v in raw_vals])
        else:
            col_lists.append([None if _is_missing(v) else v for v in raw_vals])
    return [list(row) for row in zip(*col_lists)]


def _suggest_key(df: pd.DataFrame) -> str | None:
    if len(df) == 0:
        return None
    for col in df.columns:
        vals = df[col].tolist()
        seen: set[Any] = set()
        valid = True
        for val in vals:
            if _is_missing(val) or (isinstance(val, str) and val.strip() == ""):
                valid = False
                break
            if val in seen:
                valid = False
                break
            seen.add(val)
        if valid:
            return str(col)
    return None


def _run_read(pdf_path: str, options: PdfReadOptions, max_pages: int) -> tuple[dict[str, Any], str | None]:
    extract_res = extract_tables(pdf_path, table_settings=options.table_settings, max_pages=max_pages)
    producer = extract_res.producer
    try:
        table, prompts, stats = assemble(extract_res, options)
        df, col_info, warnings = to_frame(table, options)
        table_meta = {
            "header": list(table.header),
            "pages": list(table.pages),
            "rows": len(df),
            "stitched_fragments": table.fragments,
            "column_types": {col: _column_type_str(col_info[col]) for col in table.header},
            "displayed_decimals": {
                col: col_info[col].displayed_decimals
                for col in table.header
                if col_info[col].kind == "number"
            },
            "column_widths": _column_widths_map(table.header, table.col_x),
        }
        info = PdfSourceInfo(
            file_name="",
            sha256="",
            bytes=0,
            pages=extract_res.page_count,
            producer=extract_res.producer,
            creator=extract_res.creator,
            table=table_meta,
            filled_cells=stats.filled_cells,
            dropped_empty_rows=stats.dropped_empty_rows,
            dropped_repeated_headers=stats.dropped_repeated_headers,
            ignored_tables=stats.ignored_tables,
            prompts=prompts,
            warnings=warnings,
            column_info=col_info,
        )
        return {
            "ok": True,
            "columns": [str(c) for c in df.columns],
            "rows": _df_to_json_rows(df, col_info),
            "column_info": {col: ci.to_dict() for col, ci in col_info.items()},
            "info": info.to_dict(),
        }, producer
    except PdfReadError as exc:
        raise _ProducerError(exc, producer) from exc


def _run_preview(pdf_path: str, options: PdfReadOptions, max_pages: int) -> tuple[dict[str, Any], str | None]:
    extract_res = extract_tables(pdf_path, table_settings=options.table_settings, max_pages=max_pages)
    producer = extract_res.producer
    try:
        cand_tables, prompts = candidate_tables(extract_res)
        tables_out = [
            {
                "index": idx,
                "header": list(t.header),
                "rows": len(t.rows),
                "pages": list(t.pages),
                "fragments": t.fragments,
            }
            for idx, t in enumerate(cand_tables, start=1)
        ]

        try:
            table, prompts_assembled, stats = assemble(extract_res, options)
        except PdfReadError as exc:
            if exc.code in _PREVIEW_PASSTHROUGH_ERRORS:
                return {
                    "ok": True,
                    "pages": extract_res.page_count,
                    "tables": tables_out,
                    "chosen": None,
                    "choose_error": exc.message,
                    "prompts": [{"label": p.label, "value": p.value} for p in prompts],
                    "warnings": [],
                }, producer
            raise

        df, col_info, warnings = to_frame(table, options)
        ignored_indices = {t["index"] for t in stats.ignored_tables}
        chosen_index = next((idx for idx in range(1, len(cand_tables) + 1) if idx not in ignored_indices), 1)
        chosen = {
            "index": chosen_index,
            "header": list(table.header),
            "column_types": {col: _column_type_str(col_info[col]) for col in table.header},
            "sample_rows": _df_to_json_rows(df.iloc[:10], col_info),
            "suggested_key": _suggest_key(df),
        }
        return {
            "ok": True,
            "pages": extract_res.page_count,
            "tables": tables_out,
            "chosen": chosen,
            "choose_error": None,
            "prompts": [{"label": p.label, "value": p.value} for p in prompts_assembled],
            "warnings": warnings,
        }, producer
    except PdfReadError as exc:
        raise _ProducerError(exc, producer) from exc


def _run_selftest(max_pages: int) -> dict[str, Any]:
    fd, tmp_name = tempfile.mkstemp(prefix="bo_selftest_", suffix=".pdf")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64decode(SELFTEST_PDF_B64))
        options = PdfReadOptions()
        extract_res = extract_tables(tmp_path, max_pages=max_pages)
        table, _, _ = assemble(extract_res, options)
        df, _, _ = to_frame(table, options)
        actual = df.values.tolist()
        expected = [["A1", 1.5], ["A2", 2.5]]
        if actual != expected:
            return {
                "ok": False,
                "code": "selftest_failed",
                "message": f"selftest mismatch: expected {expected}, got {actual}",
                "tables": [],
                "producer": extract_res.producer,
            }
        return {"ok": True}
    finally:
        tmp_path.unlink(missing_ok=True)


class _ProducerError(Exception):
    def __init__(self, err: PdfReadError, producer: str | None) -> None:
        super().__init__(err.message)
        self.err = err
        self.producer = producer


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 3:
        sys.stderr.write("usage: worker <read|preview|selftest> <pdf_path|-> <out_json_path>\n")
        return 2

    mode, pdf_path, out_path = args
    _apply_memory_limit()
    max_pages = _max_pages()

    raw_stdin = sys.stdin.read().strip()
    options = PdfReadOptions.from_dict(json.loads(raw_stdin)) if raw_stdin else PdfReadOptions()

    producer: str | None = None
    try:
        if mode == "read":
            payload, producer = _run_read(pdf_path, options, max_pages)
        elif mode == "preview":
            payload, producer = _run_preview(pdf_path, options, max_pages)
        elif mode == "selftest":
            payload = _run_selftest(max_pages)
        else:
            sys.stderr.write(f"unknown worker mode: {mode}\n")
            return 2
    except _ProducerError as exc:
        err = exc.err
        payload = {
            "ok": False,
            "code": err.code,
            "message": err.message,
            "tables": err.tables,
            "producer": exc.producer,
        }
    except PdfReadError as exc:
        payload = {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "tables": exc.tables,
            "producer": producer,
        }

    Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Process-isolated BO PDF reader, previewer, self-test, and error counters."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

import etl_framework
from etl_framework.bo_layout import cache
from etl_framework.bo_layout.model import ColumnInfo, PdfReadError, PdfReadOptions, PdfSourceInfo
from etl_framework.bo_layout.slots import worker_slot

logger = logging.getLogger(__name__)

ERROR_COUNTS: Counter[str] = Counter()

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 120
_DEFAULT_PREVIEW_TIMEOUT_S = 60
_MODE_TIMEOUT_ENV: dict[str, tuple[str, int]] = {
    "read": ("ATOM_PDF_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_S),
    "preview": ("ATOM_PDF_PREVIEW_TIMEOUT_SECONDS", _DEFAULT_PREVIEW_TIMEOUT_S),
}
_SELFTEST_TTL_S = 60.0
_SELFTEST_LOCK = threading.Lock()
_SELFTEST_CACHE: tuple[float, dict[str, Any]] | None = None


def _max_bytes() -> int:
    raw = os.environ.get("ATOM_PDF_MAX_BYTES")
    if raw is None:
        return _DEFAULT_MAX_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _env_timeout_s(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _build_worker_env() -> dict[str, str]:
    pkg_parent = str(Path(etl_framework.__file__).resolve().parent.parent)
    existing = os.environ.get("PYTHONPATH")
    pythonpath = os.pathsep.join([pkg_parent, existing] if existing else [pkg_parent])
    return {
        **os.environ,
        "ATOM_PDF_MAX_PAGES": os.environ.get("ATOM_PDF_MAX_PAGES", "1000"),
        "PYTHONPATH": pythonpath,
    }


def _last_stderr_line(stderr: str | None) -> str:
    if not stderr:
        return "PDF reader crashed"
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return lines[-1] if lines else "PDF reader crashed"


def _spawn_worker(
    mode: str,
    pdf_path: str,
    options: PdfReadOptions,
    timeout_s: float,
    orig_timeout_s: int,
    env: dict[str, str],
) -> dict[str, Any]:
    out_fd, out_path = tempfile.mkstemp(prefix="bo_out_", suffix=".json")
    os.close(out_fd)
    out_file = Path(out_path)
    try:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "etl_framework.bo_layout.worker", mode, pdf_path, out_path],
                input=json.dumps(options.to_dict()),
                text=True,
                capture_output=True,
                timeout=timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfReadError("timeout", f"the PDF could not be read in {orig_timeout_s} s") from exc

        if proc.returncode != 0:
            raise PdfReadError("worker_crash", _last_stderr_line(proc.stderr))

        try:
            payload = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PdfReadError("worker_crash", _last_stderr_line(proc.stderr)) from exc

        if not isinstance(payload, dict):
            raise PdfReadError("worker_crash", _last_stderr_line(proc.stderr))
        return payload
    finally:
        out_file.unlink(missing_ok=True)


def _run_mode(
    mode: str,
    raw: bytes,
    options: PdfReadOptions,
    *,
    file_name: str,
    timeout_s: int | None,
    slot_wait_s: float | None,
) -> tuple[dict[str, Any], str]:
    producer: str | None = None
    try:
        max_bytes = _max_bytes()
        if len(raw) > max_bytes:
            mb = len(raw) / (1024 * 1024)
            limit_mb = max_bytes / (1024 * 1024)
            raise PdfReadError("too_large", f"the PDF is {mb:.1f} MB; the limit is {limit_mb:.0f} MB")

        raw_sha256 = hashlib.sha256(raw).hexdigest()
        key = cache.cache_key(raw_sha256, mode, options)
        cached = cache.cache_get(key)
        if cached is not None:
            return cached, raw_sha256

        env_var, default_s = _MODE_TIMEOUT_ENV[mode]
        eff_timeout = _env_timeout_s(env_var, default_s) if timeout_s is None else timeout_s
        deadline = time.monotonic() + eff_timeout
        slot_deadline = min(deadline, time.monotonic() + slot_wait_s) if slot_wait_s is not None else deadline

        with worker_slot(slot_deadline):
            pdf_fd, pdf_path = tempfile.mkstemp(prefix="bo_in_", suffix=".pdf")
            pdf_file = Path(pdf_path)
            try:
                with os.fdopen(pdf_fd, "wb") as fh:
                    fh.write(raw)
                sub_timeout = max(1, deadline - time.monotonic())
                payload = _spawn_worker(
                    mode,
                    pdf_path,
                    options,
                    sub_timeout,
                    eff_timeout,
                    _build_worker_env(),
                )
            finally:
                pdf_file.unlink(missing_ok=True)

        if payload.get("ok") is not True:
            producer = payload.get("producer")
            raise PdfReadError(
                str(payload.get("code") or "worker_crash"),
                str(payload.get("message") or "PDF reader crashed"),
                payload.get("tables"),
            )

        cache.cache_put(key, payload)
        return payload, raw_sha256
    except PdfReadError as exc:
        ERROR_COUNTS[exc.code] += 1
        logger.warning("pdf_read_error code=%s producer=%s file=%s", exc.code, producer, file_name)
        raise


def _rebuild_dataframe(
    columns: list[str],
    rows: list[list[Any]],
    column_info: dict[str, ColumnInfo],
) -> pd.DataFrame:
    series_map: dict[str, pd.Series] = {}
    for idx, col in enumerate(columns):
        col_vals = [row[idx] for row in rows]
        ci = column_info.get(col)
        if ci is not None and ci.kind == "number":
            series_map[col] = pd.Series(
                [float("nan") if v is None else float(v) for v in col_vals],
                dtype="float64",
            )
        else:
            series_map[col] = pd.Series(
                [pd.NA if v is None else v for v in col_vals],
                dtype="object",
            )
    return pd.DataFrame(series_map, columns=columns)


def read_pdf_report(
    raw: bytes,
    options: PdfReadOptions,
    *,
    file_name: str = "report.pdf",
    timeout_s: int | None = None,
    slot_wait_s: float | None = None,
) -> tuple[pd.DataFrame, PdfSourceInfo]:
    """Read a BusinessObjects PDF report in an isolated subprocess and return (DataFrame, PdfSourceInfo)."""
    payload, raw_sha256 = _run_mode(
        "read",
        raw,
        options,
        file_name=file_name,
        timeout_s=timeout_s,
        slot_wait_s=slot_wait_s,
    )
    columns = list(payload["columns"])
    rows = list(payload["rows"])
    column_info = {k: ColumnInfo.from_dict(v) for k, v in (payload.get("column_info") or {}).items()}
    df = _rebuild_dataframe(columns, rows, column_info)

    info_dict = dict(payload["info"])
    info_dict["file_name"] = file_name
    info_dict["sha256"] = raw_sha256
    info_dict["bytes"] = len(raw)
    info = PdfSourceInfo.from_dict(info_dict)
    return df, info


def preview_pdf_report(
    raw: bytes,
    options: PdfReadOptions,
    *,
    file_name: str = "report.pdf",
    timeout_s: int | None = None,
    slot_wait_s: float = 10,
) -> dict[str, Any]:
    """Preview tables, prompts, column types, and suggested key for a BO PDF report."""
    payload, _ = _run_mode(
        "preview",
        raw,
        options,
        file_name=file_name,
        timeout_s=timeout_s,
        slot_wait_s=slot_wait_s,
    )
    return {
        "pages": payload["pages"],
        "tables": payload["tables"],
        "chosen": payload["chosen"],
        "choose_error": payload["choose_error"],
        "prompts": payload["prompts"],
        "warnings": payload["warnings"],
    }


def selftest(timeout_s: int = 30) -> dict[str, Any]:
    """Run an isolated worker self-test (bypassing slots and cache), memoised for 60 s."""
    global _SELFTEST_CACHE
    now = time.monotonic()
    with _SELFTEST_LOCK:
        if _SELFTEST_CACHE is not None:
            cached_at, cached_res = _SELFTEST_CACHE
            if (now - cached_at) < _SELFTEST_TTL_S:
                return dict(cached_res)

        t0 = time.monotonic()
        try:
            payload = _spawn_worker(
                "selftest",
                "-",
                PdfReadOptions(),
                float(timeout_s),
                timeout_s,
                _build_worker_env(),
            )
            elapsed = round(time.monotonic() - t0, 4)
            if payload.get("ok") is True:
                result: dict[str, Any] = {"ok": True, "seconds": elapsed, "error": None}
            else:
                result = {
                    "ok": False,
                    "seconds": elapsed,
                    "error": str(payload.get("message") or "selftest failed"),
                }
        except PdfReadError as exc:
            elapsed = round(time.monotonic() - t0, 4)
            result = {"ok": False, "seconds": elapsed, "error": exc.message}

        _SELFTEST_CACHE = (time.monotonic(), dict(result))
        return result

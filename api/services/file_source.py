from __future__ import annotations
import base64
import csv
import io
import json
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pandas as pd
from pandas.errors import ParserError
from fastapi import HTTPException

_WINDOWS_TEMP_BASE = Path("C:/temp") if os.name == "nt" else None


def _default_upload_base() -> Path | None:
    if "UPLOAD_BASE_DIR" in os.environ:
        return Path(os.environ["UPLOAD_BASE_DIR"]).resolve()
    if _WINDOWS_TEMP_BASE is not None and _WINDOWS_TEMP_BASE.exists():
        return _WINDOWS_TEMP_BASE.resolve()
    return None


# Filesystem-path uploads are scoped to UPLOAD_BASE_DIR when set. On local
# Windows installs, C:\temp is allowed by default because the Compare UI and
# docs use server-side temp paths for manual file comparisons.
_UPLOAD_BASE: Path | None = _default_upload_base()

_SUPPORTED_FORMATS_DETAIL = "Use .csv, .xlsx, .xls, .json, .xml, or .tsv"


def _read_csv_bytes(raw: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(raw))
    except ParserError:
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        start, delimiter = _find_csv_header(lines)
        if start is None:
            raise
        return pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=delimiter)


def _find_csv_header(lines: list[str]) -> tuple[int | None, str]:
    for delimiter in (",", "\t", ";", "|"):
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or set(stripped) <= {"-"} or delimiter not in line:
                continue
            expected_fields = len(next(csv.reader([line], delimiter=delimiter)))
            if expected_fields < 2:
                continue
            next_line = _next_nonempty(lines, idx + 1)
            if next_line is None:
                continue
            next_fields = len(next(csv.reader([next_line], delimiter=delimiter)))
            if next_fields == expected_fields:
                return idx, delimiter
    return None, ","


def _next_nonempty(lines: list[str], start: int) -> str | None:
    for line in lines[start:]:
        stripped = line.strip()
        if stripped and not set(stripped) <= {"-"}:
            return line
    return None


def _read_json_bytes(raw: bytes) -> pd.DataFrame:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Cannot parse JSON file") from exc

    if isinstance(data, list):
        if not data:
            return pd.DataFrame()
        if all(isinstance(item, dict) for item in data):
            return pd.json_normalize(data)
        return pd.DataFrame({"value": data})

    if isinstance(data, dict):
        records = _find_record_list(data)
        if records is not None:
            return pd.json_normalize(records)
        return pd.json_normalize(data)

    return pd.DataFrame({"value": [data]})


def _find_record_list(value: dict[str, Any]) -> list[dict[str, Any]] | None:
    for item in value.values():
        if isinstance(item, list) and all(isinstance(row, dict) for row in item):
            return item
        if isinstance(item, dict):
            nested = _find_record_list(item)
            if nested is not None:
                return nested
    return None


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _unique_path(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    idx = 2
    while f"{base}_{idx}" in existing:
        idx += 1
    return f"{base}_{idx}"


def _flatten_xml_element(element: ElementTree.Element, prefix: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {}

    for name, value in element.attrib.items():
        key = f"{prefix}.{_strip_namespace(name)}" if prefix else _strip_namespace(name)
        row[key] = value

    children = list(element)
    text = (element.text or "").strip()
    if text and not children:
        row[prefix or _strip_namespace(element.tag)] = text

    child_counts: dict[str, int] = {}
    for child in children:
        child_tag = _strip_namespace(child.tag)
        child_counts[child_tag] = child_counts.get(child_tag, 0) + 1

    seen: set[str] = set(row)
    for child in children:
        child_tag = _strip_namespace(child.tag)
        child_prefix = f"{prefix}.{child_tag}" if prefix else child_tag
        flattened = _flatten_xml_element(child, child_prefix)
        for key, value in flattened.items():
            output_key = key
            if child_counts[child_tag] > 1 or output_key in seen:
                output_key = _unique_path(key, seen)
            row[output_key] = value
            seen.add(output_key)

    return row


def _select_xml_records(root: ElementTree.Element) -> list[ElementTree.Element]:
    children = list(root)
    if not children:
        return [root]

    preferred = ("record", "row", "item", "entry")
    for tag in preferred:
        matches = [child for child in children if _strip_namespace(child.tag).lower() == tag]
        if matches:
            return matches

    counts: dict[str, int] = {}
    for child in children:
        tag = _strip_namespace(child.tag)
        counts[tag] = counts.get(tag, 0) + 1

    repeated_tags = {tag for tag, count in counts.items() if count > 1}
    if repeated_tags:
        return [child for child in children if _strip_namespace(child.tag) in repeated_tags]

    for child in children:
        grandchildren = list(child)
        if len(grandchildren) > 1:
            grandchild_tags = [_strip_namespace(grandchild.tag) for grandchild in grandchildren]
            if len(set(grandchild_tags)) == 1:
                return grandchildren

    return [root]


def _read_xml_bytes(raw: bytes) -> pd.DataFrame:
    head = raw[:512].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise HTTPException(status_code=400, detail="XML files with DTD or entity declarations are not supported")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=400, detail="Cannot parse XML file") from exc

    records = _select_xml_records(root)
    rows = [_flatten_xml_element(record) for record in records]
    return pd.DataFrame(rows)


def _read_tabular_bytes(raw: bytes, ext: str) -> pd.DataFrame:
    if ext == ".csv":
        return _read_csv_bytes(raw)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(raw))
    if ext == ".json":
        return _read_json_bytes(raw)
    if ext == ".xml":
        return _read_xml_bytes(raw)
    if ext in (".tsv", ".txt"):
        return pd.read_csv(io.BytesIO(raw), sep="\t")
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file format '{ext}'. {_SUPPORTED_FORMATS_DETAIL}",
    )


def _resolve_allowed_path(path: str) -> Path:
    if _UPLOAD_BASE is None:
        raise HTTPException(
            status_code=400,
            detail="Server-side file paths are disabled. Set UPLOAD_BASE_DIR or use content_b64 upload.",
        )
    base = _UPLOAD_BASE.resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file path. Allowed base directory: {base}",
        )
    return resolved


def read_tabular(
    path: str | None = None,
    content_b64: str | None = None,
    file_name: str | None = None,
) -> pd.DataFrame:
    """Read tabular files into a DataFrame from a filesystem path or base64-encoded bytes."""
    if path is None and content_b64 is None:
        raise HTTPException(status_code=400, detail="Provide path or content_b64")

    if content_b64 is not None:
        raw = base64.b64decode(content_b64)
        name = file_name or ""
        return _read_tabular_bytes(raw, Path(name).suffix.lower())

    p = _resolve_allowed_path(path)
    try:
        return _read_tabular_bytes(p.read_bytes(), p.suffix.lower())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

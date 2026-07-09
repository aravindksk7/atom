from __future__ import annotations
import base64
from collections import Counter
import csv
import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET

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
_UPLOAD_BASE: Path | None = (
    _default_upload_base()
)

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
        return pd.read_json(io.BytesIO(raw))
    except ValueError:
        try:
            return pd.read_json(io.BytesIO(raw), orient="records")
        except ValueError:
            raise HTTPException(status_code=400, detail="Cannot parse JSON file")


def _xml_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _select_xml_records(root: ET.Element) -> list[ET.Element]:
    children = list(root)
    if not children:
        return [root]

    preferred = ("record", "row", "item", "entry")
    for tag in preferred:
        matches = [child for child in children if _xml_name(child.tag).lower() == tag]
        if matches:
            return matches

    counts = Counter(_xml_name(child.tag) for child in children)
    repeated = [tag for tag, count in counts.items() if count > 1]
    if repeated:
        record_tag = max(repeated, key=lambda tag: counts[tag])
        return [child for child in children if _xml_name(child.tag) == record_tag]

    for node in children:
        nested = _select_xml_records(node)
        if len(nested) > 1:
            return nested

    return [root] if all(not list(child) for child in children) else children


def _flatten_xml_element(element: ET.Element) -> dict[str, str]:
    row: dict[str, str] = {}

    def put(key: str, value: str) -> None:
        value = value.strip()
        if not value:
            return
        if key in row:
            suffix = 2
            next_key = f"{key}_{suffix}"
            while next_key in row:
                suffix += 1
                next_key = f"{key}_{suffix}"
            key = next_key
        row[key] = value

    def walk(node: ET.Element, prefix: str) -> None:
        for attr_name, attr_value in node.attrib.items():
            attr_key = f"{prefix}_{_xml_name(attr_name)}" if prefix else _xml_name(attr_name)
            put(attr_key, attr_value)

        children = list(node)
        text = (node.text or "").strip()
        if not children:
            if prefix:
                put(prefix, text)
            return
        if text and prefix:
            put(prefix, text)
        for child in children:
            child_name = _xml_name(child.tag)
            child_key = f"{prefix}_{child_name}" if prefix else child_name
            walk(child, child_key)

    walk(element, "")
    return row


def _stream_xml_candidates(raw: bytes) -> list[tuple[str, dict[str, str]]]:
    """Flatten each direct child of the XML root as it finishes parsing and
    clear it immediately, so peak memory holds one record's worth of DOM at
    a time instead of the whole parsed document tree.
    """
    context = ET.iterparse(io.BytesIO(raw), events=("start", "end"))
    _, root = next(context)
    depth = 1
    candidates: list[tuple[str, dict[str, str]]] = []
    for event, elem in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 1:
            candidates.append((_xml_name(elem.tag), _flatten_xml_element(elem)))
            elem.clear()
    return candidates


def _read_xml_bytes_dom(raw: bytes) -> pd.DataFrame:
    root = ET.fromstring(raw)
    rows = [_flatten_xml_element(record) for record in _select_xml_records(root)]
    return pd.DataFrame(rows)


def _read_xml_bytes(raw: bytes) -> pd.DataFrame:
    try:
        candidates = _stream_xml_candidates(raw)
    except ET.ParseError:
        raise HTTPException(status_code=400, detail="Cannot parse XML file")

    if not candidates:
        # Root has no children (single flat record, or nested structure that
        # needs the recursive DOM heuristics) — these are small documents by
        # construction, so the one-shot DOM path is cheap here.
        return _read_xml_bytes_dom(raw)

    preferred = ("record", "row", "item", "entry")
    for tag in preferred:
        matches = [row for name, row in candidates if name.lower() == tag]
        if matches:
            return pd.DataFrame(matches)

    counts = Counter(name for name, _ in candidates)
    repeated = [tag for tag, count in counts.items() if count > 1]
    if repeated:
        record_tag = max(repeated, key=lambda tag: counts[tag])
        matches = [row for name, row in candidates if name == record_tag]
        return pd.DataFrame(matches)

    # No repeated top-level record tag — rare small-document edge case
    # (deeply nested single record). Candidates were already cleared during
    # streaming, so re-parse to run the recursive DOM selection heuristic.
    return _read_xml_bytes_dom(raw)


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
    """Read a tabular file into a DataFrame from a filesystem path or base64 bytes."""
    if path is None and content_b64 is None:
        raise HTTPException(status_code=400, detail="Provide path or content_b64")

    if content_b64 is not None:
        raw = base64.b64decode(content_b64)
        name = file_name or ""
        ext = Path(name).suffix.lower()
        return _read_tabular_bytes(raw, ext)

    p = _resolve_allowed_path(path)
    ext = p.suffix.lower()
    try:
        return _read_tabular_bytes(p.read_bytes(), ext)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

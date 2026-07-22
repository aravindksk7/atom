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


def _parse_base_dirs(value: str | None) -> list[Path]:
    if not value:
        return []
    bases: list[Path] = []
    for raw_part in value.split(os.pathsep):
        part = raw_part.strip().strip('"')
        if part:
            bases.append(Path(part).resolve())
    return bases


def _default_upload_base() -> Path | None:
    if "UPLOAD_BASE_DIR" in os.environ:
        return Path(os.environ["UPLOAD_BASE_DIR"]).resolve()
    if _WINDOWS_TEMP_BASE is not None and _WINDOWS_TEMP_BASE.exists():
        return _WINDOWS_TEMP_BASE.resolve()
    return None


def _default_upload_bases() -> tuple[Path, ...]:
    """Return configured server-side file roots.

    `SERVER_FILE_ALLOWED_DIRS` is the preferred on-prem setting and accepts
    an os.pathsep-separated list. `UPLOAD_BASE_DIRS` is accepted as a legacy
    plural alias, and `UPLOAD_BASE_DIR` remains the single-directory setting.
    """
    bases: list[Path] = []
    bases.extend(_parse_base_dirs(os.environ.get("SERVER_FILE_ALLOWED_DIRS")))
    bases.extend(_parse_base_dirs(os.environ.get("UPLOAD_BASE_DIRS")))
    default_base = _default_upload_base()
    if default_base is not None:
        bases.append(default_base)
    if "COMPARE_UPLOAD_ROOT" in os.environ:
        bases.append(Path(os.environ["COMPARE_UPLOAD_ROOT"]).resolve())
    bases.append(Path("reports/uploads").resolve())

    deduped: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        key = os.path.normcase(str(base))
        if key not in seen:
            seen.add(key)
            deduped.append(base)
    return tuple(deduped)


# Filesystem-path uploads are scoped to SERVER_FILE_ALLOWED_DIRS/UPLOAD_BASE_DIR
# when set. On local Windows installs, C:\temp is allowed by default because the
# Compare UI and docs use server-side temp paths for manual file comparisons.
_UPLOAD_BASES: tuple[Path, ...] = _default_upload_bases()
_UPLOAD_BASE: Path | None = _UPLOAD_BASES[0] if _UPLOAD_BASES else None

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


def _next_unique_path(base: str, existing: set[str], counters: dict[str, int]) -> str:
    """Return a stable duplicate column name without rescanning from _2 each time."""
    idx = counters.get(base, 1) + 1
    while f"{base}_{idx}" in existing:
        idx += 1
    counters[base] = idx
    return f"{base}_{idx}"


def _attribute_keyed_group_name(children: list[ElementTree.Element], tag: str) -> str | None:
    """Detect the common name/value-pair idiom: repeated same-tag siblings
    where a single attribute (e.g. ``id``) uniquely names each occurrence
    and the element has no children of its own, e.g.
    ``<field id="ISIN Code">AU000...</field>``. Returns the shared
    attribute name if every sibling with this tag qualifies, else None.
    """
    same_tag = [child for child in children if _strip_namespace(child.tag) == tag]
    attr_name: str | None = None
    seen_values: set[str] = set()
    for child in same_tag:
        if list(child) or len(child.attrib) != 1:
            return None
        ((name, value),) = child.attrib.items()
        if attr_name is None:
            attr_name = name
        elif name != attr_name:
            return None
        if value in seen_values:
            return None
        seen_values.add(value)
    return attr_name


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

    keyed_group_names: dict[str, str] = {}
    for child_tag, count in child_counts.items():
        if count <= 1:
            continue
        attr_name = _attribute_keyed_group_name(children, child_tag)
        if attr_name is not None:
            keyed_group_names[child_tag] = attr_name

    seen: set[str] = set(row)
    duplicate_counters: dict[str, int] = {}
    for child in children:
        child_tag = _strip_namespace(child.tag)
        if child_tag in keyed_group_names:
            key_name = child.attrib[keyed_group_names[child_tag]]
            output_key = f"{prefix}.{key_name}" if prefix else key_name
            row[output_key] = (child.text or "").strip()
            seen.add(output_key)
            continue
        child_prefix = f"{prefix}.{child_tag}" if prefix else child_tag
        flattened = _flatten_xml_element(child, child_prefix)
        for key, value in flattened.items():
            output_key = key
            if child_counts[child_tag] > 1 or output_key in seen:
                output_key = _next_unique_path(key, seen, duplicate_counters)
            row[output_key] = value
            seen.add(output_key)

    return row


_PREFERRED_RECORD_TAGS = ("record", "row", "item", "entry")


def _find_repeated_group(
    element: ElementTree.Element, max_depth: int = 8
) -> list[ElementTree.Element] | None:
    """Walk down through wrapper chains to find the outermost repeated
    sibling elements representing one row each.

    Some XML exports wrap the record list several levels below the element
    being inspected, e.g. ``envelope > body > container > list > record*``,
    not just a single level (``response > orders > order*``). Flattening a
    wrapper before finding the real repeat creates one column per repeated
    descendant across the *whole* subtree, which is both semantically wrong
    (positional columns don't line up between differently-shaped records)
    and very slow for large documents. This descends through any chain of
    elements that have exactly one child, or exactly one child with children
    of its own, until it finds a level with repeated (or preferred-named)
    siblings.
    """
    current = element
    for _ in range(max_depth):
        children = list(current)
        if not children:
            return None

        for tag in _PREFERRED_RECORD_TAGS:
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

        containers = [child for child in children if list(child)]
        if len(containers) == 1:
            current = containers[0]
            continue

        return None
    return None


def _select_nested_xml_records(element: ElementTree.Element) -> list[ElementTree.Element] | None:
    """Return likely record descendants for one top-level wrapper element."""
    return _find_repeated_group(element)


def _select_xml_records(root: ElementTree.Element) -> list[ElementTree.Element]:
    children = list(root)
    if not children:
        return [root]
    return _find_repeated_group(root) or [root]


def _stream_xml_candidates(raw: bytes) -> list[tuple[str, dict[str, Any]]]:
    """Flatten each direct child of the XML root as it finishes parsing and
    clear it immediately, so peak memory holds one record's worth of DOM at
    a time instead of the whole parsed document tree.
    """
    depth1_counts = _count_depth1_tags(raw)

    context = ElementTree.iterparse(io.BytesIO(raw), events=("start", "end"))
    _, root = next(context)
    depth = 1
    candidates: list[tuple[str, dict[str, Any]]] = []
    for event, elem in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 1:
            tag = _strip_namespace(elem.tag)
            if depth1_counts.get(tag, 0) > 1:
                # This tag already repeats among its own root-level siblings --
                # that IS the record boundary. Don't look for a further nested
                # list inside it: any repeated children of its own are just
                # multi-valued fields belonging to this one row, not more rows.
                candidates.append((tag, _flatten_xml_element(elem)))
            else:
                nested_records = _select_nested_xml_records(elem)
                if nested_records is None:
                    candidates.append((tag, _flatten_xml_element(elem)))
                else:
                    candidates.extend(
                        (_strip_namespace(record.tag), _flatten_xml_element(record))
                        for record in nested_records
                    )
            elem.clear()
    return candidates


def _count_depth1_tags(raw: bytes) -> dict[str, int]:
    """Cheap pre-pass: count root-level child tags without flattening, so
    the real pass can tell a genuine root-level repeat (the record boundary)
    apart from a singleton wrapper that might hide records further down.
    """
    context = ElementTree.iterparse(io.BytesIO(raw), events=("start", "end"))
    _, root = next(context)
    depth = 1
    counts: dict[str, int] = {}
    for event, elem in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 1:
            tag = _strip_namespace(elem.tag)
            counts[tag] = counts.get(tag, 0) + 1
            elem.clear()
    return counts


def _read_xml_bytes_dom(raw: bytes) -> pd.DataFrame:
    root = ElementTree.fromstring(raw)
    records = _select_xml_records(root)
    rows = [_flatten_xml_element(record) for record in records]
    return pd.DataFrame(rows)


def _read_xml_bytes(raw: bytes) -> pd.DataFrame:
    head = raw[:512].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise HTTPException(status_code=400, detail="XML files with DTD or entity declarations are not supported")

    try:
        candidates = _stream_xml_candidates(raw)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=400, detail="Cannot parse XML file") from exc

    if not candidates:
        # Root has no children (single flat record) — trivially small, so
        # the one-shot DOM path is cheap here.
        return _read_xml_bytes_dom(raw)

    preferred = ("record", "row", "item", "entry")
    for tag in preferred:
        matches = [row for name, row in candidates if name.lower() == tag]
        if matches:
            return pd.DataFrame(matches)

    counts: dict[str, int] = {}
    for name, _ in candidates:
        counts[name] = counts.get(name, 0) + 1
    repeated_tags = {tag for tag, count in counts.items() if count > 1}
    if repeated_tags:
        matches = [row for name, row in candidates if name in repeated_tags]
        return pd.DataFrame(matches)

    # No repeated top-level tag — rare small-document edge case (nested
    # record container, e.g. <response><orders><order/>...</orders></response>).
    # Candidates were already cleared during streaming, so re-parse to run
    # the recursive DOM selection heuristic (grandchildren detection).
    return _read_xml_bytes_dom(raw)


def _read_tabular_bytes(raw: bytes, ext: str) -> pd.DataFrame:
    if ext in (".csv", ".dat"):
        # .dat is a common extension for delimited flat-file spools (e.g. the
        # financials_{YYYYMMDD}.dat baseline exports multi-file reconciliation
        # pairs against). _read_csv_bytes sniffs the delimiter on fallback, so
        # comma/tab/semicolon/pipe .dat files all parse.
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


def _allowed_bases() -> tuple[Path, ...]:
    bases: list[Path] = []
    if _UPLOAD_BASE is not None:
        bases.append(_UPLOAD_BASE.resolve())
    bases.extend(base.resolve() for base in _UPLOAD_BASES)

    deduped: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        key = os.path.normcase(str(base))
        if key not in seen:
            seen.add(key)
            deduped.append(base)
    return tuple(deduped)


def _allowed_bases_detail(bases: tuple[Path, ...]) -> str:
    return ", ".join(str(base) for base in bases)


def _resolve_allowed_path(path: str) -> Path:
    bases = _allowed_bases()
    if not bases:
        raise HTTPException(
            status_code=400,
            detail=(
                "Server-side file paths are disabled. Set SERVER_FILE_ALLOWED_DIRS "
                "or UPLOAD_BASE_DIR, or use content_b64 upload."
            ),
        )
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        for base in bases:
            try:
                resolved.relative_to(base)
                return resolved
            except ValueError:
                continue
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file path. Allowed server-side base directories: {_allowed_bases_detail(bases)}",
        )

    base = bases[0]
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file path. Allowed server-side base directories: {_allowed_bases_detail(bases)}",
        )
    return resolved


def resolve_allowed_path(path: str) -> Path:
    """Public entry point for callers outside this module (e.g. the
    multi-file discovery resolver in etl_framework.reconciliation.file_mapping)
    that need the same server-side directory allow-listing ``read_tabular``
    already enforces, without duplicating it."""
    return _resolve_allowed_path(path)


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

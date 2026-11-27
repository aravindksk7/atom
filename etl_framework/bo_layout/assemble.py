"""Assemble extracted PDF table fragments into a normalised BO data table."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from etl_framework.bo_layout.model import PdfPrompt, PdfReadError, PdfReadOptions, PdfTable, RawTable

if TYPE_CHECKING:
    from etl_framework.bo_layout.extract import ExtractResult

PAGE_TEXT_RE = re.compile(r"(?i)^(page\s*\d+(\s*(of|/)\s*\d+)?|\d+\s*(of|/)\s*\d+)$")
_PROMPT_PREFIX_RE = re.compile(r"^\d+\.\s*")
_CONTINUATION_TOL_PT = 2.0

_ERR_NO_DATA_TABLE = "no data table found in the PDF"
_ERR_SEVERAL_TABLES = "the PDF holds several data tables; choose one"
_ERR_SECTION_REPORT = (
    "this PDF uses report sections (group headings above each block); section reports aren't supported yet"
)


@dataclass
class AssembleStats:
    filled_cells: int
    dropped_empty_rows: int
    dropped_repeated_headers: int
    ignored_tables: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _TableGroup:
    norm_header: list[str]
    body_rows: list[list[str | None]]
    fragments: list[RawTable]
    dropped_repeated_headers: int = 0


def normalise_header(cell: str | None) -> str:
    """Normalise a header/prompt cell with NFKC, newline-to-space, collapsed whitespace, and strip."""
    if cell is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", cell).split())


def _is_prompts_block(raw: RawTable) -> bool:
    if not raw.rows or not all(len(row) == 2 for row in raw.rows):
        return False
    if (raw.above_text or "").strip() == "Prompts:":
        return True
    return all((row[0] or "").strip().endswith((":", "?")) for row in raw.rows)


def _extract_prompts(raw: RawTable) -> list[PdfPrompt]:
    prompts: list[PdfPrompt] = []
    for row in raw.rows:
        label = normalise_header(row[0])
        label = _PROMPT_PREFIX_RE.sub("", label)
        if label.endswith((":", "?")):
            label = label[:-1]
        label = label.strip()
        value = normalise_header(row[1])
        prompts.append(PdfPrompt(label=label, value=value))
    return prompts


def _is_continuation(
    raw: RawTable,
    first_row_norm: tuple[str, ...],
    prev_data_raw: RawTable | None,
    prev_group: _TableGroup | None,
    seen_headers: set[tuple[str, ...]],
) -> bool:
    if prev_data_raw is None or prev_group is None:
        return False
    if first_row_norm in seen_headers:
        return False
    if not raw.starts_page:
        return False
    if not prev_data_raw.ends_page or raw.page != prev_data_raw.page + 1:
        return False
    if len(raw.rows[0]) != len(prev_group.norm_header):
        return False
    if len(raw.col_x) != len(prev_data_raw.col_x):
        return False
    return all(abs(a - b) <= _CONTINUATION_TOL_PT for a, b in zip(raw.col_x, prev_data_raw.col_x))


def _distinct_pages(fragments: list[RawTable]) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for frag in fragments:
        if frag.page not in seen:
            seen.add(frag.page)
            pages.append(frag.page)
    return pages


def _reject_section_reports(groups: list[_TableGroup]) -> None:
    for grp in groups:
        by_page: dict[int, set[str]] = {}
        across_pages: set[str] = set()
        for frag in grp.fragments:
            text = (frag.above_text or "").strip()
            if not text:
                continue
            page_set = by_page.setdefault(frag.page, set())
            page_set.add(text)
            if len(page_set) >= 2:
                raise PdfReadError("section_report", _ERR_SECTION_REPORT)
            if not PAGE_TEXT_RE.match(text):
                across_pages.add(text)
        if len(across_pages) >= 2:
            raise PdfReadError("section_report", _ERR_SECTION_REPORT)


def _build_groups(result: ExtractResult) -> tuple[list[_TableGroup], list[PdfPrompt]]:
    ordered = sorted(result.tables, key=lambda t: (t.page, t.top))
    prompts: list[PdfPrompt] = []
    groups: list[_TableGroup] = []
    groups_by_header: dict[tuple[str, ...], _TableGroup] = {}
    seen_headers: set[tuple[str, ...]] = set()
    prev_data_raw: RawTable | None = None
    prev_group: _TableGroup | None = None

    for raw in ordered:
        if _is_prompts_block(raw):
            prompts.extend(_extract_prompts(raw))
            continue
        if not raw.rows:
            continue

        first_row_norm = tuple(normalise_header(c) for c in raw.rows[0])
        if _is_continuation(raw, first_row_norm, prev_data_raw, prev_group, seen_headers):
            assert prev_group is not None
            prev_group.body_rows.extend(list(r) for r in raw.rows)
            prev_group.fragments.append(raw)
            prev_data_raw = raw
            continue

        body = [list(r) for r in raw.rows[1:]]
        if first_row_norm in groups_by_header:
            grp = groups_by_header[first_row_norm]
            grp.dropped_repeated_headers += 1
            grp.body_rows.extend(body)
            grp.fragments.append(raw)
        else:
            grp = _TableGroup(
                norm_header=list(first_row_norm),
                body_rows=body,
                fragments=[raw],
            )
            groups.append(grp)
            groups_by_header[first_row_norm] = grp
            seen_headers.add(first_row_norm)

        prev_data_raw = raw
        prev_group = grp

    _reject_section_reports(groups)
    return groups, prompts


def _deduplicate_headers(header: list[str]) -> list[str]:
    named = [h if h else f"Column {idx}" for idx, h in enumerate(header, start=1)]
    seen: set[str] = set()
    deduped: list[str] = []
    for col in named:
        if col not in seen:
            seen.add(col)
            deduped.append(col)
            continue
        suffix = 2
        candidate = f"{col} ({suffix})"
        while candidate in seen:
            suffix += 1
            candidate = f"{col} ({suffix})"
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _table_summary(index: int, grp: _TableGroup) -> dict[str, Any]:
    return {
        "index": index,
        "header": list(grp.norm_header),
        "rows": len(grp.body_rows),
        "pages": _distinct_pages(grp.fragments),
    }


def _choose_group_index(groups: list[_TableGroup], choice: int | str | None, summaries: list[dict[str, Any]]) -> int:
    if len(groups) == 1 and choice is None:
        return 0
    if choice is None:
        raise PdfReadError("several_tables", _ERR_SEVERAL_TABLES, tables=summaries)
    if isinstance(choice, int) and not isinstance(choice, bool):
        if 1 <= choice <= len(groups):
            return choice - 1
        raise PdfReadError(
            "table_not_found",
            f'no data table matches "{choice}"; choose one',
            tables=summaries,
        )
    needle = str(choice).lower()
    matches = [i for i, grp in enumerate(groups) if needle in " | ".join(grp.norm_header).lower()]
    if len(matches) == 1:
        return matches[0]
    raise PdfReadError(
        "table_not_found",
        f'no data table matches "{choice}"; choose one',
        tables=summaries,
    )


def candidate_tables(result: ExtractResult) -> tuple[list[PdfTable], list[PdfPrompt]]:
    """Return candidate table groups (rules 1-5) and extracted prompts without choosing a table."""
    groups, prompts = _build_groups(result)
    tables = [
        PdfTable(
            header=list(grp.norm_header),
            rows=[list(r) for r in grp.body_rows],
            pages=_distinct_pages(grp.fragments),
            fragments=len(grp.fragments),
            col_x=list(grp.fragments[0].col_x),
        )
        for grp in groups
    ]
    return tables, prompts


def assemble(result: ExtractResult, options: PdfReadOptions) -> tuple[PdfTable, list[PdfPrompt], AssembleStats]:
    """Assemble the chosen BO data table, prompts, and assembly stats."""
    groups, prompts = _build_groups(result)
    if not groups:
        raise PdfReadError("no_data_table", _ERR_NO_DATA_TABLE)

    summaries = [_table_summary(idx, grp) for idx, grp in enumerate(groups, start=1)]
    chosen_idx = _choose_group_index(groups, options.table, summaries)
    chosen = groups[chosen_idx]
    ignored_tables = [summaries[i] for i in range(len(groups)) if i != chosen_idx]

    header_key = tuple(chosen.norm_header)
    dropped_repeated_headers = chosen.dropped_repeated_headers
    rows_without_repeated_headers: list[list[str | None]] = []
    for row in chosen.body_rows:
        if tuple(normalise_header(c) for c in row) == header_key:
            dropped_repeated_headers += 1
        else:
            rows_without_repeated_headers.append(list(row))

    dropped_empty_rows = 0
    rows: list[list[str | None]] = []
    for row in rows_without_repeated_headers:
        if all(cell is None or cell == "" for cell in row):
            dropped_empty_rows += 1
        else:
            rows.append(row)

    filled_cells = 0
    if options.fill_down and len(rows) > 1:
        for r_idx in range(1, len(rows)):
            prev_row = rows[r_idx - 1]
            cur_row = rows[r_idx]
            for c_idx in range(len(cur_row)):
                if cur_row[c_idx] is None and c_idx < len(prev_row) and prev_row[c_idx] is not None:
                    cur_row[c_idx] = prev_row[c_idx]
                    filled_cells += 1

    final_header = _deduplicate_headers(chosen.norm_header)
    table = PdfTable(
        header=final_header,
        rows=rows,
        pages=_distinct_pages(chosen.fragments),
        fragments=len(chosen.fragments),
        col_x=list(chosen.fragments[0].col_x),
    )
    stats = AssembleStats(
        filled_cells=filled_cells,
        dropped_empty_rows=dropped_empty_rows,
        dropped_repeated_headers=dropped_repeated_headers,
        ignored_tables=ignored_tables,
    )
    return table, prompts, stats

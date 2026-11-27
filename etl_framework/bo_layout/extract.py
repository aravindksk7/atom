"""PDF table extraction with border-rectangle grid, guarded fallback, and preflight."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils import extract_text, extract_words
from pdfplumber.utils.exceptions import PdfminerException

from etl_framework.bo_layout.model import PdfReadError, RawTable

ALLOWED_TABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "vertical_strategy",
        "horizontal_strategy",
        "snap_tolerance",
        "join_tolerance",
        "edge_min_length",
        "intersection_tolerance",
        "text_x_tolerance",
        "text_y_tolerance",
    }
)

# Measured on synthetic 3-column `borders="lines"` tall pages (T2.S5):
#   rows | len(page.edges) | find_tables() time
#    250 |           1,760 | 0.057 s
#    500 |           3,510 | 0.232 s
#  1,000 |           7,010 | 0.754 s
#  2,000 |          14,010 | 2.699 s
# Largest page <= 20 s is 2,000 rows (14,010 edges), rounded down to a multiple of 100:
FALLBACK_MAX_EDGES: int = 14000

_DEFAULT_FALLBACK_SETTINGS: dict[str, Any] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
}


@dataclass
class ExtractResult:
    tables: list[RawTable]
    text_lines: list[tuple[int, float, str]]
    page_count: int
    producer: str | None
    creator: str | None


def _cluster_coords(values: list[float], tol: float = 1.0) -> list[float]:
    """Cluster sorted coordinates within `tol` pt, returning cluster means."""
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters: list[list[float]] = [[sorted_vals[0]]]
    for val in sorted_vals[1:]:
        if val - clusters[-1][0] <= tol:
            clusters[-1].append(val)
        else:
            clusters.append([val])
    return [sum(c) / len(c) for c in clusters]


def _nearest_index(sorted_targets: list[float], val: float) -> int:
    """Return index of closest value in a non-empty sorted list."""
    pos = bisect_left(sorted_targets, val)
    if pos == 0:
        return 0
    if pos == len(sorted_targets):
        return len(sorted_targets) - 1
    before = sorted_targets[pos - 1]
    after = sorted_targets[pos]
    return pos - 1 if (val - before) <= (after - val) else pos


def _prepare_char_index(page: Any) -> tuple[list[dict[str, Any]], list[float], list[float]]:
    """Return page chars sorted by centre-y along with parallel centre-y and centre-x lists."""
    chars = page.chars
    keyed = sorted(
        chars,
        key=lambda ch: ((ch["top"] + ch["bottom"]) / 2.0, (ch["x0"] + ch["x1"]) / 2.0),
    )
    v_mids = [(ch["top"] + ch["bottom"]) / 2.0 for ch in keyed]
    h_mids = [(ch["x0"] + ch["x1"]) / 2.0 for ch in keyed]
    return keyed, v_mids, h_mids


def _grid_tables_with_chars(
    page: Any,
    sorted_chars: list[dict[str, Any]],
    char_v_mids: list[float],
    char_h_mids: list[float],
) -> list[RawTable]:
    """Extract bordered tables from thin horizontal and vertical rects on `page`."""
    rects = page.rects
    h_borders = [r for r in rects if r["height"] <= 1.0 and r["width"] > 1.0]
    v_borders = [r for r in rects if r["width"] <= 1.0 and r["height"] > 1.0]

    if not h_borders or not v_borders:
        return []

    y_lines = _cluster_coords([(r["top"] + r["bottom"]) / 2.0 for r in h_borders], tol=1.0)
    if len(y_lines) < 2:
        return []

    # Map each horizontal border to its y_line index
    h_by_y: list[list[tuple[float, float]]] = [[] for _ in y_lines]
    for r in h_borders:
        y_mid = (r["top"] + r["bottom"]) / 2.0
        idx = _nearest_index(y_lines, y_mid)
        h_by_y[idx].append((float(r["x0"]), float(r["x1"])))

    # For each row band (y_lines[b], y_lines[b + 1]), collect vertical borders that span it.
    n_bands = len(y_lines) - 1
    band_vborders: list[list[dict[str, Any]]] = [[] for _ in range(n_bands)]
    for v in v_borders:
        v_top = float(v["top"])
        v_bot = float(v["bottom"])
        # Use bisect to locate candidate bands overlapping [v_top, v_bot]
        b_start = max(0, bisect_left(y_lines, v_top - 1.0) - 1)
        b_end = min(n_bands, bisect_left(y_lines, v_bot + 1.0))
        for b in range(b_start, b_end):
            y0, y1 = y_lines[b], y_lines[b + 1]
            band_h = y1 - y0
            if band_h <= 0:
                continue
            overlap = min(v_bot, y1) - max(v_top, y0)
            if overlap > 0.5 * band_h:
                band_vborders[b].append(v)

    # Maximal runs of bands that have vertical borders
    runs: list[tuple[int, int]] = []
    b = 0
    while b < n_bands:
        if band_vborders[b]:
            start = b
            while b < n_bands and band_vborders[b]:
                b += 1
            runs.append((start, b))
        else:
            b += 1

    page_no = getattr(page, "page_number", 1)
    results: list[RawTable] = []

    for start_b, end_b in runs:
        run_vs: list[dict[str, Any]] = []
        for rb in range(start_b, end_b):
            run_vs.extend(band_vborders[rb])

        col_x = _cluster_coords([(v["x0"] + v["x1"]) / 2.0 for v in run_vs], tol=1.0)
        n_cols = len(col_x) - 1
        if n_cols < 1:
            continue

        n_rows = end_b - start_b
        col_mids = [(col_x[c] + col_x[c + 1]) / 2.0 for c in range(n_cols)]

        # Which vertical column lines are present in each band of the run
        v_present: list[set[int]] = []
        for rb in range(start_b, end_b):
            present = {_nearest_index(col_x, (v["x0"] + v["x1"]) / 2.0) for v in band_vborders[rb]}
            v_present.append(present)

        # Which column midpoints are covered by a horizontal border at each y_line in the run
        h_covers: list[list[bool]] = []
        for k in range(start_b, end_b + 1):
            segs = h_by_y[k]
            row_cov = [
                any((x0 - 0.5) <= cm <= (x1 + 0.5) for x0, x1 in segs)
                for cm in col_mids
            ]
            h_covers.append(row_cov)

        rows: list[list[str | None]] = []
        for i in range(n_rows):
            row_vals: list[str | None] = []
            vp = v_present[i]
            for c in range(n_cols):
                if (c > 0 and c not in vp) or (i > 0 and not h_covers[i][c]):
                    row_vals.append(None)
                    continue

                c2 = c + 1
                while c2 <= n_cols and c2 not in vp:
                    c2 += 1
                if c2 > n_cols:
                    c2 = n_cols

                i2 = i + 1
                while i2 < n_rows and not h_covers[i2][c]:
                    i2 += 1

                cx0, cx1 = col_x[c], col_x[c2]
                ctop, cbot = y_lines[start_b + i], y_lines[start_b + i2]

                lo = bisect_left(char_v_mids, ctop)
                hi = bisect_left(char_v_mids, cbot)
                cell_chars = [
                    sorted_chars[j]
                    for j in range(lo, hi)
                    if cx0 <= char_h_mids[j] < cx1
                ]
                cell_text = extract_text(cell_chars) if cell_chars else ""
                row_vals.append(cell_text)

            rows.append(row_vals)

        results.append(
            RawTable(
                rows=rows,
                page=page_no,
                top=y_lines[start_b],
                bottom=y_lines[end_b],
                col_x=col_x,
                above_text=None,
                starts_page=False,
                ends_page=False,
            )
        )

    return results


def grid_tables(page: Any) -> list[RawTable]:
    """Extract bordered tables from `page` using thin horizontal/vertical border rects."""
    sorted_chars, char_v_mids, char_h_mids = _prepare_char_index(page)
    return _grid_tables_with_chars(page, sorted_chars, char_v_mids, char_h_mids)


def _has_thin_border_rects(page: Any) -> bool:
    return any(
        (r["height"] <= 1.0 and r["width"] > 1.0)
        or (r["width"] <= 1.0 and r["height"] > 1.0)
        for r in page.rects
    )


def _fallback_tables(page: Any, settings: dict[str, Any]) -> list[RawTable]:
    page_no = getattr(page, "page_number", 1)
    found = page.find_tables(settings)
    results: list[RawTable] = []
    for tbl in found:
        x0, top, x1, bottom = tbl.bbox
        xs = sorted(
            {
                round(val, 4)
                for row in tbl.rows
                for cell in row.cells
                if cell is not None
                for val in (cell[0], cell[2])
            }
        )
        if not xs:
            xs = [float(x0), float(x1)]
        results.append(
            RawTable(
                rows=tbl.extract(),
                page=page_no,
                top=float(top),
                bottom=float(bottom),
                col_x=[float(x) for x in xs],
                above_text=None,
                starts_page=False,
                ends_page=False,
            )
        )
    return results


def _page_text_lines_outside_tables(
    page_no: int,
    sorted_chars: list[dict[str, Any]],
    char_v_mids: list[float],
    char_h_mids: list[float],
    page_tables: list[RawTable],
) -> list[tuple[int, float, str]]:
    """Group words outside every table on the page by `round(top)`."""
    if not sorted_chars:
        return []

    bboxes = [
        (t.col_x[0] - 0.5, t.top - 0.5, t.col_x[-1] + 0.5, t.bottom + 0.5)
        for t in page_tables
        if len(t.col_x) >= 2
    ]

    if not bboxes:
        outside_chars = sorted_chars
    else:
        outside_chars = []
        for idx, ch in enumerate(sorted_chars):
            vy = char_v_mids[idx]
            hx = char_h_mids[idx]
            inside = False
            for bx0, btop, bx1, bbot in bboxes:
                if bx0 <= hx <= bx1 and btop <= vy <= bbot:
                    inside = True
                    break
            if not inside:
                outside_chars.append(ch)

    if not outside_chars:
        return []

    words = extract_words(outside_chars)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for w in words:
        key = round(w["top"])
        grouped.setdefault(key, []).append(w)

    lines: list[tuple[int, float, str]] = []
    for top_key in sorted(grouped):
        line_words = sorted(grouped[top_key], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line_words)
        lines.append((page_no, float(top_key), text))
    return lines


def _annotate_page_tables(
    page_tables: list[RawTable],
    page_lines: list[tuple[int, float, str]],
    char_v_mids: list[float],
) -> None:
    """Populate `above_text`, `starts_page`, and `ends_page` on each RawTable for a page."""
    for tbl in page_tables:
        # Nearest text line above table top within 30 pt (already outside all tables)
        candidates = [
            (line_top, text)
            for _, line_top, text in page_lines
            if 0.0 < (tbl.top - line_top) <= 30.0
        ]
        if candidates:
            _, best_text = max(candidates, key=lambda item: item[0])
            tbl.above_text = best_text

        has_table_above = any(other is not tbl and other.top < tbl.top for other in page_tables)
        has_table_below = any(other is not tbl and other.bottom > tbl.bottom for other in page_tables)

        has_chars_above = bisect_left(char_v_mids, tbl.top - 0.5) > 0
        has_chars_below = bisect_left(char_v_mids, tbl.bottom + 0.5) < len(char_v_mids)

        tbl.starts_page = not has_table_above and not has_chars_above
        tbl.ends_page = not has_table_below and not has_chars_below


def extract_tables(
    path: str | Path,
    table_settings: dict[str, Any] | None = None,
    *,
    max_pages: int = 1000,
) -> ExtractResult:
    """Extract tables, outer text lines, and PDF metadata with preflight validation."""
    if table_settings:
        unknown = sorted(set(table_settings) - ALLOWED_TABLE_SETTINGS)
        if unknown:
            raise PdfReadError("bad_settings", f"unknown table_settings keys: {', '.join(unknown)}")

    merged_settings = {**_DEFAULT_FALLBACK_SETTINGS, **(table_settings or {})}

    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:
        inner = exc.args[0] if isinstance(exc, PdfminerException) and exc.args else exc
        if isinstance(inner, (PDFPasswordIncorrect, PDFEncryptionError)):
            raise PdfReadError("encrypted", "PDF is encrypted and cannot be read") from exc
        if isinstance(inner, PDFSyntaxError):
            raise PdfReadError("invalid_pdf", f"invalid PDF: {inner}") from exc
        raise PdfReadError("invalid_pdf", f"unable to open PDF: {inner}") from exc

    with pdf:
        page_count = len(pdf.pages)
        if page_count > max_pages:
            raise PdfReadError(
                "too_many_pages",
                f"PDF has {page_count} pages, exceeding max_pages={max_pages}",
            )

        if not any(page.chars for page in pdf.pages):
            raise PdfReadError("no_text_layer", "PDF has no extractable text layer")

        meta = pdf.metadata or {}
        raw_producer = meta.get("Producer")
        raw_creator = meta.get("Creator")
        producer = str(raw_producer) if raw_producer is not None else None
        creator = str(raw_creator) if raw_creator is not None else None

        all_tables: list[RawTable] = []
        all_lines: list[tuple[int, float, str]] = []

        for page_no, page in enumerate(pdf.pages, start=1):
            sorted_chars, char_v_mids, char_h_mids = _prepare_char_index(page)

            if _has_thin_border_rects(page):
                page_tables = _grid_tables_with_chars(page, sorted_chars, char_v_mids, char_h_mids)
            else:
                if len(page.edges) > FALLBACK_MAX_EDGES:
                    raise PdfReadError(
                        "no_borders",
                        "the PDF has no cell borders and is too large to read without them",
                    )
                page_tables = _fallback_tables(page, merged_settings)

            page_lines = _page_text_lines_outside_tables(
                page_no, sorted_chars, char_v_mids, char_h_mids, page_tables
            )
            _annotate_page_tables(page_tables, page_lines, char_v_mids)

            all_tables.extend(page_tables)
            all_lines.extend(page_lines)

        return ExtractResult(
            tables=all_tables,
            text_lines=all_lines,
            page_count=page_count,
            producer=producer,
            creator=creator,
        )

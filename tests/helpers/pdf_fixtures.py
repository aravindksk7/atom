"""Synthetic SAP BusinessObjects PDF report builders for tests."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas


@dataclass
class _TableFragment:
    rows: list[list[str | None]]
    above: str | None = None
    is_prompts: bool = False


def _cell_lines(val: str | None) -> list[str]:
    if val is None or val == "":
        return [""]
    return val.split("\n")


def _row_height(row: list[str | None], line_height: float = 11.0, pad_y: float = 4.0) -> float:
    max_lines = max((len(_cell_lines(v)) for v in row), default=1)
    return 2 * pad_y + max_lines * line_height


def _draw_bordered_table(
    c: canvas.Canvas,
    x0: float,
    top_y: float,
    col_widths: list[float],
    rows: list[list[str | None]],
    *,
    borders: Literal["rects", "lines"],
    font_name: str = "Helvetica",
    font_size: float = 8.5,
    line_height: float = 11.0,
    pad_x: float = 4.0,
    pad_y: float = 4.0,
) -> float:
    """Draw a table top-down from ReportLab y=`top_y`; return bottom ReportLab y."""
    n_cols = len(col_widths)
    xs = [x0]
    for w in col_widths:
        xs.append(xs[-1] + w)

    row_heights = [_row_height(r, line_height=line_height, pad_y=pad_y) for r in rows]
    y_edges = [top_y]
    for rh in row_heights:
        y_edges.append(y_edges[-1] - rh)

    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.5)

    def h_seg(xa: float, xb: float, y: float) -> None:
        if borders == "rects":
            c.rect(xa - 0.15, y - 0.15, (xb - xa) + 0.3, 0.3, stroke=0, fill=1)
        else:
            c.line(xa, y, xb, y)

    def v_seg(x: float, yb: float, yt: float) -> None:
        if borders == "rects":
            c.rect(x - 0.15, yb - 0.15, 0.3, (yt - yb) + 0.3, stroke=0, fill=1)
        else:
            c.line(x, yb, x, yt)

    # Top border of the table
    for col_idx in range(n_cols):
        h_seg(xs[col_idx], xs[col_idx + 1], y_edges[0])

    c.setFont(font_name, font_size)
    for r_idx, row in enumerate(rows):
        y_top = y_edges[r_idx]
        y_bot = y_edges[r_idx + 1]

        # Top border of row r_idx (> 0): omit where cell value is None (merged with above)
        if r_idx > 0:
            for col_idx in range(n_cols):
                if row[col_idx] is not None:
                    h_seg(xs[col_idx], xs[col_idx + 1], y_top)

        # Vertical borders for this row
        for col_idx in range(n_cols + 1):
            v_seg(xs[col_idx], y_bot, y_top)

        # Cell text
        for col_idx, val in enumerate(row):
            if val is None or val == "":
                continue
            lines = val.split("\n")
            text_y = y_top - pad_y - font_size
            for line in lines:
                c.drawString(xs[col_idx] + pad_x, text_y, line)
                text_y -= line_height

    # Bottom border of the table
    for col_idx in range(n_cols):
        h_seg(xs[col_idx], xs[col_idx + 1], y_edges[-1])

    return y_edges[-1]


def build_bo_pdf(
    path: str | Path,
    *,
    title: str = "Report Title",
    prompts: list[tuple[str, str]] | None = None,
    tables: list[dict[str, Any]],
    rows_per_page: int | None = None,
    repeat_header: bool = True,
    borders: Literal["rects", "lines"] = "rects",
    encrypt: str | None = None,
    image_only: bool = False,
    col_widths: list[float] | None = None,
    page_text: Callable[[int, int], str] | None = None,
) -> Path:
    """Build a synthetic SAP BO-style PDF for unit and performance testing."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    enc = StandardEncryption(encrypt) if encrypt is not None else None

    if image_only:
        c = canvas.Canvas(str(out_path), pagesize=(612.0, 792.0), encrypt=enc)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.rect(72.0, 500.0, 200.0, 120.0, stroke=0, fill=1)
        c.showPage()
        c.save()
        return out_path

    # Plan pages as lists of _TableFragment items
    pages_items: list[list[_TableFragment]] = [[]]

    if prompts:
        prompt_rows: list[list[str | None]] = [[lbl, val] for lbl, val in prompts]
        pages_items[0].append(_TableFragment(rows=prompt_rows, above="Prompts:", is_prompts=True))

    for tbl in tables:
        header: list[str] = list(tbl["header"])
        data_rows: list[list[str | None]] = [list(r) for r in tbl.get("rows", [])]
        above: str | None = tbl.get("above")
        new_page: bool = bool(tbl.get("new_page", False))

        if new_page and pages_items[-1]:
            pages_items.append([])

        if rows_per_page is None:
            frag_rows: list[list[str | None]] = [list(header)] + data_rows
            pages_items[-1].append(_TableFragment(rows=frag_rows, above=above))
        else:
            if not data_rows:
                pages_items[-1].append(_TableFragment(rows=[list(header)], above=above))
            else:
                for start in range(0, len(data_rows), rows_per_page):
                    if start > 0:
                        pages_items.append([])
                    chunk = data_rows[start : start + rows_per_page]
                    if start == 0 or repeat_header:
                        frag_rows = [list(header)] + chunk
                    else:
                        frag_rows = chunk
                    frag_above = above if start == 0 else None
                    pages_items[-1].append(_TableFragment(rows=frag_rows, above=frag_above))

    page_count = len(pages_items)
    margin_x = 40.0
    margin_y = 40.0
    table_gap = 36.0
    above_gap = 12.0
    title_gap = 40.0
    font_size = 8.5
    line_height = 11.0
    pad_y = 4.0

    # Determine page width from widest table
    max_cols = 1
    for page_frags in pages_items:
        for frag in page_frags:
            if frag.rows:
                max_cols = max(max_cols, len(frag.rows[0]))
    default_w = col_widths if col_widths is not None else [100.0] * max_cols
    page_width = max(612.0, margin_x * 2 + sum(default_w))

    c = canvas.Canvas(str(out_path), pagesize=(page_width, 792.0), encrypt=enc)

    for page_idx, page_frags in enumerate(pages_items, start=1):
        # Compute required height for this page
        needed = margin_y * 2
        has_title = page_idx == 1 and bool(title)
        if has_title:
            needed += 14.0 + title_gap
        if page_text is not None:
            needed += 12.0 + above_gap

        for idx, frag in enumerate(page_frags):
            if idx > 0:
                needed += table_gap
            if frag.above:
                needed += 12.0 + above_gap
            for row in frag.rows:
                needed += _row_height(row, line_height=line_height, pad_y=pad_y)

        page_height = max(792.0, needed)
        c.setPageSize((page_width, page_height))

        cursor_y = page_height - margin_y

        if has_title:
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 11.0)
            c.drawString(margin_x, cursor_y - 11.0, title)
            cursor_y -= 14.0 + title_gap

        p_txt = page_text(page_idx, page_count) if page_text is not None else None
        if p_txt is not None and not page_frags:
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", 9.0)
            c.drawString(margin_x, cursor_y - 9.0, p_txt)
            cursor_y -= 12.0 + above_gap

        for idx, frag in enumerate(page_frags):
            if idx > 0:
                cursor_y -= table_gap

            if idx == 0 and p_txt is not None:
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica", 9.0)
                if frag.above:
                    # Place page_text above the table's `above` line
                    c.drawString(margin_x, cursor_y - 9.0, p_txt)
                    cursor_y -= 12.0 + above_gap
                else:
                    # Place page_text 12 pt above the first table
                    c.drawString(margin_x, cursor_y - 9.0, p_txt)
                    cursor_y -= 9.0 + above_gap

            if frag.above:
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold" if frag.is_prompts else "Helvetica", 9.0)
                c.drawString(margin_x, cursor_y - 9.0, frag.above)
                cursor_y -= 9.0 + above_gap

            n_cols = len(frag.rows[0]) if frag.rows else 1
            if frag.is_prompts:
                widths = [120.0, 220.0]
            elif col_widths is not None and len(col_widths) == n_cols:
                widths = list(col_widths)
            else:
                widths = [100.0] * n_cols

            cursor_y = _draw_bordered_table(
                c,
                margin_x,
                cursor_y,
                widths,
                frag.rows,
                borders=borders,
                font_size=font_size,
                line_height=line_height,
                pad_y=pad_y,
            )

        c.showPage()

    c.save()
    return out_path


def build_demo_pdf(path: str | Path) -> Path:
    """Write a synthetic demo BO PDF (Region/Value, 5 rows, 1 dp) for UI tests."""
    return build_bo_pdf(
        path,
        title="Regional Sales Demo Report",
        tables=[
            {
                "header": ["Region", "Value"],
                "rows": [
                    ["North", "10.5"],
                    ["South", "20.2"],
                    ["East", "30.8"],
                    ["West", "40.1"],
                    ["Central", "50.4"],
                ],
            }
        ],
    )


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "demo":
        out = build_demo_pdf(args[1])
        print(out)
        raise SystemExit(0)
    if len(args) == 3 and args[0] == "spec":
        # spec <spec.json> <out.pdf>: build_bo_pdf keyword arguments as JSON.
        # "page_text" is a format string with {n} (page) and {total} (pages).
        import json

        spec = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        fmt = spec.pop("page_text", None)
        if fmt is not None:
            spec["page_text"] = lambda n, total, _fmt=fmt: _fmt.format(n=n, total=total)
        if "prompts" in spec:
            spec["prompts"] = [tuple(p) for p in spec["prompts"]]
        print(build_bo_pdf(args[2], **spec))
        raise SystemExit(0)
    print(
        "Usage: python -m tests.helpers.pdf_fixtures demo <out.pdf>\n"
        "       python -m tests.helpers.pdf_fixtures spec <spec.json> <out.pdf>",
        file=sys.stderr,
    )
    raise SystemExit(2)

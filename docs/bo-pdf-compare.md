# SAP BO PDF-vs-PDF Compare (`as printed`)

Atom can compare two SAP BusinessObjects (BO) Web Intelligence PDF exports side-by-side (`Compare -> BO Report` or `POST /api/compare/bo-report`). Unlike Excel/CSV exports that carry underlying cell values, a PDF only contains what was rendered on the page—so every PDF comparison runs **as printed**.

## What It Compares

- **Two BO PDFs, as printed:** Both Source A and Source B must be PDF files (`format: "pdf"` or a `.pdf` file extension).
- **Precision limit:** Differences smaller than the printed decimal places cannot be seen. For example, if two environments compute `1042.501` and `1042.504` and print both to 2 decimal places (`1042.50`), the PDF compare treats them as equal.
- **`"(as printed)"` status label:** Every PDF-vs-PDF run sets `compared_as: "printed"` and displays `PASSED (as printed)` or `FAILED (as printed)` in the Compare tab, History list/detail, and result rows. This reminds reviewers that equality was verified at printed precision rather than raw database precision.
- **Prompts:** Two-column prompt blocks at the top of a BO PDF (labeled `Prompts:` or rows whose labels end in `:` or `?`) are extracted separately, stripped from the data table, and diffed case-insensitively by label (`prompt_differences`). Prompt differences are shown in the PDF summary rather than compared as data rows.

## Supported vs. Unsupported Inputs

### Supported

- **Source types:** `path` (server-side path under allowed directories) and `upload` (browser base64 upload).
- **Bordered BO tables:** Tables drawn with thin horizontal and vertical cell border rectangles, plus a guarded `pdfplumber` `lines` fallback on small borderless pages (`<= 14,000` edges per page).
- **Multi-page tables:** Tables that span multiple pages are stitched across continuation fragments, and repeated table headers on later pages are dropped automatically.
- **One data table per compare:** Either a single data table in the PDF, or one explicitly chosen via `pdf.table` when the PDF holds multiple tables.

### Unsupported

- **Mixed PDF vs. non-PDF:** Comparing a PDF against `.xlsx`, `.csv`, `.xls`, `.json`, `.xml`, `.tsv`, `.txt`, `api`, `live`, or `run` is rejected (`"a PDF compare needs a PDF on both sides"`).
- **Live BO PDF export & `api`/`run` PDF sources:** Live SAP BO exports (`source_type: "live"`), REST API endpoints (`api`), and stored runs (`run`) cannot supply PDFs here. Export the PDFs from BO first and supply them via `upload` or `path`.
- **Section reports:** Reports with group headings above repeated table blocks (including one section per page) are rejected.
- **Scanned PDFs:** Image-only PDFs with no text layer are rejected (OCR is not supported).
- **Encrypted PDFs:** Password-protected or encrypted PDFs are rejected.
- **Large borderless PDFs:** Pages without thin cell border rectangles that exceed `14,000` geometric edges are rejected to prevent timeouts.

## Printed-Format Warnings & Wrap Reconciliation

Because PDFs reflect formatting choices made by each environment, Atom surfaces warnings during preview (`POST /api/compare/bo-report/pdf-preview`) and alignment:

### Per-source parsing warnings

- `ambiguous_date_format: <col> could be <fmt1> or <fmt2>; set the date order` — Slash dates such as `04/05/2026` match both `MM/DD/...` and `DD/MM/...`. Until `date_order` (`"dmy"` or `"mdy"`) is specified, the column stays `text`.
- `mixed_column: <col> is <share>% number|date; other values: ...` — At least 90% of non-empty values parse as a number (sharing one unit) or a single date format, while a small remainder (such as footer notes or mixed currencies) do not. The column stays `text`.

### Cross-source alignment warnings & `pdf_rewrap`

- **Different decimal places:** `<col>: printed with different decimals (A <da> dp, B <db> dp)` — Recorded in `printed_as`; `precision_note` reports the coarser precision (`min(da, db)` dp). Set `advanced.float_tolerance` (or `advanced.column_tolerances`) if rounding differences at the coarser precision are expected.
- **Different date formats:** `<col>: printed in different date formats (A <fa>, B <fb>)` — Recorded in `printed_as`. Note that date columns are compared as their printed strings, so differing date formats between environments will show as value differences unless formatted identically or excluded.
- **Different inferred column kinds:** `<col>: printed as <kind_a> in A and <kind_b> in B, compared as text` — Both sides are formatted to printed strings before comparison.
- **Wrap reconciliation (`pdf_rewrap`):** When a column is `text` on both sides, `pdf_rewrap` is `true` (default), and the column's rendered width differs by more than `1.0 pt` between Source A and Source B, Atom collapses whitespace on Source A values; wherever Source A has a single unique wrapped form for a collapsed string, matching values in Source B are normalized to Source A's wrapped form (`<col>: column widths differ (A <wa> pt, B <wb> pt); <count> values re-wrapped to A's form`). Hyphenated or slash line-wraps inside cells (e.g. `order-\n101`) are also joined during cell cleaning.

### When the two environments' report formats differ

1. Click **Preview PDF** on both Source A and Source B to inspect detected tables, column types (`number:2dp`, `date:%d-%b-%Y`, `text`), and warnings.
2. If one environment uses European decimals (`1.234,56`) or `DD/MM/YYYY` dates while the other uses US formatting, set `number_locale` and `date_order` independently on each source.
3. Keep **Reconcile line wrapping when column widths differ** (`pdf_rewrap: true`) enabled if column widths differ slightly between environments. Disable it when exact line breaks matter.
4. Use `advanced.float_tolerance` / `advanced.column_tolerances` when one side displays fewer decimal places, or add intentionally differing display-only columns to `exclude_columns`.

## Options Reference

### Per-source PDF options (`source_a.pdf` / `source_b.pdf`)

| Option | Type / Default | What it does | When to use |
|---|---|---|---|
| `table` | `int \| str \| null` (`null`) | Selects a single data table by 1-based index (e.g. `2`) or case-insensitive header substring (e.g. `"Region \| Net Sales"`). | Required when a PDF contains more than one data table. |
| `number_locale` | `"en" \| "eu"` (`"en"`) | `"en"` parses `1,234.56`; `"eu"` parses `1.234,56` and `1 234,56`. Recognizes `%`, `$`, `€`, `£`, `¥` and `-` or `(...)` negatives. | Set `"eu"` on any source formatted with comma decimals and dot/space thousands separators. |
| `date_order` | `"dmy" \| "mdy" \| null` (`null`) | Disambiguates numeric slash dates (`DD/MM/YYYY` vs. `MM/DD/YYYY`). | Set when preview or compare warns `ambiguous_date_format`. |
| `fill_down` | `bool` (`true`) | Propagates non-null values downward into vertically merged/blank cells produced by BO grouping/breaks. | Leave `true` for grouped BO tables; set `false` when blank cells mean empty values rather than merged group labels. |
| `table_settings` | `dict` (`{}`) | Optional allow-listed `pdfplumber` settings used only on pages without thin cell border rectangles: `vertical_strategy`, `horizontal_strategy`, `snap_tolerance`, `join_tolerance`, `edge_min_length`, `intersection_tolerance`, `text_x_tolerance`, `text_y_tolerance`. | Tune borderless fallback extraction via API if default `"lines"` settings miss edges. Any other key is rejected. |

### Request-level options (`BOCompareRequest`)

| Option | Type / Default | What it does | When to use |
|---|---|---|---|
| `pdf_rewrap` | `bool` (`true`) | Enables wrap reconciliation for `text` columns whose widths differ by `> 1.0 pt`. | Leave `true` across differently sized templates; set `false` to detect whitespace/wrapping diffs strictly. |
| `key_columns` | `list[str]` (`[]`) | Unique row-matching key columns. Auto-inferred from standard ID names if omitted, otherwise required. | Always set (or click **Use as key** from PDF Preview). Positional `__row__` fallback is disabled for PDF compares. |
| `exclude_columns` | `list[str]` (`[]`) | Columns excluded from value comparison. | Exclude volatile run timestamps, page numbers, or intentionally differently formatted display columns. |

### Invented API example

```json
POST /api/compare/bo-report
{
  "source_a": {
    "source_type": "path",
    "file_path": "C:/reports/northwind_sales_dev.pdf",
    "format": "pdf",
    "pdf": {
      "table": 1,
      "number_locale": "en",
      "date_order": "mdy",
      "fill_down": true
    }
  },
  "source_b": {
    "source_type": "path",
    "file_path": "C:/reports/northwind_sales_qa.pdf",
    "format": "pdf",
    "pdf": {
      "table": 1,
      "number_locale": "eu",
      "date_order": "dmy",
      "fill_down": true
    }
  },
  "key_columns": ["Account Code"],
  "exclude_columns": ["Printed At"],
  "label_a": "Dev PDF",
  "label_b": "QA PDF",
  "pdf_rewrap": true
}
```

## Limits, Concurrency, Cache & Diagnostics

Each PDF read and preview runs in an isolated subprocess worker bounded by the following environment variables:

| Environment Variable | Default | Description |
|---|---|---|
| `ATOM_PDF_MAX_WORKERS` | `2` | Maximum concurrent PDF reader worker processes allowed by the cross-process lock slots. |
| `ATOM_PDF_TIMEOUT_SECONDS` | `120` | Timeout in seconds for full PDF reads (`read` mode), including waiting for a worker slot. |
| `ATOM_PDF_PREVIEW_TIMEOUT_SECONDS` | `60` | Timeout in seconds for PDF preview (`POST /api/compare/bo-report/pdf-preview`) and synchronous precheck on compare submission. |
| `ATOM_PDF_MAX_BYTES` | `52428800` (50 MiB) | Maximum allowed PDF file size in bytes. |
| `ATOM_PDF_MAX_PAGES` | `1000` | Maximum allowed page count per PDF. |
| `ATOM_PDF_SLOT_DIR` | `<tempdir>/atom_pdf_slots` | Directory holding `slot-<n>.lock` files. **Atom instances sharing `ATOM_PDF_SLOT_DIR` share the `ATOM_PDF_MAX_WORKERS` worker limit.** |
| `ATOM_PDF_CACHE_DIR` | `<tempdir>/atom_pdf_cache` | Directory storing parsed JSON results keyed by parser version, mode, PDF SHA-256, and options. |
| `ATOM_PDF_CACHE_MAX_ENTRIES` | `64` | Maximum cached JSON entries retained (oldest pruned by modification time; `0` disables caching). |

### Memory cap (POSIX vs. Windows)

- **POSIX (Linux/macOS):** The worker subprocess enforces a 2 GiB `resource.RLIMIT_DATA` memory cap.
- **Windows:** `resource.RLIMIT_DATA` does not exist on Windows, so there is **no OS-enforced memory cap** on the worker subprocess. Rely on `ATOM_PDF_MAX_BYTES`, `ATOM_PDF_MAX_PAGES`, `ATOM_PDF_MAX_WORKERS`, and `ATOM_PDF_TIMEOUT_SECONDS` to bound memory use on Windows hosts.

### Clearing the parse cache

To clear cached PDF parses, delete the `ATOM_PDF_CACHE_DIR` directory (or `<tempdir>/atom_pdf_cache` when unset). It will be recreated automatically on the next PDF read or preview.

### Diagnostics (`pdf_reader`)

`GET /api/health/diagnostics` (via `DiagnosticsService.build_bundle`) returns a `pdf_reader` object containing:

- **Self-test (`selftest()`):** Spawns an isolated worker against a built-in synthetic 2-row PDF (`ok`, `seconds`, `error`), memoized for 60 seconds.
- **`error_counts`:** A per-code counter map (`ERROR_COUNTS`) of PDF read/preview errors seen since the server started (e.g. `timeout`, `worker_crash`, `too_many_pages`, `section_report`).

## Error Reference

| Exact Error Message | Cause & What To Do |
|---|---|
| `a PDF compare needs a PDF on both sides` | One side is a PDF and the other is a non-PDF format (such as `.xlsx` or `.csv`). Supply PDFs for both Source A and Source B, or compare spreadsheet/CSV exports on both sides instead. |
| `choose a report tab for a PDF export` | Returned when a live BO PDF source leaves `report_id` empty. Note that live BO PDF compare is currently disabled (`PDF export from a live BO source isn't enabled`) until live PDF export is enabled, so selecting a report tab will not enable live BO PDF export yet—export the PDF from SAP BO and submit it as `upload` or `path`. |
| `source is not a PDF` | `POST /api/compare/bo-report/pdf-preview` was called with a non-PDF source. Pass a source with `format: "pdf"` or a `.pdf` file name/path. |
| `PDF export from a live BO source isn't enabled` | `source_type: "live"` was submitted as a PDF source. Export the PDF from SAP BO and submit it as `upload` or `path`. |
| `PDF is supported for path and upload sources` | `source_type` was `"api"` or `"run"` with a PDF format. Use `"path"` or `"upload"` instead. |
| `no data table found in the PDF` | No extractable bordered (or fallback) table remained after stripping prompt blocks. Verify the PDF contains a BO data table and is not just cover/prompt text. |
| `the PDF holds several data tables; choose one` | More than one data table was detected. Run **Preview PDF** and set `pdf.table` to the 1-based table index or a unique header substring. |
| `no data table matches "{choice}"; choose one` | The supplied `pdf.table` index is out of range, or the substring matched zero (or multiple) tables. Pick one of the tables listed in `tables` / PDF Preview. |
| `this PDF uses report sections (group headings above each block); section reports aren't supported yet` | Multiple section headings were detected above table blocks on a page or across pages. Export the report as `.xlsx`/`.csv` from BO, or remove report sections before exporting to PDF. |
| `the PDF is encrypted` | The PDF requires decryption/password access. Remove encryption/password protection before uploading or referencing it. |
| `the PDF has no text layer (scanned?); OCR is not supported` | No text characters exist on any page. Re-export directly from SAP BO as a vector/text PDF rather than scanning or rasterizing to images. |
| `the PDF has no cell borders and is too large to read without them` | A page has no thin border rectangles and exceeds `14,000` edges, so fallback extraction is blocked. Add cell borders in the BO table style or export to `.xlsx`/`.csv`. |
| `the PDF has {n} pages; the limit is {max_pages}` | Page count exceeds `ATOM_PDF_MAX_PAGES` (default `1000`). Narrow the BO report prompt filters or raise `ATOM_PDF_MAX_PAGES` if the host has capacity. |
| `the PDF is {mb:.1f} MB; the limit is {limit_mb:.0f} MB` | File size exceeds `ATOM_PDF_MAX_BYTES` (default 50 MiB). Narrow the report or increase `ATOM_PDF_MAX_BYTES`. |
| `the file is not a readable PDF` | The file is truncated, corrupt, or not a valid PDF. Re-export or re-upload the file. |
| `unsupported table_settings: {sorted(bad)}` | `pdf.table_settings` contains a key outside the allow-list (`vertical_strategy`, `horizontal_strategy`, `snap_tolerance`, `join_tolerance`, `edge_min_length`, `intersection_tolerance`, `text_x_tolerance`, `text_y_tolerance`). Remove unsupported keys. |
| `the PDF could not be read in {n} s` | Extraction exceeded `ATOM_PDF_TIMEOUT_SECONDS` (or `ATOM_PDF_PREVIEW_TIMEOUT_SECONDS` for preview/precheck). Narrow the PDF or raise the timeout env var. |
| `PDF reader crashed` | The isolated worker subprocess exited abnormally without a structured error. Check `GET /api/health/diagnostics` (`pdf_reader`) and server logs; try clearing `ATOM_PDF_CACHE_DIR` if a corrupt file was involved. |
| `PDF reader busy` | HTTP `503`: all `ATOM_PDF_MAX_WORKERS` slots in `ATOM_PDF_SLOT_DIR` remained occupied until the wait deadline. Retry shortly or increase `ATOM_PDF_MAX_WORKERS`. |
| `set key columns for a PDF compare` | No key columns were provided and none could be auto-inferred from standard ID names. Enter `key_columns` explicitly (or click **Use as key** / **Use \<suggested\>**). |
| `key columns aren't unique in {label}: {n} duplicate rows, for example {example}` | The chosen `key_columns` do not uniquely identify rows in Source A or Source B. Add additional columns to `key_columns` (composite key) so every row is unique. |

## Release Gate & Real-Sample Verification

Real BO sample files and expectations are private, untracked, and must never be committed or quoted in docs/logs.

- **Default runs (without private samples):** Tests requiring private samples (`real_pdf`, `corpus`, `bo_expected` fixtures) call `pytest.skip` and print a terminal summary reminder.
- **Release-gate run (with private samples):** Set `ATOM_REQUIRE_REAL_SAMPLES=1` and point `ATOM_BO_SAMPLES_DIR` to the local sample directory (if not stored directly under `tests/fixtures/bo_layout/`):

```powershell
$env:ATOM_REQUIRE_REAL_SAMPLES = "1"
$env:ATOM_BO_SAMPLES_DIR = "C:\path\to\private_bo_samples"
C:\atom\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q -n auto
```

- **Failure log redaction:** When any real-sample test fails, pytest output shows only a redacted notice; full failure details are appended locally to `tests/fixtures/bo_layout/_local_failures.log` (gitignored / local only).
- **Synthetic slow benchmark tests:**

```powershell
Remove-Item Env:ATOM_REQUIRE_REAL_SAMPLES -ErrorAction SilentlyContinue
C:\atom\.venv\Scripts\python.exe -m pytest tests/unit/bo_layout -m slow -s
```

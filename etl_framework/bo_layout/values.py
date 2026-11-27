"""Cell cleaning, number/date parsing, and DataFrame typing for BO PDF tables."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Literal

import pandas as pd

from etl_framework.bo_layout.model import ColumnInfo, PdfReadOptions, PdfTable

DATE_FORMATS: list[str] = [
    "%d-%b-%Y",
    "%d-%b-%y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%A, %B %d, %Y",
]

_TIME_SUFFIXES: tuple[str, ...] = (
    "",
    " %H:%M",
    " %H:%M:%S",
    " %I:%M %p",
    " %I:%M:%S %p",
)

_ALL_DATE_FORMATS: list[str] = [base + suffix for base in DATE_FORMATS for suffix in _TIME_SUFFIXES]

_AMBIGUOUS_PAIRS: set[tuple[str, str]] = {
    ("%m/%d/%y" + suffix, "%d/%m/%y" + suffix) for suffix in _TIME_SUFFIXES
} | {
    ("%m/%d/%Y" + suffix, "%d/%m/%Y" + suffix) for suffix in _TIME_SUFFIXES
}

_UNITS = frozenset({"%", "$", "€", "£", "¥"})

_EN_NUM_RE = re.compile(r"^(?P<int>\d+|\d{1,3}(?:,\d{3})+)(?:\.(?P<frac>\d+))?$")
_EU_NUM_RE = re.compile(
    r"^(?P<int>\d+|\d{1,3}(?P<sep>[. ])\d{3}(?:(?P=sep)\d{3})*)(?:,(?P<frac>\d+))?$"
)

_SHORT_MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_FULL_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_WEEKDAYS = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)

_DAYS_IN_MONTH = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

_TOKEN_PATTERNS: dict[str, str] = {
    "%Y": r"(?P<Y>\d{4})",
    "%y": r"(?P<y>\d{2})",
    "%m": r"(?P<m>\d{1,2})",
    "%d": r"(?P<d>\d{1,2})",
    "%b": r"(?P<b>[A-Za-z]{3})",
    "%B": r"(?P<B>[A-Za-z]+)",
    "%A": r"(?P<A>[A-Za-z]+)",
    "%H": r"(?P<H>\d{1,2})",
    "%I": r"(?P<I>\d{1,2})",
    "%M": r"(?P<M>\d{2})",
    "%S": r"(?P<S>\d{2})",
    "%p": r"(?P<p>[AaPp][Mm])",
}

_TOKEN_RE = re.compile(r"%[YymdbBAHMISp]")


def _compile_date_pattern(fmt: str) -> re.Pattern[str]:
    parts: list[str] = ["^"]
    pos = 0
    for match in _TOKEN_RE.finditer(fmt):
        if match.start() > pos:
            parts.append(re.escape(fmt[pos : match.start()]))
        parts.append(_TOKEN_PATTERNS[match.group(0)])
        pos = match.end()
    if pos < len(fmt):
        parts.append(re.escape(fmt[pos:]))
    parts.append("$")
    return re.compile("".join(parts))


_DATE_PATTERNS: dict[str, re.Pattern[str]] = {
    fmt: _compile_date_pattern(fmt) for fmt in _ALL_DATE_FORMATS
}


def clean_cell(v: str | None) -> str | None:
    """Normalize a cell string with NFKC, hyphen/slash line joining, and collapsed whitespace."""
    if v is None:
        return None
    normalized = unicodedata.normalize("NFKC", v)
    lines = normalized.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = [lines[0]]
    for idx in range(1, len(lines)):
        sep = "" if lines[idx - 1].endswith(("-", "/")) else " "
        parts.append(sep + lines[idx])
    return " ".join("".join(parts).split())


def parse_number(text: str, locale: Literal["en", "eu"]) -> tuple[float, int, str] | None:
    """Parse a formatted number string into (value, displayed_decimals, unit) or None."""
    s = text.strip().replace("\u00a0", " ")
    if not s:
        return None

    negative = False
    has_sign = False
    has_parens = False

    if s[0] in "+-":
        negative = s[0] == "-"
        has_sign = True
        s = s[1:].lstrip()
        if not s or s[0] in "+-(":
            return None
    elif s.startswith("(") and s.endswith(")"):
        negative = True
        has_parens = True
        s = s[1:-1].strip()
        if not s or s[0] in "+-(":
            return None

    unit_pre = ""
    unit_post = ""
    if s and s[0] in _UNITS:
        unit_pre = s[0]
        s = s[1:].lstrip()
    if s and s[-1] in _UNITS:
        unit_post = s[-1]
        s = s[:-1].rstrip()
    if unit_pre and unit_post:
        return None
    unit = unit_pre or unit_post
    if not s:
        return None

    if not has_sign and not has_parens and s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
        if not s or s[0] in "+-(" or any(u in s for u in _UNITS):
            return None

    if locale == "en":
        match = _EN_NUM_RE.fullmatch(s)
        if match is None:
            return None
        int_digits = match.group("int").replace(",", "")
    else:
        match = _EU_NUM_RE.fullmatch(s)
        if match is None:
            return None
        int_digits = match.group("int").replace(".", "").replace(" ", "")

    if len(int_digits) > 1 and int_digits.startswith("0"):
        return None

    frac_digits = match.group("frac") or ""
    sig_digits = (int_digits + frac_digits).lstrip("0")
    if len(sig_digits) > 15:
        return None

    num_str = f"{int_digits}.{frac_digits}" if frac_digits else int_digits
    value = float(num_str)
    if negative and value != 0.0:
        value = -value
    return (value, len(frac_digits), unit)


def _matches_date_format(value: str, fmt: str) -> bool:
    match = _DATE_PATTERNS[fmt].fullmatch(value)
    if match is None:
        return False

    groups = match.groupdict()

    if "A" in groups and groups["A"].lower() not in _WEEKDAYS:
        return False

    if "m" in groups:
        month = int(groups["m"])
        if not (1 <= month <= 12):
            return False
    elif "b" in groups:
        month_opt = _SHORT_MONTHS.get(groups["b"].lower())
        if month_opt is None:
            return False
        month = month_opt
    else:
        month_opt = _FULL_MONTHS.get(groups["B"].lower())
        if month_opt is None:
            return False
        month = month_opt

    if "Y" in groups:
        year = int(groups["Y"])
        if year < 1:
            return False
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    else:
        year = int(groups["y"])
        is_leap = year % 4 == 0

    day = int(groups["d"])
    max_day = 29 if (month == 2 and is_leap) else _DAYS_IN_MONTH[month]
    if not (1 <= day <= max_day):
        return False

    if "H" in groups and not (0 <= int(groups["H"]) <= 23):
        return False
    if "I" in groups and not (1 <= int(groups["I"]) <= 12):
        return False
    if "M" in groups and not (0 <= int(groups["M"]) <= 59):
        return False
    if "S" in groups and not (0 <= int(groups["S"]) <= 59):
        return False

    return True


def infer_date_format(
    values: list[str], date_order: Literal["dmy", "mdy"] | None
) -> tuple[str | None, list[str]]:
    """Infer a single matching date format from values, or report an mdy/dmy ambiguity."""
    if not values:
        return (None, [])

    survivors = [fmt for fmt in _ALL_DATE_FORMATS if all(_matches_date_format(v, fmt) for v in values)]
    if not survivors:
        return (None, [])
    if len(survivors) == 1:
        return (survivors[0], [])

    if len(survivors) == 2 and (survivors[0], survivors[1]) in _AMBIGUOUS_PAIRS:
        if date_order == "mdy":
            return (survivors[0], [])
        if date_order == "dmy":
            return (survivors[1], [])
        return (None, survivors)

    return (None, [])


def _mixed_column_warning(
    col_name: str,
    non_empty: list[str],
    parsed_numbers: list[tuple[float, int, str] | None],
) -> str | None:
    total = len(non_empty)
    unit_counts = Counter(p[2] for p in parsed_numbers if p is not None)
    if unit_counts:
        dominant_unit, num_count = unit_counts.most_common(1)[0]
        share = num_count / total
        if share >= 0.9:
            offending = [
                val
                for val, parsed in zip(non_empty, parsed_numbers)
                if parsed is None or parsed[2] != dominant_unit
            ]
            if offending:
                return f"mixed_column: {col_name} is {share:.0%} number; other values: {', '.join(offending[:3])}"

    best_fmt: str | None = None
    best_count = 0
    for fmt in _ALL_DATE_FORMATS:
        count = sum(1 for val in non_empty if _matches_date_format(val, fmt))
        if count > best_count:
            best_count = count
            best_fmt = fmt

    if best_fmt is not None and best_count / total >= 0.9:
        share = best_count / total
        offending = [val for val in non_empty if not _matches_date_format(val, best_fmt)]
        if offending:
            return f"mixed_column: {col_name} is {share:.0%} date; other values: {', '.join(offending[:3])}"

    return None


def to_frame(
    table: PdfTable, options: PdfReadOptions
) -> tuple[pd.DataFrame, dict[str, ColumnInfo], list[str]]:
    """Convert an assembled PdfTable into a typed DataFrame, ColumnInfo map, and warnings."""
    cleaned_rows = [[clean_cell(c) for c in row] for row in table.rows]
    columns_data: dict[str, pd.Series] = {}
    column_info: dict[str, ColumnInfo] = {}
    warnings: list[str] = []

    for col_idx, col_name in enumerate(table.header):
        cells = [row[col_idx] if col_idx < len(row) else None for row in cleaned_rows]
        non_empty = [c for c in cells if c is not None and c != ""]

        if not non_empty:
            column_info[col_name] = ColumnInfo(kind="text")
            columns_data[col_name] = pd.Series(
                [pd.NA if c is None else c for c in cells], dtype="object"
            )
            continue

        parsed_numbers = [parse_number(c, options.number_locale) for c in non_empty]
        if all(p is not None for p in parsed_numbers):
            units = {p[2] for p in parsed_numbers if p is not None}
            if len(units) == 1:
                shared_unit = next(iter(units))
                max_decimals = max(p[1] for p in parsed_numbers if p is not None)
                parsed_map = dict(zip(non_empty, parsed_numbers))
                series_vals = [
                    float("nan") if (c is None or c == "") else parsed_map[c][0]  # type: ignore[index]
                    for c in cells
                ]
                columns_data[col_name] = pd.Series(series_vals, dtype="float64")
                column_info[col_name] = ColumnInfo(
                    kind="number",
                    displayed_decimals=max_decimals,
                    unit=shared_unit,
                )
                continue

        date_fmt, ambiguous = infer_date_format(non_empty, options.date_order)
        if date_fmt is not None:
            columns_data[col_name] = pd.Series(
                [pd.NA if c is None else c for c in cells], dtype="object"
            )
            column_info[col_name] = ColumnInfo(kind="date", date_format=date_fmt)
            continue

        columns_data[col_name] = pd.Series(
            [pd.NA if c is None else c for c in cells], dtype="object"
        )
        if ambiguous:
            column_info[col_name] = ColumnInfo(kind="text", ambiguous_formats=ambiguous)
            warnings.append(
                f"ambiguous_date_format: {col_name} could be {ambiguous[0]} or {ambiguous[1]}; set the date order"
            )
        else:
            column_info[col_name] = ColumnInfo(kind="text")
            mixed_warn = _mixed_column_warning(col_name, non_empty, parsed_numbers)
            if mixed_warn is not None:
                warnings.append(mixed_warn)

    df = pd.DataFrame(columns_data, columns=list(table.header))
    return df, column_info, warnings

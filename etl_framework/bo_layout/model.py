"""Data models for SAP BusinessObjects PDF report layout parsing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class RawTable:
    rows: list[list[str | None]]
    page: int
    top: float
    bottom: float
    col_x: list[float]
    above_text: str | None
    starts_page: bool
    ends_page: bool


@dataclass
class PdfTable:
    header: list[str]
    rows: list[list[str | None]]
    pages: list[int]
    fragments: int
    col_x: list[float] = field(default_factory=list)  # first fragment's column lines


@dataclass
class PdfPrompt:
    label: str
    value: str


@dataclass
class ColumnInfo:
    kind: Literal["number", "date", "text"]
    displayed_decimals: int | None = None
    date_format: str | None = None
    ambiguous_formats: list[str] = field(default_factory=list)
    unit: str = ""  # "", "%", or a currency symbol shared by the column

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnInfo:
        return cls(
            kind=data["kind"],
            displayed_decimals=data.get("displayed_decimals"),
            date_format=data.get("date_format"),
            ambiguous_formats=list(data.get("ambiguous_formats") or []),
            unit=data.get("unit", ""),
        )


@dataclass
class PdfReadOptions:
    table: int | str | None = None
    number_locale: Literal["en", "eu"] = "en"
    date_order: Literal["dmy", "mdy"] | None = None
    fill_down: bool = True
    table_settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PdfReadOptions:
        return cls(
            table=data.get("table"),
            number_locale=data.get("number_locale", "en"),
            date_order=data.get("date_order"),
            fill_down=data.get("fill_down", True),
            table_settings=dict(data.get("table_settings") or {}),
        )


@dataclass
class PdfSourceInfo:
    file_name: str
    sha256: str
    bytes: int
    pages: int
    producer: str | None
    creator: str | None
    table: dict[str, Any]  # header, pages, rows, stitched_fragments, column_types, displayed_decimals, column_widths
    filled_cells: int
    dropped_empty_rows: int
    dropped_repeated_headers: int
    ignored_tables: list[dict[str, Any]]
    prompts: list[PdfPrompt]
    warnings: list[str]
    column_info: dict[str, ColumnInfo]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PdfSourceInfo:
        prompts = [
            p if isinstance(p, PdfPrompt) else PdfPrompt(label=p["label"], value=p["value"])
            for p in (data.get("prompts") or [])
        ]
        column_info = {
            k: v if isinstance(v, ColumnInfo) else ColumnInfo.from_dict(v)
            for k, v in (data.get("column_info") or {}).items()
        }
        return cls(
            file_name=data["file_name"],
            sha256=data["sha256"],
            bytes=data["bytes"],
            pages=data["pages"],
            producer=data.get("producer"),
            creator=data.get("creator"),
            table=dict(data["table"]),
            filled_cells=data["filled_cells"],
            dropped_empty_rows=data["dropped_empty_rows"],
            dropped_repeated_headers=data["dropped_repeated_headers"],
            ignored_tables=list(data.get("ignored_tables") or []),
            prompts=prompts,
            warnings=list(data.get("warnings") or []),
            column_info=column_info,
        )


class PdfReadError(Exception):
    def __init__(self, code: str, message: str, tables: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.tables: list[dict[str, Any]] = list(tables) if tables is not None else []

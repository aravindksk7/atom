from __future__ import annotations
import base64
import io
import pytest
import pandas as pd
from fastapi import HTTPException


def test_read_tabular_from_csv_path(tmp_path):
    from api.services.file_source import read_tabular
    f = tmp_path / "data.csv"
    f.write_text("id,amount\n1,100\n2,200\n")
    df = read_tabular(path=str(f))
    assert list(df.columns) == ["id", "amount"]
    assert len(df) == 2


def test_read_tabular_from_bo_csv_with_metadata_preamble(tmp_path):
    from api.services.file_source import read_tabular
    f = tmp_path / "report.csv"
    f.write_text(
        "Report Name: Monthly Regional Sales Summary\n"
        "Run Date: 2026-06-14\n"
        "Universe Data Source: Sales_and_Operations_Cube\n"
        "--------------------------------------------------\n"
        "Region,Employee ID,Amount\n"
        "North,EM1092,7500.00\n",
        encoding="utf-8",
    )
    df = read_tabular(path=str(f))
    assert list(df.columns) == ["Region", "Employee ID", "Amount"]
    assert df.iloc[0]["Employee ID"] == "EM1092"


def test_read_tabular_from_xlsx_upload():
    from api.services.file_source import read_tabular
    buf = io.BytesIO()
    df_in = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    df_in.to_excel(buf, index=False)
    b64 = base64.b64encode(buf.getvalue()).decode()
    df = read_tabular(content_b64=b64, file_name="data.xlsx")
    assert list(df.columns) == ["id", "val"]
    assert len(df) == 2


def test_read_tabular_from_csv_upload():
    from api.services.file_source import read_tabular
    csv_bytes = b"x,y\n1,2\n3,4\n"
    b64 = base64.b64encode(csv_bytes).decode()
    df = read_tabular(content_b64=b64, file_name="data.csv")
    assert len(df) == 2


def test_read_tabular_unsupported_format_raises_400():
    from api.services.file_source import read_tabular
    b64 = base64.b64encode(b"garbage").decode()
    with pytest.raises(HTTPException) as exc_info:
        read_tabular(content_b64=b64, file_name="data.json")
    assert exc_info.value.status_code == 400


def test_read_tabular_no_input_raises_400():
    from api.services.file_source import read_tabular
    with pytest.raises(HTTPException) as exc_info:
        read_tabular()
    assert exc_info.value.status_code == 400

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.repository.repository import RunRepository
from etl_framework.runner.state import TestStatus


@pytest.fixture(autouse=True)
def _memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    yield


def _create_run_with_result(total_issues: int, stored_rows: int, query_name: str = "orders") -> str:
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id=f"run-full-html-{total_issues}-{stored_rows}",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "unknown", "request": {}},
        )
        result = ReconciliationResult(
            query_name=query_name,
            source_env="dev",
            target_env="prod",
            source_row_count=10,
            target_row_count=10,
            matched_count=10,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=total_issues,
            mismatches=[],
            status=TestStatus.FAILED if total_issues else TestStatus.PASSED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        )
        tr = repo.add_test_result(run.run_id, result)
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(
                key_values={"id": idx + 1},
                column_name="amount",
                source_value=idx,
                target_value=idx + 10,
                mismatch_type="value_diff",
                delta=10.0,
                relative_delta=None,
            )
            for idx in range(stored_rows)
        ])
        return run.run_id


def test_write_full_html_report_includes_rows_beyond_the_stored_cap(tmp_path, monkeypatch):
    """total_issues=5, only 2 stored -- write_full_html_report must recompute and
    bake in all 5, not just the 2 that made it into mismatch_details."""
    from api.services import difference_export as de
    from etl_framework.repository.models import TestRun

    run_id = _create_run_with_result(total_issues=5, stored_rows=2)

    def _fake_recompute(db, run, fmt, path):
        rows = [
            {
                "test_name": "orders", "key_values": json.dumps({"id": i}),
                "column_name": "amount", "source_value": str(i), "target_value": str(i + 100),
                "mismatch_type": "value_diff", "delta": 100.0, "relative_delta": None,
            }
            for i in range(5)
        ]
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return len(rows)

    monkeypatch.setattr(de, "write_recomputed_differences", _fake_recompute)

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        row_count = de.write_full_html_report(db, run, out_path)

    assert row_count == 5
    html = out_path.read_text(encoding="utf-8")
    # count actual rendered rows (`<tr data-mismatch`), not the substring
    # "data-mismatch" alone -- that also appears inside the template's JS as
    # `'tr[data-mismatch]'` selector strings, which would inflate a bare count
    assert html.count("<tr data-mismatch") == 5
    # nothing left to load -- the global "Load all differences" button must not render
    assert "load-all-btn-global" not in html


def test_write_full_html_report_key_values_round_trip_as_dict_not_double_encoded_string(tmp_path, monkeypatch):
    """DifferenceWriter pre-serializes key_values to a JSON string (_json_text); the
    recompute reader must json.loads it back to a dict, or the template's
    `mm.key_values | tojson` double-encodes it into an unusable string literal."""
    from api.services import difference_export as de
    from etl_framework.repository.models import TestRun

    run_id = _create_run_with_result(total_issues=1, stored_rows=0)

    def _fake_recompute(db, run, fmt, path):
        row = {
            "test_name": "orders", "key_values": json.dumps({"id": 42}),
            "column_name": "amount", "source_value": "1", "target_value": "2",
            "mismatch_type": "value_diff", "delta": 1.0, "relative_delta": None,
        }
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return 1

    monkeypatch.setattr(de, "write_recomputed_differences", _fake_recompute)

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")
    import re
    from html import unescape
    match = re.search(r'data-key="([^"]*)"', html)
    assert match is not None
    parsed = json.loads(unescape(match.group(1)))
    # single-encoded dict, not a JSON string containing an escaped JSON string
    assert parsed == {"id": 42}


def test_write_full_html_report_applies_severity_colours_and_search(tmp_path, monkeypatch):
    """The "Download Full HTML Report" path goes through write_full_html_report, not
    the plain report writer -- assert the downloaded file itself carries the severity
    colour coding and the inlined search engine, with no mocks in the way.

    Rows missing on each side exercise both directions: the recompute writes the
    literal sentinels "present"/"missing" as values, which is what used to render
    red-for-present / green-for-missing.
    """
    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.repository.models import TestRun

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path,))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)

    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    # ids 9,10 only in source -> missing_in_target; id 11 only in target ->
    # missing_in_source; id 1 differs in value -> value_diff.
    src.write_text("id,amount\n1,100\n2,2\n9,9\n10,10\n", encoding="utf-8")
    tgt.write_text("id,amount\n1,101\n2,2\n11,11\n", encoding="utf-8")

    request = {"file_a_path": str(src), "file_b_path": str(tgt), "key_columns": ["id"]}
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-full-html-severity",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "recon_file", "request": request},
        )
        repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="Run / File A",
            source_env="dev",
            target_env="prod",
            source_row_count=4,
            target_row_count=3,
            matched_count=2,
            missing_in_target_count=2,
            missing_in_source_count=1,
            value_mismatch_count=1,
            mismatches=[],
            status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        ))
        run_id = run.run_id

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")

    # Both missing directions really were produced by the recompute.
    assert 'data-type="missing_in_target"' in html
    assert 'data-type="missing_in_source"' in html

    # Severity colour coding reached the downloaded file.
    assert "diff-panel-absent" in html
    assert "presence-absent" in html
    assert "presence-present" in html
    assert '<span class="badge badge-fail">missing_in_target</span>' in html
    assert '<span class="badge badge-fail">missing_in_source</span>' in html
    assert '<span class="badge badge-amber">value_diff</span>' in html
    # The sentinels are no longer emitted as raw diffable values.
    assert ">present</span>" not in html
    assert ">missing</span>" not in html

    # Search engine is inlined, so the downloaded file searches offline.
    assert "function parseDiffQuery" in html
    assert "matchesDiffQuery(rowSearchFields(tr), terms)" in html
    assert 'id="filter-help-chips"' in html
    assert "<script src=" not in html


def test_write_full_html_report_header_carries_a_meaningful_run_label(tmp_path, monkeypatch):
    """A bare uuid4 in the header says nothing about what the run was. The
    downloaded file must lead with the derived label -- and still carry the full
    id, since that is what URLs and export paths key on."""
    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.repository.models import TestRun

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path,))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)

    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("id,amount\n1,100\n", encoding="utf-8")
    tgt.write_text("id,amount\n1,101\n", encoding="utf-8")

    run_id = "abc12345-0000-4000-8000-000000000000"
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id=run_id,
            source_env="dev",
            target_env="prod",
            config_snapshot={
                "compare_request_type": "recon_file",
                "request": {"file_a_path": str(src), "file_b_path": str(tgt), "key_columns": ["id"]},
            },
        )
        repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="Run / File A",
            source_env="dev", target_env="prod",
            source_row_count=1, target_row_count=1, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        ))

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")

    label = "file compare · dev → prod · abc12345"
    assert f'<span id="run-label" style="font-weight:600">{label}</span>' in html
    # Distinguishes one downloaded file (and browser tab) from the next.
    assert f"<title>ETL Report - {label}</title>" in html
    # The full id is still present and copyable.
    assert f'<span id="run-id" style="font-family: monospace;">{run_id}</span>' in html


def test_write_full_html_report_real_recompute_renders_every_row_untruncated(tmp_path, monkeypatch):
    """No mocks: a recon_file compare with 150 differing rows (well past the report's
    usual 100-row display cap) recomputed from the real CSV files must render all 150
    mismatch rows and no truncation banners/buttons."""
    from api.services import difference_export as de
    from etl_framework.repository.models import TestRun

    # _UPLOAD_BASES is resolved from env at import time, so patch the module
    # globals directly to allow reading CSVs from tmp_path
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path,))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)

    n_rows, n_diffs = 200, 150
    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text(
        "id,amount\n" + "\n".join(f"{i},{i}" for i in range(1, n_rows + 1)) + "\n",
        encoding="utf-8",
    )
    tgt.write_text(
        "id,amount\n" + "\n".join(
            f"{i},{i + 1000 if i <= n_diffs else i}" for i in range(1, n_rows + 1)
        ) + "\n",
        encoding="utf-8",
    )

    request = {
        "file_a_path": str(src),
        "file_b_path": str(tgt),
        "key_columns": ["id"],
    }
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-full-html-real-recompute",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "recon_file", "request": request},
        )
        result = ReconciliationResult(
            query_name="Run / File A",  # ReconFileCompareRequest.label_a default
            source_env="dev",
            target_env="prod",
            source_row_count=n_rows,
            target_row_count=n_rows,
            matched_count=n_rows - n_diffs,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=n_diffs,
            mismatches=[],
            status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        )
        repo.add_test_result(run.run_id, result)
        run_id = run.run_id

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        row_count = de.write_full_html_report(db, run, out_path)

    assert row_count == n_diffs
    html = out_path.read_text(encoding="utf-8")
    assert html.count("<tr data-mismatch") == n_diffs
    assert "load-all-btn-global" not in html
    assert "truncation-note" not in html


def test_write_full_html_report_carries_accepted_and_rejected_triage(tmp_path, monkeypatch):
    """Accepting or rejecting a mismatch is a decision the Web UI paints green/red.
    The full report rebuilds its rows from the recompute, so that triage has to be
    re-attached from the stored details or the download shows every row as untouched.
    """
    import re
    from html import unescape

    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.repository.models import MismatchDetail, TestRun

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path,))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)

    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("id,amount\n1,100\n2,2\n9,9\n", encoding="utf-8")
    tgt.write_text("id,amount\n1,101\n2,2\n11,11\n", encoding="utf-8")

    request = {"file_a_path": str(src), "file_b_path": str(tgt), "key_columns": ["id"]}
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-full-html-triage",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "recon_file", "request": request},
        )
        tr = repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="Run / File A",
            source_env="dev", target_env="prod",
            source_row_count=3, target_row_count=3, matched_count=1,
            missing_in_target_count=1, missing_in_source_count=1, value_mismatch_count=1,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        ))
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="100",
                           target_value="101", mismatch_type="value_diff",
                           delta=1.0, relative_delta=None),
            MismatchRecord(key_values={"id": 9}, column_name="<row>", source_value="present",
                           target_value="missing", mismatch_type="missing_in_target",
                           delta=None, relative_delta=None),
        ])
        db.commit()
        details = {d.column_name: d for d in db.query(MismatchDetail).all()}
        details["amount"].accepted = True
        details["amount"].accepted_by = "qa"
        details["amount"].accepted_note = "known FX rounding"
        details["<row>"].rejected = True
        details["<row>"].rejected_by = "qa"
        details["<row>"].rejected_note = "source feed was late"
        db.commit()
        run_id = run.run_id

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")

    rows = re.findall(r"<tr data-mismatch.*?</tr>", html, re.S)
    accepted_row = next(r for r in rows if '{"id": 1}' in unescape(r))
    rejected_row = next(r for r in rows if '{"id": 9}' in unescape(r))
    assert 'data-accepted="true"' in accepted_row
    assert 'data-rejected="true"' in rejected_row

    # ...and the decision is spelled out in its own tinted note row, as the
    # capped report does.
    assert "known FX rounding" in html
    assert "source feed was late" in html
    assert html.count('<td colspan="5" class="accepted-note">') == 1
    assert html.count('<td colspan="5" class="rejected-note">') == 1


def test_write_full_html_report_renders_an_empty_cell_as_the_null_marker(tmp_path, monkeypatch):
    """A blank cell comes back from the recompute as pandas' NaN. Rendered raw it
    reads as the literal string "nan" -- a value the source never contained -- where
    the Web UI shows a greyed NULL marker."""
    import re
    from html import unescape

    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.repository.models import TestRun

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path,))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)

    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("id,amount\n1,100\n3,7\n", encoding="utf-8")
    tgt.write_text("id,amount\n1,100\n3,\n", encoding="utf-8")

    request = {"file_a_path": str(src), "file_b_path": str(tgt), "key_columns": ["id"]}
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-full-html-null",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "recon_file", "request": request},
        )
        repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="Run / File A",
            source_env="dev", target_env="prod",
            source_row_count=2, target_row_count=2, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        ))
        run_id = run.run_id

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")
    row = next(r for r in re.findall(r"<tr data-mismatch.*?</tr>", html, re.S)
               if '{"id": 3}' in unescape(r))

    assert '<span class="null-val">NULL</span>' in row
    assert "nan" not in row

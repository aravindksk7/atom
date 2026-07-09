"""Smoke tests for report.html.j2 — verifies the template renders and includes
key HTML landmarks introduced by the enhanced diff display feature."""
import types
from datetime import datetime, timezone

from etl_framework.reporting.generator import ReportGenerator


def _make_suite(mismatches=None):
    mm_list = mismatches or []

    result = types.SimpleNamespace(
        query_name="orders_recon",
        status="FAILED",
        duration_seconds=1.23,
        source_row_count=100,
        target_row_count=98,
        total_issues=len(mm_list),
        value_mismatch_count=sum(1 for m in mm_list if m.mismatch_type == "value_mismatch"),
        missing_in_target_count=sum(1 for m in mm_list if m.mismatch_type == "missing_in_target"),
        missing_in_source_count=sum(1 for m in mm_list if m.mismatch_type == "missing_in_source"),
        mismatches=mm_list,
        schema_diff=None,
        effective_status="FAILED",
        override_status=None,
    )

    suite = types.SimpleNamespace(
        run_id="test-run-001",
        started_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
        source_env="dev",
        target_env="prod",
        test_cases=[result],
        reconciliation_results=[result],
        total_passed=0,
        total_failed=1,
        total_skipped=0,
    )
    return suite


def _make_mm(col, src, tgt, mm_type="value_mismatch"):
    return types.SimpleNamespace(
        column_name=col,
        source_value=src,
        target_value=tgt,
        mismatch_type=mm_type,
        key_values={"id": 1},
        accepted=False,
        accepted_by=None,
        accepted_at=None,
        accepted_note=None,
    )


def _render(suite, tmp_path):
    gen = ReportGenerator(output_dir=str(tmp_path))
    path = gen.generate(suite)
    return open(path, encoding="utf-8").read()


class TestReportTemplateSmoke:
    def test_renders_without_error(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "ETL Framework Execution Report" in html

    def test_stat_cards_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "stat-total-mm" in html
        assert "stat-duration" in html
        assert "nav-pill" in html

    def test_analytics_placeholders_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert 'id="col-heatmap"' in html
        assert 'id="type-donut"' in html

    def test_filter_toolbar_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert 'id="filter-toolbar"' in html
        assert 'id="filter-search"' in html

    def test_mismatch_row_data_attributes(self, tmp_path):
        mm = _make_mm("amount", "100.00", "100.01")
        html = _render(_make_suite([mm]), tmp_path)
        assert "data-mismatch" in html
        assert 'data-column="amount"' in html
        assert 'data-type="value_mismatch"' in html
        assert 'data-role="src-diff"' in html
        assert 'data-role="tgt-diff"' in html

    def test_diff_panels_present_for_mismatches(self, tmp_path):
        mm = _make_mm("status", "active", "inactive")
        html = _render(_make_suite([mm]), tmp_path)
        assert "diff-panel-src" in html
        assert "diff-panel-tgt" in html
        assert "copy-btn" in html

    def test_js_block_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "charDiff" in html
        assert "renderSrc" in html
        assert "applyDiff" in html
        assert "buildHeatmap" in html
        assert "buildDonut" in html

    def test_expand_collapse_buttons_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "setAllDetails(true)" in html
        assert "setAllDetails(false)" in html

    def test_source_target_env_in_header(self, tmp_path):
        mm = _make_mm("col1", "a", "b")
        html = _render(_make_suite([mm]), tmp_path)
        assert "dev" in html
        assert "prod" in html

    def test_effective_status_is_rendered_with_raw_status_note(self, tmp_path):
        suite = _make_suite()
        suite.reconciliation_results[0].status = "FAILED"
        suite.reconciliation_results[0].effective_status = "PASSED"
        suite.total_passed = 1
        suite.total_failed = 0

        html = _render(suite, tmp_path)

        assert "PASSED" in html
        assert "raw: FAILED" in html


def test_accepted_at_rendered_via_to_local_filter(tmp_path):
    accepted_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    mm = _make_mm("amount", "100.00", "100.01")
    mm.accepted = True
    mm.accepted_by = "alice"
    mm.accepted_at = accepted_dt
    html = _render(_make_suite([mm]), tmp_path)
    expected = accepted_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    assert expected in html

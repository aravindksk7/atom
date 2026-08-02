"""Smoke tests for report.html.j2 — verifies the template renders and includes
key HTML landmarks introduced by the enhanced diff display feature."""
import types
from datetime import datetime, timezone

from api.services.run_report import ReportResult, RunReportSnapshot
from etl_framework.reporting.generator import ReportGenerator


def _make_suite(mismatches=None, total_issues=None):
    mm_list = mismatches or []
    issue_count = len(mm_list) if total_issues is None else total_issues

    result = types.SimpleNamespace(
        query_name="orders_recon",
        status="FAILED",
        duration_seconds=1.23,
        source_row_count=100,
        target_row_count=98,
        total_issues=issue_count,
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
        total_issues=issue_count,
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

    def test_mismatch_summary_uses_total_not_rendered_detail_count(self, tmp_path):
        mismatches = [
            _make_mm("amount", "100.00", "100.01"),
            _make_mm("status", "active", "inactive"),
        ]
        html = _render(_make_suite(mismatches, total_issues=12000), tmp_path)

        assert 'data-total-issues="12000"' in html
        assert ">12000</div>" in html
        assert "Showing first 2 of 12000" in html
        assert "download the full differences export for all differences" in html

    def test_effective_status_is_rendered_with_raw_status_note(self, tmp_path):
        suite = _make_suite()
        suite.reconciliation_results[0].status = "FAILED"
        suite.reconciliation_results[0].effective_status = "PASSED"
        suite.total_passed = 1
        suite.total_failed = 0

        html = _render(suite, tmp_path)

        assert "PASSED" in html
        assert "raw: FAILED" in html

    def test_multi_file_pair_rollup_rendered(self, tmp_path):
        suite = _make_suite()
        result = suite.reconciliation_results[0]
        result.source_file_name = "2 file(s) across 2 pair(s)"
        result.target_file_name = "2 file(s) across 2 pair(s)"
        result.mismatch_summary = {
            "pairs_total": 2,
            "pairs_passed": 1,
            "pairs_failed": 1,
            "pairs_errored": 0,
            "file_pairs": [
                {"key": {"region": "east"}, "status": "PASSED", "source_files": ["sales_east.csv"], "target_files": ["financials_east.csv"], "value_mismatch_count": 0},
                {"key": {"region": "west"}, "status": "FAILED", "source_files": ["sales_west.csv"], "target_files": ["financials_west.csv"], "value_mismatch_count": 1},
            ],
            "unmatched_sources": [{"key": {"region": "north"}, "files": ["sales_north.csv"]}],
            "unmatched_targets": [],
        }

        html = _render(suite, tmp_path)

        assert "File pairs" in html
        assert "region=west" in html
        assert "sales_west.csv" in html
        assert "financials_west.csv" in html
        # Unmatched files now read as "absent on the other side" rather than the
        # neutral "Unmatched sources" heading -- see TestMultiFilePairSeverity.
        assert 'data-testid="unmatched-sources"' in html
        assert "no target counterpart" in html
        assert "sales_north.csv" in html


def test_started_at_rendered_via_to_local_filter(tmp_path):
    """The header interpolated the raw datetime, so it read
    "2026-07-01 12:00:00+00:00" -- microseconds and a UTC offset, while every
    other timestamp in the report is localized."""
    suite = _make_suite()
    html = _render(suite, tmp_path)

    expected = suite.started_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    assert f"<strong>Started:</strong> {expected}</p>" in html
    assert str(suite.started_at) not in html


def test_started_at_missing_renders_a_dash_not_none(tmp_path):
    suite = _make_suite()
    suite.started_at = None

    html = _render(suite, tmp_path)

    assert "<strong>Started:</strong> —</p>" in html
    assert "<strong>Started:</strong> None" not in html


def test_to_local_passes_through_a_non_datetime_unchanged():
    """Callers hand the template several suite shapes; a filter that assumed
    datetime would blow up the whole report over one field."""
    from etl_framework.reporting.generator import to_local

    assert to_local("2026-07-01T12:00:00Z") == "2026-07-01T12:00:00Z"
    assert to_local(None) == ""


class TestMultiFilePairKey:
    """Multi-file rows carry their file pair under __pair__ inside key_values.
    It is pairing metadata, not row identity, so it renders as its own chip and
    filters on its own rather than sitting raw inside the key JSON."""

    def _paired_mm(self, region, kid, col="amount"):
        mm = _make_mm(col, "100", "101", mm_type="value_diff")
        mm.key_values = {"__pair__": {"region": region}, "id": kid}
        return mm

    def _rendered_rows(self, html):
        """Just the rendered mismatch markup -- not the script block below it,
        which legitimately mentions pair-chip as a string it builds."""
        body = html.split('id="mismatches"')[1]
        return body.split("<script")[0]

    def _key_cell(self, html):
        """The visible Row Key Values cell. data-key deliberately keeps the raw
        key including __pair__ -- the search engine splits key: from pair: off
        that one attribute -- so only the cell itself must be clean."""
        marker = '<td style="font-family: monospace; font-size: 0.85em;">'
        start = html.index(marker, html.index('id="mismatches"'))
        return html[start:html.index("</td>", start)]

    def test_pair_renders_as_a_chip_and_leaves_the_key_clean(self, tmp_path):
        html = _render(_make_suite([self._paired_mm("west", 9)]), tmp_path)

        assert '<span class="pair-chip" title="File pair this row came from">region=west</span>' in html
        assert '{"id": 9}' in self._key_cell(html)
        # The raw metadata no longer leaks into the visible key cell.
        assert "__pair__" not in self._key_cell(html)

    def test_row_carries_the_pair_for_filtering(self, tmp_path):
        html = _render(_make_suite([self._paired_mm("west", 9)]), tmp_path)
        assert 'data-pair="region=west"' in html

    def test_pair_filter_control_exists_and_is_applied(self, tmp_path):
        html = _render(_make_suite([self._paired_mm("west", 9)]), tmp_path)

        assert 'id="filter-pair"' in html
        assert "populatePairFilter" in html
        assert "tr.dataset.pair===filterState.pair" in html

    def test_unpaired_rows_get_no_chip(self, tmp_path):
        html = _render(_make_suite([_make_mm("amount", "1", "2")]), tmp_path)

        assert "pair-chip" not in self._rendered_rows(html)
        assert 'data-pair=""' in html

    def test_load_all_injection_builds_the_same_chip(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        # the whole function body, not a fixed-width window -- the slice used to
        # cut the assertions off whenever the builder grew a line
        builder = html.split("function buildMismatchRow")[1].split("\n  function ")[0]

        assert "diffPairLabel(row.key_values)" in builder
        assert "diffKeyWithoutPair(row.key_values)" in builder


class TestMultiFilePairSeverity:
    """A multi-file run's per-pair rollup is the one place a whole FILE can be
    missing, and it rendered every pair identically -- plain bold status text,
    same neutral border for PASSED and FAILED, and unmatched files as an
    unstyled list. Same defect as the row-level colours, one level up."""

    def _suite(self):
        suite = _make_suite()
        result = suite.reconciliation_results[0]
        result.source_file_name = "3 file(s) across 3 pair(s)"
        result.target_file_name = "2 file(s) across 2 pair(s)"
        result.mismatch_summary = {
            "file_pairs": [
                {"key": {"region": "east"}, "status": "PASSED",
                 "source_files": ["sales_east.csv"], "target_files": ["fin_east.csv"],
                 "source_row_count": 2, "target_row_count": 2, "value_mismatch_count": 0},
                {"key": {"region": "west"}, "status": "FAILED",
                 "source_files": ["sales_west.csv"], "target_files": ["fin_west.csv"],
                 "source_row_count": 2, "target_row_count": 2, "value_mismatch_count": 1},
                {"key": {"region": "south"}, "status": "ERROR",
                 "source_files": ["sales_south.csv"], "target_files": ["fin_south.csv"],
                 "error": "could not parse header"},
            ],
            "unmatched_sources": [{"key": {"region": "north"}, "files": ["sales_north.csv"]}],
            "unmatched_targets": [{"key": {"region": "far"}, "files": ["fin_far.csv"]}],
        }
        return suite

    def _pair_block(self, html, region):
        """One pair's markup: from its wrapper up to the next pair (or the
        unmatched-files block), so nested divs inside it are not cut off."""
        start = html.rindex('<div data-testid="file-pair-row"', 0, html.index(f"region={region}"))
        rest = html[start + 1:]
        ends = [
            offset for offset in (
                rest.find('<div data-testid="file-pair-row"'),
                rest.find('data-testid="unmatched-'),
            ) if offset != -1
        ]
        return rest[:min(ends)] if ends else rest

    def _block(self, html, testid, until):
        """Markup of one data-testid block, up to the next landmark -- slicing at
        the first </div> would cut off the nested file list."""
        start = html.index(f'data-testid="{testid}"')
        rest = html[start:]
        end = rest.find(until, 1)
        return rest[:end] if end != -1 else rest

    def test_pair_status_uses_the_same_badges_as_the_results_table(self, tmp_path):
        html = _render(self._suite(), tmp_path)

        assert 'class="badge badge-pass">PASSED' in self._pair_block(html, "east")
        assert 'class="badge badge-fail">FAILED' in self._pair_block(html, "west")
        assert 'class="badge badge-fail">ERROR' in self._pair_block(html, "south")
        # The unstyled bold status is gone.
        assert "<strong>FAILED</strong>" not in html

    def test_failing_pairs_carry_a_severity_accent(self, tmp_path):
        html = _render(self._suite(), tmp_path)

        assert "pair-row-failed" in self._pair_block(html, "west")
        assert "pair-row-failed" in self._pair_block(html, "south")
        assert "pair-row-failed" not in self._pair_block(html, "east")

    def test_unmatched_files_read_as_absent_on_the_other_side(self, tmp_path):
        html = _render(self._suite(), tmp_path)

        sources = self._block(html, "unmatched-sources", "unmatched-targets")
        assert "presence-absent" in sources
        assert "sales_north.csv" in sources
        # Says which side is missing it, rather than just "unmatched".
        assert "no target" in sources.lower()

        targets = self._block(html, "unmatched-targets", "</details>")
        assert "presence-absent" in targets
        assert "fin_far.csv" in targets
        assert "no source" in targets.lower()

    def test_pair_error_still_shown(self, tmp_path):
        html = _render(self._suite(), tmp_path)
        assert "could not parse header" in self._pair_block(html, "south")


def test_accepted_at_rendered_via_to_local_filter(tmp_path):
    accepted_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    mm = _make_mm("amount", "100.00", "100.01")
    mm.accepted = True
    mm.accepted_by = "alice"
    mm.accepted_at = accepted_dt
    html = _render(_make_suite([mm]), tmp_path)
    expected = accepted_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    assert expected in html


def _make_snapshot(results, run_id="run-load-all"):
    return RunReportSnapshot(
        run_id=run_id,
        status="FAILED",
        raw_status="FAILED",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_env="dev",
        target_env="qa",
        config_snapshot=None,
        run_type="reconciliation",
        pair_id=None,
        total_tests=len(results),
        passed=0,
        failed=len(results),
        slow=0,
        error=0,
        raw_total_tests=len(results),
        raw_passed=0,
        raw_failed=len(results),
        raw_slow=0,
        raw_error=0,
        results=results,
        has_result_rows=True,
    )


def test_load_all_buttons_present_when_truncated(tmp_path):
    truncated = ReportResult(
        id=1,
        query_name="orders",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=1.0,
        source_row_count=20,
        target_row_count=20,
        value_mismatch_count=1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatches=[_make_mm("amount", "1", "2")],
        total_issues_override=20,
    )
    complete = ReportResult(
        id=2,
        query_name="invoices",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=0.5,
        source_row_count=1,
        target_row_count=1,
        value_mismatch_count=1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatches=[_make_mm("amount", "1", "2")],
    )

    html = _render(_make_snapshot([truncated, complete]), tmp_path)

    assert 'id="load-all-btn-global"' in html
    assert 'id="load-all-btn-1"' in html
    assert 'id="mismatch-tbody-1"' in html
    assert 'id="truncation-text-1"' in html
    # The complete (non-truncated) result gets no per-test load-all button.
    assert 'id="load-all-btn-2"' not in html
    assert 'id="mismatch-tbody-2"' in html


def test_load_all_global_button_absent_when_nothing_truncated(tmp_path):
    complete = ReportResult(
        id=1,
        query_name="orders",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=1.0,
        source_row_count=1,
        target_row_count=1,
        value_mismatch_count=1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatches=[_make_mm("amount", "1", "2")],
    )

    html = _render(_make_snapshot([complete]), tmp_path)

    assert 'id="load-all-btn-global"' not in html
    assert 'id="load-all-btn-1"' not in html


def test_load_all_zero_stored_rows_skeleton_present(tmp_path):
    zero_stored = ReportResult(
        id=3,
        query_name="products",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=1.0,
        source_row_count=5,
        target_row_count=5,
        value_mismatch_count=0,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatches=[],
        total_issues_override=5,
    )

    html = _render(_make_snapshot([zero_stored]), tmp_path)

    assert 'id="load-all-btn-global"' in html
    assert 'id="load-all-btn-3"' in html
    assert 'id="mismatch-tbody-3"' in html
    assert 'id="truncation-text-3"' in html


def test_analytics_use_uncapped_aggregate_counts(tmp_path):
    result = ReportResult(
        id=1,
        query_name="orders",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=1.0,
        source_row_count=6_000,
        target_row_count=6_000,
        value_mismatch_count=12_000,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatch_summary={
            "by_column": {"amount": 6_000, "status": 6_000},
            "by_type": {
                "value_diff": 12_000,
                "missing_in_target": 0,
                "missing_in_source": 0,
            },
        },
        mismatches=[],
    )
    snapshot = RunReportSnapshot(
        run_id="run-1",
        status="FAILED",
        raw_status="FAILED",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_env="source",
        target_env="target",
        config_snapshot=None,
        run_type="reconciliation",
        pair_id=None,
        total_tests=1,
        passed=0,
        failed=1,
        slow=0,
        error=0,
        raw_total_tests=1,
        raw_passed=0,
        raw_failed=1,
        raw_slow=0,
        raw_error=0,
        results=[result],
        has_result_rows=True,
    )

    html = _render(snapshot, tmp_path)

    assert "Top Columns by Mismatch Count" in html
    assert "Mismatch Type Breakdown" in html
    assert "Top Columns by Displayed Mismatch Count" not in html
    assert "Displayed Mismatch Type Breakdown" not in html
    assert '"amount": 6000' in html
    assert '"status": 6000' in html
    assert '"value_diff": 12000' in html
    assert ">total<" in html


def test_filter_search_is_debounced(tmp_path):
    html = _render(_make_suite(), tmp_path)
    assert "setTimeout" in html.split('id="filter-search"')[1][:400]


class TestSearchWiring:
    """The report's filter box must search every field, not just key values."""

    def test_search_engine_is_inlined(self, tmp_path):
        html = _render(_make_suite(), tmp_path)

        # Inlined, not linked: the downloaded report has to work offline.
        assert "function parseDiffQuery" in html
        assert "function matchesDiffQuery" in html
        assert "<script src=" not in html

    def test_filters_run_the_shared_query_engine(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        applied = html.split("function applyFilters")[1][:900]

        assert "matchesDiffQuery(rowSearchFields(tr), terms)" in applied
        # The old behaviour -- substring over the key JSON alone -- is gone.
        assert "dataset.key.toLowerCase().includes" not in html

    def test_search_affordances_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)

        assert 'id="filter-help"' in html
        assert 'id="filter-empty"' in html
        assert "col:amount" in html
        assert "-type:value_diff" in html
        assert "buildSearchHelp" in html

    def test_hits_are_highlighted_in_a_colour_that_is_not_a_diff_colour(self, tmp_path):
        html = _render(_make_suite(), tmp_path)

        assert "highlightDiffMatches" in html
        assert "clearDiffHighlights" in html
        # Accent, not rose/emerald -- those already mean absent/inserted here.
        assert "mark.q-hit { background:rgba(var(--accent-rgb),0.30)" in html


def _panel_html(html, role):
    """Return the diff panel markup wrapping the given data-role span."""
    marker = 'data-role="%s"' % role
    start = html.rindex('<div class="diff-panel', 0, html.index(marker))
    return html[start:html.index("</div>", html.index(marker))]


class TestSeverityColorCoding:
    """Red must mark the side that is *wrong*, never merely "the source side".

    Regression guard: row-missing differences store the literal sentinels
    "present"/"missing" as values, so the old char-diff painted the word
    "present" red (deleted) and "missing" green (inserted) -- exactly backwards.
    """

    def test_missing_in_target_marks_target_panel_absent(self, tmp_path):
        mm = _make_mm("<row>", "present", "missing", mm_type="missing_in_target")
        html = _render(_make_suite([mm]), tmp_path)

        assert "diff-panel-absent" in _panel_html(html, "tgt-diff")
        assert "diff-panel-absent" not in _panel_html(html, "src-diff")

    def test_missing_in_source_marks_source_panel_absent(self, tmp_path):
        mm = _make_mm("<row>", "missing", "present", mm_type="missing_in_source")
        html = _render(_make_suite([mm]), tmp_path)

        assert "diff-panel-absent" in _panel_html(html, "src-diff")
        assert "diff-panel-absent" not in _panel_html(html, "tgt-diff")

    def test_presence_sentinels_render_as_markers_not_diffed_words(self, tmp_path):
        mm = _make_mm("<row>", "present", "missing", mm_type="missing_in_target")
        html = _render(_make_suite([mm]), tmp_path)

        assert "presence-absent" in _panel_html(html, "tgt-diff")
        assert "presence-present" in _panel_html(html, "src-diff")

    def test_value_diff_keeps_neutral_panels(self, tmp_path):
        mm = _make_mm("amount", "100.00", "100.01", mm_type="value_diff")
        html = _render(_make_suite([mm]), tmp_path)

        assert "diff-panel-absent" not in _panel_html(html, "src-diff")
        assert "diff-panel-absent" not in _panel_html(html, "tgt-diff")

    def test_missing_row_badge_outranks_value_diff_badge(self, tmp_path):
        missing = _make_mm("<row>", "present", "missing", mm_type="missing_in_target")
        drift = _make_mm("amount", "100.00", "100.01", mm_type="value_diff")
        html = _render(_make_suite([missing, drift]), tmp_path)

        assert '<span class="badge badge-fail">missing_in_target</span>' in html
        assert '<span class="badge badge-amber">value_diff</span>' in html

    def test_client_side_injection_shares_the_severity_rules(self, tmp_path):
        html = _render(_make_suite(), tmp_path)

        # The "Load all differences" path rebuilds rows in JS; it must classify
        # presence rows the same way the server-rendered rows are classified.
        assert "isPresenceType" in html
        assert "renderPresence" in html
        assert "badge-fail" in html.split("function mismatchTypeBadgeClass")[1][:400]


class TestNullValues:
    """A null value is an absence, and both the Web UI and the report mark it as
    one. The report renders rows twice -- once server-side, then again in the
    browser from the row's data attributes -- so the absence has to survive the
    round trip through an attribute, which can only hold strings."""

    def _row(self, html):
        import re
        return re.search(r"<tr data-mismatch.*?</tr>", html, re.S).group(0)

    def test_a_null_value_renders_as_the_null_marker(self, tmp_path):
        html = _render(_make_suite([_make_mm("amount", "7", None)]), tmp_path)

        assert '<span class="null-val">NULL</span>' in self._row(html)

    def test_the_null_survives_the_client_side_re_render(self, tmp_path):
        html = _render(_make_suite([_make_mm("amount", "7", None)]), tmp_path)

        # flagged on the row, because data-tgt="" cannot say which of "absent"
        # and "empty" it means...
        assert 'data-tgt-null="1"' in self._row(html)
        # ...and honoured when the browser re-renders the value panels.
        assert "tr.dataset.tgtNull ? null : tr.dataset.tgt" in html

    def test_a_present_value_is_not_flagged_as_null(self, tmp_path):
        row = self._row(_render(_make_suite([_make_mm("amount", "7", "8")]), tmp_path))

        assert "data-src-null" not in row
        assert "data-tgt-null" not in row

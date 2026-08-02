"""The History tab's inline mismatch table sits next to the "Download Full HTML
Report" button, so the two are read side by side.

It kept the pre-cf55a3b colour language: every source value red, every target
value green, and the row itself tinted by mismatch type -- which paints a
missing_in_source row green while the downloaded report paints the absent source
panel red. Same run, opposite reading. This pins the History table to the shared
severity renderers the Compare tab, the Differences Explorer and the report all use.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PARTIAL = REPO_ROOT / "frontend" / "partials" / "tab-history.html"
INDEX = REPO_ROOT / "frontend" / "index.html"
STYLES = REPO_ROOT / "frontend" / "styles.css"


@pytest.fixture(scope="module")
def partial() -> str:
    return PARTIAL.read_text(encoding="utf-8")


def _mismatch_table(text: str) -> str:
    """The inline per-result mismatch diff table, not the rest of the tab."""
    assert 'class="mismatch-diff-table"' in text
    return text.split('class="mismatch-diff-table"', 1)[1].split("</table>", 1)[0]


def test_values_render_through_the_shared_severity_renderers(partial):
    table = _mismatch_table(partial)

    assert 'x-html="renderSrc(m.source_value, m.target_value, m.mismatch_type)"' in table
    assert 'x-html="renderTgt(m.source_value, m.target_value, m.mismatch_type)"' in table


def test_a_missing_row_outranks_a_drifted_value_in_the_type_badge(partial):
    table = _mismatch_table(partial)

    assert "isPresenceType(m.mismatch_type) ? 'badge-rose' : 'badge-amber'" in table


def test_rows_are_not_tinted_by_which_side_a_value_came_from(partial):
    table = _mismatch_table(partial)

    for side_keyed in ("diff-missing-target", "diff-missing-source", "diff-value",
                       'class="diff-src"', 'class="diff-tgt"'):
        assert side_keyed not in table, f"{side_keyed} colours a row by side, not by severity"


def test_a_triaged_row_keeps_its_decision_tint(partial):
    """Accepted is a decision, not a side -- the report tints those rows too."""
    table = _mismatch_table(partial)

    assert "diff-accepted" in table


def test_no_stylesheet_rule_paints_a_value_by_its_side():
    styles = STYLES.read_text(encoding="utf-8")

    assert ".diff-src" not in styles
    assert ".diff-tgt" not in styles


def test_the_generated_page_carries_the_same_table(partial):
    """index.html is assembled from the partials by scripts/build-html.js; a stale
    build ships the old colours to every user."""
    assert _mismatch_table(partial) == _mismatch_table(INDEX.read_text(encoding="utf-8")), (
        "frontend/index.html is stale; run: node scripts/build-html.js"
    )

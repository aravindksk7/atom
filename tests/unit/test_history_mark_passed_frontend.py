"""Run History exposes "Mark passed & continue" for failed sequence steps."""
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_history_detail_lists_steps_with_mark_passed_button():
    markup = _read("frontend/partials/tab-history.html")
    assert 'data-testid="history-run-steps"' in markup
    assert 'x-for="s in selectedRun.steps"' in markup
    assert 'data-testid="mark-passed-btn"' in markup
    assert "canMarkStepPassed(selectedRun, s)" in markup
    assert "openMarkPassed(selectedRun.run_id, s)" in markup


def test_mark_passed_modal_requires_name_and_note():
    markup = _read("frontend/partials/tab-history.html")
    assert 'x-show="markPassedModal.show"' in markup
    assert 'data-testid="mark-passed-submit"' in markup
    assert ':disabled="!markPassedModal.note.trim() || !markPassedModal.releasedBy.trim() || markPassedModal.busy"' in markup
    assert "will not be re-run" in markup


def test_history_js_posts_to_mark_passed_endpoint_and_gates_on_failed_steps():
    js = _read("frontend/features/history.js")
    assert "markPassedModal:" in js
    assert "canMarkStepPassed(run, step)" in js
    assert "['FAILED', 'ERROR'].includes(step.status)" in js
    assert "/mark-passed`" in js
    assert "async submitMarkPassed()" in js


def test_built_index_includes_mark_passed_ui():
    assert 'data-testid="mark-passed-btn"' in _read("frontend/index.html")

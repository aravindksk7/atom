import re
import shutil
import subprocess
from pathlib import Path

import pytest

from api.services.run_timing import _JOB_FAMILIES

ROOT = Path(__file__).parents[2]
SPEC = Path(__file__).with_suffix(".js")

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_run_timing_frontend_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

def test_run_timing_slice_is_registered():
    assert "ETL_FEATURE_RUN_TIMING()" in (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    template = (ROOT / "frontend" / "index.template.html").read_text(encoding="utf-8")
    assert '<script src="features/run-timing.js"></script>' in template


# --- the markup and the styles have to stay together --------------------------
# CSS loss doesn't fail anything by itself: fills go transparent, Gantt bars go
# 0px high, and the page still loads.

STYLES = ROOT / "frontend" / "styles.css"
GANTT = ROOT / "frontend" / "partials" / "shared" / "run-gantt.html"
MONITOR = ROOT / "frontend" / "partials" / "tab-monitor.html"
RUN_TIMING_JS = ROOT / "frontend" / "features" / "run-timing.js"

# Wrapper that only groups the rows; the plan's CSS gives it no rule of its own.
UNSTYLED_HOOKS = {"rt-gantt"}

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _css_classes() -> set[str]:
    return set(re.findall(r"\.(rt-[a-z0-9-]+)", STYLES.read_text(encoding="utf-8")))


def _literal_classes(text: str) -> set[str]:
    # A dynamic prefix like 'rt-s-' or `rt-f-${x}` ends in a dash, so it isn't a class.
    return set(re.findall(r"(?<![\w-])rt-[a-z0-9]+(?:-[a-z0-9]+)*(?![\w$-])", text))


def _dynamic_classes() -> set[str]:
    """Class names run-timing.js builds from its tables."""
    source = RUN_TIMING_JS.read_text(encoding="utf-8")
    statuses = re.search(r"STATUS_CLASSES = \{(.*?)\}", source, re.S).group(1)
    legend = re.search(r"STATUS_LEGEND = \[(.*?)\n  \];", source, re.S).group(1)
    families = re.search(r"FAMILY_LEGEND = \[(.*?)\n  \];", source, re.S).group(1)
    names = {f"rt-s-{value}" for value in re.findall(r":\s*'(\w+)'", statuses)}
    names |= {f"rt-s-{value}" for value in re.findall(r"\['(\w+)',", legend)}
    names |= {f"rt-f-{value}" for value in re.findall(r"\['(\w+)',", families)}
    assert len(names) >= 12, f"the class tables moved, so this test no longer reads them: {sorted(names)}"
    return names


@pytest.mark.parametrize("path", [GANTT, MONITOR, RUN_TIMING_JS], ids=lambda p: p.name)
def test_every_literal_rt_class_has_a_rule(path):
    used = _literal_classes(path.read_text(encoding="utf-8"))
    assert used, f"no rt-* classes found in {path.name}"
    assert not used - _css_classes() - UNSTYLED_HOOKS, f"{path.name} uses classes with no rule"


def test_every_dynamic_rt_class_has_a_rule():
    assert not _dynamic_classes() - _css_classes()


def test_every_job_family_the_server_can_send_has_a_color():
    families = set(_JOB_FAMILIES.values()) | {"other"}
    assert not {f"rt-f-{family}" for family in families} - _css_classes()


@pytest.mark.parametrize("selector", [
    ".rt-gantt-track", ".rt-gantt-bar", ".rt-gantt-hold", ".rt-legend", ".rt-tab.is-on",
])
def test_rules_the_timeline_depends_on_exist(selector):
    assert selector in STYLES.read_text(encoding="utf-8")


def test_gantt_ticks_are_spread_across_the_axis():
    rule = re.search(r"\.rt-gantt-ticks\s*\{([^}]*)\}", STYLES.read_text(encoding="utf-8")).group(1)
    assert "display: flex" in rule
    assert "space-between" in rule


def test_phone_width_stacks_the_rows():
    styles = STYLES.read_text(encoding="utf-8")
    block = re.search(r"@media \(max-width: 640px\)\s*\{\s*\.rt-progress-row[^}]*\}", styles)
    assert block, "the 640px rule for run timing rows is gone"
    assert "grid-template-columns: 1fr" in block.group(0)


def test_gantt_partial_keeps_its_legend():
    gantt = GANTT.read_text(encoding="utf-8")
    assert 'data-testid="run-gantt-legend"' in gantt
    assert "ganttLegend()" in gantt


def test_gantt_row_shows_status_with_a_real_middle_dot():
    raw = GANTT.read_bytes()
    assert b"\xc3\x82\xc2\xb7" not in raw, "the separator is mojibake"
    # Without the parentheses the status only shows when the job type is missing.
    assert "(row.step.job_type || 'unknown') + ' · ' +" in raw.decode("utf-8")


def test_monitor_tablist_is_labelled():
    assert re.search(r'role="tablist"[^>]*aria-label=', MONITOR.read_text(encoding="utf-8"))


def test_release_dialog_renders_on_the_tab_that_opens_it():
    # Each tab partial is an x-if, so a dialog in another tab's partial isn't in the
    # page and the Release button appears to do nothing.
    assert 'x-ref="stepReleaseDialog"' in MONITOR.read_text(encoding="utf-8")
    assert "openStepRelease" in MONITOR.read_text(encoding="utf-8")


def test_blocked_has_a_badge_color():
    assert re.search(r"BLOCKED:\s*'badge-amber'", (ROOT / "frontend" / "app.js").read_text(encoding="utf-8"))


# --- the generated page -------------------------------------------------------

def _build_tree(tmp_path: Path) -> Path:
    """Just enough of the repo for build-html.js, which works relative to itself."""
    for relative in (
        "scripts/build-html.js",
        "etl_framework/reporting/templates/_diff_search.js",
        "frontend/index.template.html",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    (tmp_path / "frontend" / "features").mkdir()
    shutil.copytree(ROOT / "frontend" / "partials", tmp_path / "frontend" / "partials")
    return tmp_path


def _run_build(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("node"), str(tree / "scripts" / "build-html.js")],
        capture_output=True,
        text=True,
    )


def _lf(data: bytes) -> bytes:
    # The repo checks out CRLF on Windows, and partials can differ from the template.
    return data.replace(b"\r\n", b"\n")


@needs_node
def test_generated_index_matches_a_fresh_build(tmp_path):
    tree = _build_tree(tmp_path)
    proc = _run_build(tree)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fresh = _lf((tree / "frontend" / "index.html").read_bytes())
    assert _lf((ROOT / "frontend" / "index.html").read_bytes()) == fresh, (
        "frontend/index.html is stale; run: node scripts/build-html.js"
    )


@needs_node
@pytest.mark.parametrize("marker", [
    "<!-- SHARED: run-gantt.html -->",              # path not under shared/
    "<!-- SHARED: shared/run-gantt.html-->",        # no space before -->
    "<!-- SHARED: shared/run-gantt.html -->   ",    # trailing spaces
    "<!--SHARED: shared/run-gantt.html -->",        # no space after <!--
    "<!-- SHARED: shared/run gantt.html -->",       # not a file name
    "text <!-- SHARED: shared/run-gantt.html -->",  # not on its own line
])
def test_malformed_shared_marker_fails_the_build(tmp_path, marker):
    tree = _build_tree(tmp_path)
    partial = tree / "frontend" / "partials" / "tab-monitor.html"
    text = partial.read_text(encoding="utf-8")
    good = "<!-- SHARED: shared/run-gantt.html -->"
    assert good in text
    partial.write_text(text.replace(good, marker), encoding="utf-8")
    proc = _run_build(tree)
    assert proc.returncode != 0, f"{marker!r} passed silently"
    assert "SHARED" in proc.stderr
    assert not (tree / "frontend" / "index.html").exists(), "a failed build must not write the page"


@needs_node
def test_shared_partial_cannot_nest_another(tmp_path):
    tree = _build_tree(tmp_path)
    gantt = tree / "frontend" / "partials" / "shared" / "run-gantt.html"
    gantt.write_text("<!-- SHARED: shared/run-gantt.html -->\n", encoding="utf-8")
    assert _run_build(tree).returncode != 0


@needs_node
def test_built_page_includes_the_shared_gantt_in_monitor_and_history(tmp_path):
    tree = _build_tree(tmp_path)
    proc = _run_build(tree)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    html = (tree / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "<!-- SHARED:" not in html
    assert html.count('data-testid="run-gantt"') == 2


# --- History and Sequences ----------------------------------------------------

def test_sequence_detail_has_recent_runs():
    markup = (ROOT / "frontend" / "partials" / "tab-sequences.html").read_text(encoding="utf-8")
    assert 'data-testid="sequence-recent-runs"' in markup
    script = (ROOT / "frontend" / "features" / "sequences.js").read_text(encoding="utf-8")
    assert "/runs?limit=20" in script


def test_history_detail_shows_elapsed_and_timeline():
    markup = (ROOT / "frontend" / "partials" / "tab-history.html").read_text(encoding="utf-8")
    assert 'data-testid="history-run-elapsed"' in markup
    assert 'data-testid="history-timeline"' in markup
    assert "<!-- SHARED: shared/run-gantt.html -->" in markup


def test_history_can_filter_blocked_runs():
    markup = (ROOT / "frontend" / "partials" / "tab-history.html").read_text(encoding="utf-8")
    assert '<option value="BLOCKED">BLOCKED</option>' in markup

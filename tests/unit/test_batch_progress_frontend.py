import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]

SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_batch_progress_frontend_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_sequence_tab_uses_persistent_loop_panel():
    markup = (ROOT / "frontend" / "partials" / "tab-sequences.html").read_text()
    assert 'x-init="rehydrateBatchProgress()"' in markup
    assert "batchProgress || batchProgressError || recentBatchIds.length" in markup
    assert 'data-testid="recent-batches-picker"' in markup
    assert 'data-testid="batch-loop-list"' in markup
    assert "toggleBatchRunSteps(loop.run_id)" in markup
    assert "loop.started_at ? 'Started ' + fmtDate(loop.started_at) : ''" in markup
    assert "loop.completed_at ? 'Completed ' + fmtDate(loop.completed_at) : ''" in markup

import shutil
import subprocess
from pathlib import Path
import pytest

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
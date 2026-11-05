import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = Path(__file__).with_suffix(".mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_demo_timeline():
    proc = subprocess.run(
        [shutil.which("node"), "--experimental-strip-types", "--no-warnings", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

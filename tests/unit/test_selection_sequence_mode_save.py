import shutil
import subprocess
from pathlib import Path

import pytest


SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_selection_sequence_mode_save_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

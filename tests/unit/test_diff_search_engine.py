"""Runs the shared search engine's behaviour suite (test_diff_search_engine.js).

The engine is JavaScript shared by the HTML report and the live Compare tab, so
its semantics are pinned in JS and driven from pytest to keep one test command.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_diff_search_engine_semantics():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

"""Auto-use isolated PDF env directories for all bo_layout unit tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auto_pdf_env(pdf_env):
    yield

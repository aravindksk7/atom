"""The mismatch search engine has one canonical source and one generated copy.

The downloadable HTML report inlines it (the report must stay self-contained);
the live Compare tab loads the generated copy as a script. If the two drift, the
same query silently returns different rows on each surface -- so guard the copy
here rather than trusting anyone to remember `node scripts/build-html.js`.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = REPO_ROOT / "etl_framework" / "reporting" / "templates" / "_diff_search.js"
GENERATED = REPO_ROOT / "frontend" / "features" / "diff-search.js"


def test_canonical_search_engine_exists():
    assert CANONICAL.is_file(), f"missing canonical search engine at {CANONICAL}"


def test_generated_frontend_copy_is_in_sync():
    if not GENERATED.is_file():
        pytest.fail(f"missing generated copy at {GENERATED}; run: node scripts/build-html.js")

    generated = GENERATED.read_text(encoding="utf-8")
    assert generated.startswith("// GENERATED FILE -- do not edit."), (
        "generated copy lost its banner; run: node scripts/build-html.js"
    )
    body = generated.split("// Regenerate with: node scripts/build-html.js\n", 1)[1]
    assert body == CANONICAL.read_text(encoding="utf-8"), (
        "frontend/features/diff-search.js is stale; run: node scripts/build-html.js"
    )


def test_generated_copy_is_loaded_by_the_page():
    index = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'src="features/diff-search.js"' in index
    # Must load before the code that calls into it.
    assert index.index('src="features/diff-search.js"') < index.index('src="features/compare.js"')

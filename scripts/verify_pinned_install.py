#!/usr/bin/env python3
"""Confirm every install path resolves only to versions pinned in constraints.txt.

Resolves ``requirements.txt`` and the package with all extras (both with the
constraints applied) using ``uv pip compile`` for each supported Python, then
checks that every resolved package is pinned and matches its pin. Runs in a
few seconds without installing anything; needs ``uv`` on PATH.

    python scripts/verify_pinned_install.py               # Python 3.11, 3.12, 3.14
    python scripts/verify_pinned_install.py --python 3.12
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_versions(text: str) -> dict[str, set[str]]:
    pins: dict[str, set[str]] = {}
    for line in text.splitlines():
        match = _PIN.match(line)
        if match:
            pins.setdefault(_norm(match.group(1)), set()).add(match.group(2))
    return pins


def resolve(python: str, inputs: list[str], extra_args: list[str]) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "resolved.txt"
        cmd = ["uv", "pip", "compile", *inputs, "-c", str(ROOT / "constraints.txt"),
               "--python-version", python, "--no-header", "--no-annotate", "--quiet",
               "-o", str(out), *extra_args]
        subprocess.run(cmd, cwd=ROOT, check=True)
        return {_norm(n): v for n, v in (m.groups() for m in map(_PIN.match, out.read_text().splitlines()) if m)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--python", action="append", help="Python version(s) to check")
    args = parser.parse_args(argv)
    pythons = args.python or ["3.11", "3.12", "3.14"]

    pins = pinned_versions((ROOT / "constraints.txt").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        extra = Path(tmp) / "extra.in"
        extra.write_text("typer\npytest-xdist\n", encoding="utf-8")
        paths = {
            "pip install -r requirements.txt": (["requirements.txt", str(extra)], []),
            "pip install -c constraints.txt .[all extras]": (["pyproject.toml"], ["--all-extras"]),
        }
        problems = []
        for python in pythons:
            for label, (inputs, extra_args) in paths.items():
                resolved = resolve(python, inputs, extra_args)
                for name, version in sorted(resolved.items()):
                    if name not in pins:
                        problems.append(f"py{python} {label}: {name}=={version} is not pinned")
                    elif version not in pins[name]:
                        problems.append(f"py{python} {label}: {name}=={version} not in pins {sorted(pins[name])}")
                print(f"py{python}  {label}: {len(resolved)} packages, "
                      f"fastapi={resolved.get('fastapi')}, annotated-doc={resolved.get('annotated-doc')}")
    if problems:
        print("\n".join(problems))
        return 1
    print("every resolved package matches its pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())

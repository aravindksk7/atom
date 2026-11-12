#!/usr/bin/env python3
"""Fail when a pinned dependency is too new for the on-prem package mirror.

The on-prem Nexus only serves a release once it is older than a quarantine
period (7 days). A build that pins a younger release fails there even though
it works everywhere else. This reads the ``name==version`` pins in
constraints.txt, asks the package index when each version was first uploaded,
and lists every pin younger than ``--min-age-days``.

    python scripts/check_dependency_age.py                    # constraints.txt, 7 days
    python scripts/check_dependency_age.py --min-age-days 8 path/to/constraints.txt
    python scripts/check_dependency_age.py --json-url "https://nexus.example/repository/pypi/pypi/{name}/{version}/json"

Exit codes: 0 all pins old enough, 1 at least one pin too new, 2 could not
check (network or index error).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def parse_pins(text: str) -> list[tuple[str, str]]:
    """``(name, version)`` for each exact pin; environment markers are ignored,
    and a package pinned per Python version yields one entry per version."""
    pins: dict[tuple[str, str], None] = {}
    for line in text.splitlines():
        match = _PIN.match(line)
        if match:
            pins[(match.group(1).lower(), match.group(2))] = None
    return list(pins)


def first_upload(payload: dict) -> datetime | None:
    """Earliest file upload time in a PyPI-style ``/{name}/{version}/json`` reply."""
    times = [
        f.get("upload_time_iso_8601") or f.get("upload_time")
        for f in payload.get("urls") or []
    ]
    parsed = []
    for value in times:
        if not value:
            continue
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
    return min(parsed) if parsed else None


def too_new(
    pins: list[tuple[str, str]],
    uploaded: dict[tuple[str, str], datetime | None],
    now: datetime,
    min_age: timedelta,
) -> list[tuple[str, str, datetime]]:
    """Pins first uploaded less than ``min_age`` before ``now``."""
    cutoff = now - min_age
    return [
        (name, version, uploaded[(name, version)])  # type: ignore[misc]
        for name, version in pins
        if uploaded.get((name, version)) is not None and uploaded[(name, version)] > cutoff
    ]


def _fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "dependency-age-check"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("constraints", nargs="?", default="constraints.txt")
    parser.add_argument("--min-age-days", type=float, default=7.0)
    parser.add_argument("--json-url", default=DEFAULT_JSON_URL,
                        help="PyPI-style JSON endpoint with {name} and {version} placeholders")
    args = parser.parse_args(argv)

    pins = parse_pins(Path(args.constraints).read_text(encoding="utf-8"))
    if not pins:
        print(f"no name==version pins found in {args.constraints}", file=sys.stderr)
        return 2

    def lookup(pin: tuple[str, str]) -> tuple[tuple[str, str], datetime | None]:
        name, version = pin
        return pin, first_upload(_fetch(args.json_url.format(name=name, version=version)))

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            uploaded = dict(pool.map(lookup, pins))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"could not query the package index: {exc}", file=sys.stderr)
        return 2

    unknown = sorted(f"{n}=={v}" for (n, v), when in uploaded.items() if when is None)
    if unknown:
        print("no upload time reported for: " + ", ".join(unknown), file=sys.stderr)

    now = datetime.now(timezone.utc)
    offenders = too_new(pins, uploaded, now, timedelta(days=args.min_age_days))
    if offenders:
        print(f"{len(offenders)} pin(s) younger than {args.min_age_days:g} days "
              "(the on-prem mirror will block them):")
        for name, version, when in sorted(offenders):
            age = (now - when).total_seconds() / 86400
            print(f"  {name}=={version}  uploaded {when:%Y-%m-%d %H:%M} UTC ({age:.1f} days ago)")
        return 1
    print(f"all {len(pins)} pins are at least {args.min_age_days:g} days old")
    return 0


if __name__ == "__main__":
    sys.exit(main())

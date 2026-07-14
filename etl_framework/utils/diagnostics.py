from __future__ import annotations

import importlib.metadata
import platform
import sys
from pathlib import Path
from typing import Iterable, Any


def collect_environment_info() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
    }


def collect_package_versions(packages: Iterable[str] | None = None) -> dict[str, str]:
    names = list(packages or ["pandas", "sqlalchemy", "fastapi", "polars", "duckdb"])
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def collect_database_info(engine: Any) -> dict[str, str]:
    return {
        "dialect": getattr(engine.dialect, "name", "unknown"),
        "driver": getattr(engine.dialect, "driver", "unknown"),
    }


def collect_recent_logs(log_dir: str | Path = "logs", limit: int = 200) -> list[str]:
    path = Path(log_dir)
    if not path.exists():
        return []
    files = sorted((p for p in path.glob("*.log") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    lines: list[str] = []
    for file_path in files[:3]:
        try:
            file_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        lines.extend(f"{file_path.name}: {line}" for line in file_lines[-limit:])
        if len(lines) >= limit:
            break
    return lines[-limit:]


def build_support_bundle(engine: Any | None = None, log_dir: str | Path = "logs") -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "environment": collect_environment_info(),
        "packages": collect_package_versions(),
        "recent_logs": collect_recent_logs(log_dir),
    }
    if engine is not None:
        bundle["database"] = collect_database_info(engine)
    return bundle

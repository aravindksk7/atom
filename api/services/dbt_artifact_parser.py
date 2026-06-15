from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DbtNodeResult:
    unique_id: str
    name: str
    status: str
    execution_time: float = 0.0
    message: str = ""


@dataclass(frozen=True)
class DbtArtifactSummary:
    generated_at: str | None
    results: list[DbtNodeResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status in {"pass", "success", "warn"})

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status in {"fail", "error"})


class DbtArtifactParser:
    def parse(
        self,
        run_results_path: str | Path,
        manifest_path: str | Path | None = None,
    ) -> DbtArtifactSummary:
        run_results = self._read_json(run_results_path)
        manifest = self._read_json(manifest_path) if manifest_path else {}
        nodes = manifest.get("nodes", {}) if isinstance(manifest, dict) else {}

        results: list[DbtNodeResult] = []
        for raw in run_results.get("results", []):
            unique_id = raw.get("unique_id", "")
            node = nodes.get(unique_id, {}) if isinstance(nodes, dict) else {}
            name = node.get("name") or unique_id.rsplit(".", 1)[-1] or unique_id
            results.append(
                DbtNodeResult(
                    unique_id=unique_id,
                    name=name,
                    status=str(raw.get("status", "unknown")).lower(),
                    execution_time=float(raw.get("execution_time") or 0),
                    message=self._message(raw),
                )
            )

        metadata = run_results.get("metadata", {})
        return DbtArtifactSummary(
            generated_at=metadata.get("generated_at") if isinstance(metadata, dict) else None,
            results=results,
        )

    def _read_json(self, path: str | Path | None) -> dict[str, Any]:
        if not path:
            return {}
        with Path(path).expanduser().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}

    def _message(self, raw: dict[str, Any]) -> str:
        message = raw.get("message") or raw.get("failures")
        if message is None:
            return ""
        return str(message)

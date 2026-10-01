from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .io import read_json, resolve_path


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "benchmarks.json"


@dataclass(frozen=True)
class BenchmarkConfig:
    name: str
    values: Dict[str, Any]
    config_dir: Path

    @property
    def enabled(self) -> bool:
        return bool(self.values.get("enabled", True))

    @property
    def adapter(self) -> str:
        return str(self.values["adapter"])

    @property
    def output_format(self) -> str:
        return str(self.values.get("output_format", "html"))

    @property
    def evaluator(self) -> Dict[str, Any]:
        value = self.values.get("evaluator", {"type": "ui2code_vlm"})
        if isinstance(value, str):
            return {"type": value}
        return dict(value)

    def path(self, key: str, required: bool = True) -> Path | None:
        value = self.values.get(key)
        if value in (None, ""):
            if required:
                raise KeyError(f"Benchmark {self.name!r} has no {key!r}")
            return None
        return resolve_path(str(value), self.config_dir)


class ProjectConfig:
    def __init__(self, path: str | Path | None = None):
        selected = Path(
            path
            or os.environ.get("MULTIMODALCODE_CONFIG", "")
            or DEFAULT_CONFIG
        ).resolve()
        payload = read_json(selected)
        if not isinstance(payload, dict) or not isinstance(payload.get("benchmarks"), dict):
            raise ValueError(f"Invalid benchmark config: {selected}")
        self.path = selected
        self.root = selected.parent
        self.raw = payload

    def names(self, include_disabled: bool = True) -> List[str]:
        result = []
        for name, values in self.raw["benchmarks"].items():
            if include_disabled or values.get("enabled", True):
                result.append(name)
        return sorted(result)

    def benchmark(self, name: str) -> BenchmarkConfig:
        try:
            values = self.raw["benchmarks"][name]
        except KeyError as exc:
            known = ", ".join(self.names())
            raise KeyError(f"Unknown benchmark {name!r}. Known: {known}") from exc
        return BenchmarkConfig(name=name, values=dict(values), config_dir=self.root)


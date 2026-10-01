from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class BenchmarkItem:
    benchmark: str
    item_id: str
    prompt: str
    image_paths: List[str]
    reference_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    image_paths: List[str]
    max_tokens: int
    temperature: float
    system_prompt: Optional[str] = None
    seed: Optional[int] = None


@dataclass
class Prediction:
    benchmark: str
    item_id: str
    sample_index: int
    prompt: str
    image_paths: List[str]
    output: str
    status: str
    latency_seconds: float
    error: Optional[str] = None
    raw_path: Optional[str] = None
    html_path: Optional[str] = None
    site_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.item_id}::{self.sample_index}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

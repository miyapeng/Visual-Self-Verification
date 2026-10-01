"""Frozen scaffold choices for official and controlled agent runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
VENDORED_MINI_ROOT = PROJECT_ROOT / "scaffolds" / "mini_swe_agent"
VENDORED_OPENHANDS_ROOT = PROJECT_ROOT / "scaffolds" / "openhands"


@dataclass(frozen=True)
class ScaffoldSpec:
    benchmark: str
    scaffold: str
    execution: str
    rationale: str
    source_root: Path | None = None
    source_revision: str | None = None
    runtime_image: str | None = None
    runtime_digest: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["source_root"] = str(self.source_root) if self.source_root else None
        return data


_VISION_IMAGE = PROJECT_ROOT / "data" / "vision2web" / "image.json"


def _vision_image_value(key: str) -> str | None:
    if not _VISION_IMAGE.is_file():
        return None
    import json

    return json.loads(_VISION_IMAGE.read_text(encoding="utf-8")).get(key)


SCAFFOLDS: dict[str, ScaffoldSpec] = {
    "swe-mm": ScaffoldSpec(
        benchmark="swe-mm",
        scaffold="mini-swe-agent",
        execution="clusterx-case-container",
        rationale="Official SWE-style repository loop with a minimal Bash action surface.",
        source_root=VENDORED_MINI_ROOT,
        source_revision="a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
    ),
    "design2code": ScaffoldSpec(
        benchmark="design2code",
        scaffold="mini-swe-agent",
        execution="local-workspace",
        rationale="The task needs iterative file editing and rendering, not a full browser-control platform.",
        source_root=VENDORED_MINI_ROOT,
        source_revision="a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
    ),
    "chartmimic": ScaffoldSpec(
        benchmark="chartmimic",
        scaffold="mini-swe-agent",
        execution="local-workspace",
        rationale="A Bash loop is sufficient for editing, executing, and visually inspecting Matplotlib code.",
        source_root=VENDORED_MINI_ROOT,
        source_revision="a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
    ),
    "vision2web": ScaffoldSpec(
        benchmark="vision2web",
        scaffold="openhands-headless-cli",
        execution="clusterx-task-container",
        rationale="Preserves the benchmark's official OpenHands path without nested Docker.",
        source_root=VENDORED_OPENHANDS_ROOT,
        source_revision=(
            "openhands-cli==1.16.0;openhands-sdk==1.21.0;"
            "openhands-tools==1.21.0;openhands-workspace==1.11.1;"
            "openhands-agent-server==1.42.1"
        ),
        runtime_image=_vision_image_value("immutable_image") or _vision_image_value("image"),
        runtime_digest=_vision_image_value("target_digest"),
    ),
}


# Vision2Web officially ships more than one inference scaffold.  ``SCAFFOLDS``
# keeps OpenHands as the benchmark default for backward compatibility, while
# this alternative records the equally official Claude Code path used by the
# direct-container adapter.
VISION2WEB_CLAUDE_CODE = ScaffoldSpec(
    benchmark="vision2web",
    scaffold="claude-code-cli",
    execution="clusterx-task-container",
    rationale=(
        "Preserves Vision2Web's released Claude Code command and environment; "
        "ClusterX replaces only the outer docker exec transport."
    ),
    source_root=PROJECT_ROOT / "evaluate" / "vision2web" / "upstream",
    source_revision="577f9397b3db8fc6d828adde254a830caa65d515",
    runtime_image=_vision_image_value("immutable_image") or _vision_image_value("image"),
    runtime_digest=_vision_image_value("target_digest"),
)


ALIASES = {
    "swe_mm": "swe-mm",
    "swemm": "swe-mm",
    "vision2web": "vision2web",
    "design2code": "design2code",
    "chartmimic": "chartmimic",
}


def canonical_benchmark(name: str) -> str:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key not in SCAFFOLDS:
        raise KeyError(f"Unknown agent benchmark {name!r}; choose from {', '.join(SCAFFOLDS)}")
    return key

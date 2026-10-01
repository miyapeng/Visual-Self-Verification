"""Agent-visible case loading and workspace preparation."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .registry import PROJECT_ROOT, SCAFFOLDS, canonical_benchmark


@dataclass(frozen=True)
class AgentCase:
    benchmark: str
    case_id: str
    task_type: str
    prompt: str
    image_paths: tuple[Path, ...] = ()
    source_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def scaffold(self) -> str:
        return SCAFFOLDS[self.benchmark].scaffold

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_paths"] = [str(path) for path in self.image_paths]
        data["source_dir"] = str(self.source_dir) if self.source_dir else None
        data["scaffold"] = self.scaffold
        return data


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def list_case_ids(benchmark: str) -> list[str]:
    benchmark = canonical_benchmark(benchmark)
    if benchmark == "swe-mm":
        path = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "agent_visible" / "instances.jsonl"
        return [row["instance_id"] for row in _jsonl(path)]
    if benchmark == "design2code":
        path = PROJECT_ROOT / "data" / "design2code" / "data.jsonl"
        return [str(row["id"]) for row in _jsonl(path)]
    if benchmark == "chartmimic":
        root = PROJECT_ROOT / "data" / "chartmimic" / "agent_visible"
        result: list[str] = []
        for split in ("direct_600", "customized_600"):
            result.extend(f"{split}/{row['idx']}" for row in _jsonl(root / f"{split}.jsonl"))
        return result
    root = PROJECT_ROOT / "data" / "vision2web" / "extracted"
    result = []
    for task_type in ("webpage", "frontend", "website"):
        task_root = root / task_type
        if task_root.is_dir():
            result.extend(
                f"{task_type}/{path.name}"
                for path in sorted(task_root.iterdir())
                if path.is_dir()
                and not path.name.startswith(".")
                and (path / "workflow.json").is_file()
                and (path / "prototypes").is_dir()
            )
    return result


def load_case(benchmark: str, case_id: str) -> AgentCase:
    benchmark = canonical_benchmark(benchmark)
    if benchmark == "swe-mm":
        return _load_swe(case_id)
    if benchmark == "design2code":
        return _load_design2code(case_id)
    if benchmark == "chartmimic":
        return _load_chartmimic(case_id)
    return _load_vision2web(case_id)


def _only_row(path: Path, key: str, value: str) -> dict[str, Any]:
    rows = [row for row in _jsonl(path) if str(row[key]) == value]
    if len(rows) != 1:
        raise KeyError(f"Expected exactly one {key}={value!r} in {path}, found {len(rows)}")
    return rows[0]


def _load_swe(case_id: str) -> AgentCase:
    root = PROJECT_ROOT / "data" / "swe_mm" / "dev"
    row = _only_row(root / "agent_visible" / "instances.jsonl", "instance_id", case_id)
    images = tuple(root / item["local_path"] for item in row.get("images", []))
    return AgentCase(
        benchmark="swe-mm",
        case_id=case_id,
        task_type="repository-repair",
        prompt=row["problem_statement"],
        image_paths=images,
        metadata={key: row[key] for key in ("repo", "version", "base_commit")},
    )


def _load_design2code(case_id: str) -> AgentCase:
    root = PROJECT_ROOT / "data" / "design2code"
    row = _only_row(root / "data.jsonl", "id", case_id)
    # Deliberately do not put `reference` in metadata: it is evaluator-private oracle code.
    return AgentCase(
        benchmark="design2code",
        case_id=case_id,
        task_type="ui-to-code",
        prompt=row["prompt"],
        image_paths=(root / row["image_path"],),
        metadata={"category": row.get("category"), "image_path": row["image_path"]},
    )


def _load_chartmimic(case_id: str) -> AgentCase:
    try:
        split, idx = case_id.split("/", 1)
    except ValueError as exc:
        raise KeyError("ChartMimic case IDs must look like direct_600/line_1") from exc
    if split not in {"direct_600", "customized_600"}:
        raise KeyError("Agent runs are frozen to direct_600 and customized_600")
    root = PROJECT_ROOT / "data" / "chartmimic"
    row = _only_row(root / "agent_visible" / f"{split}.jsonl", "idx", idx)
    reference_split = "direct_600"
    prompt = row.get("instruction") or (
        "Reproduce the supplied chart as closely as possible with an executable Python Matplotlib program. "
        "Infer the plotted data from the image."
    )
    return AgentCase(
        benchmark="chartmimic",
        case_id=case_id,
        task_type="customized-mimic" if split.startswith("customized") else "direct-mimic",
        prompt=prompt,
        image_paths=(root / "extracted" / reference_split / f"{idx}.png",),
        metadata={"split": split, "idx": idx, "width": row["width"], "height": row["height"]},
    )


def _load_vision2web(case_id: str) -> AgentCase:
    try:
        task_type, name = case_id.split("/", 1)
    except ValueError as exc:
        raise KeyError("Vision2Web case IDs must look like frontend/afl") from exc
    if task_type not in {"webpage", "frontend", "website"} or not name or "/" in name:
        raise KeyError(f"Invalid Vision2Web case ID: {case_id}")
    root = PROJECT_ROOT / "data" / "vision2web"
    source = root / "extracted" / task_type / name
    if not (source / "workflow.json").is_file() or not (source / "prototypes").is_dir():
        raise KeyError(f"Vision2Web case not found: {case_id}")
    prompt_path = root / "agent_visible" / "prompts" / f"{task_type}.md"
    images = tuple(sorted((source / "prototypes").glob("*")))
    prompt = prompt_path.read_text(encoding="utf-8")
    # The frozen Markdown carrier has one POSIX file-ending newline, while the
    # released Python prompt constants do not. Remove exactly that carrier byte
    # so the string passed to OpenHands is byte-identical to upstream.
    if prompt.endswith("\n"):
        prompt = prompt[:-1]
    return AgentCase(
        benchmark="vision2web",
        case_id=case_id,
        task_type=task_type,
        prompt=prompt,
        image_paths=images,
        source_dir=source,
        # The workflow is required to validate that the frozen case is
        # complete, but it is evaluator-private and is intentionally absent
        # from every agent-facing or run-side case record.
        metadata={},
    )


def prepare_workspace(
    case: AgentCase,
    workspace: Path,
    marker_path: Path | None = None,
    *,
    scaffold_override: str | None = None,
) -> Path:
    """Create an agent workspace without copying evaluator-private artifacts."""
    workspace = workspace.resolve()
    if case.benchmark == "swe-mm":
        if not (workspace / ".git").exists():
            raise RuntimeError(f"SWE-MM must run inside the case image's repository workspace: {workspace}")
        return workspace

    workspace.mkdir(parents=True, exist_ok=True)
    marker = marker_path.resolve() if marker_path else workspace / ".mmcode_case.json"
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved.get("benchmark") != case.benchmark or saved.get("case_id") != case.case_id:
            raise RuntimeError(f"Workspace already belongs to another case: {workspace}")
        # Vision2Web's bookkeeping is persisted under /data, but /workspace
        # belongs to a fresh ClusterX container on every retry. A matching
        # persistent marker therefore cannot by itself prove that the task
        # inputs are still staged in this container.
        if case.benchmark != "vision2web" or (workspace / "prototypes").is_dir():
            return workspace
    if any(workspace.iterdir()):
        raise RuntimeError(f"Refusing to prepare a non-empty unmarked workspace: {workspace}")

    if case.benchmark == "vision2web":
        assert case.source_dir is not None
        shutil.copytree(case.source_dir / "prototypes", workspace / "prototypes")
        resources = case.source_dir / "resources"
        if resources.is_dir():
            shutil.copytree(resources, workspace / "resources")
        if case.task_type == "frontend":
            shutil.copy2(case.source_dir / "prompt.txt", workspace / "prompt.txt")
        if case.task_type == "website":
            shutil.copy2(case.source_dir / "prd.md", workspace / "prd.md")
    elif case.benchmark == "design2code":
        # The official direct-prompting protocol explicitly provides this
        # placeholder for every image region in the target screenshot.
        placeholder = PROJECT_ROOT / "evaluate" / "design2code" / "Design2Code" / "prompting" / "rick.jpg"
        if not placeholder.is_file():
            raise RuntimeError(f"Frozen Design2Code placeholder is missing: {placeholder}")
        shutil.copy2(placeholder, workspace / "rick.jpg")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker_record = case.to_dict()
    if case.benchmark == "vision2web":
        # This marker is outside /workspace, but keep it free of source-tree
        # paths anyway: ClusterX mounts the shared project filesystem and an
        # absolute dataset path would make accidental oracle discovery easier.
        marker_record = {
            "benchmark": case.benchmark,
            "case_id": case.case_id,
            "task_type": case.task_type,
            "scaffold": scaffold_override or case.scaffold,
        }
    elif scaffold_override:
        marker_record["scaffold"] = scaffold_override
    marker.write_text(
        json.dumps(marker_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return workspace

#!/usr/bin/env python3
"""Freeze code snapshots and public manifests for the Vision2Web pilot.

The model-facing manifest never contains workflow.json.  A separate private
file records only aggregate workflow structure for post-trajectory coverage
analysis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.vision2web_trace import copy_program


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    PROJECT_ROOT / "configs/vision2web/visual_self_verification_pilot_20.json"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_case(case_id: str) -> str:
    return case_id.replace("/", "__")


def public_materials(case_id: str) -> dict[str, Any]:
    case = load_case("vision2web", case_id)
    assert case.source_dir is not None
    task_file = None
    if case.task_type == "frontend":
        task_file = case.source_dir / "prompt.txt"
    elif case.task_type == "website":
        task_file = case.source_dir / "prd.md"
    return {
        "task_file": str(task_file) if task_file is not None else None,
        "prototype_files": [str(path) for path in case.image_paths],
        "resources_dir": str(case.source_dir / "resources")
        if (case.source_dir / "resources").is_dir()
        else None,
    }


def workflow_shape(case_id: str) -> dict[str, Any]:
    task_type, name = case_id.split("/", 1)
    workflow_path = (
        PROJECT_ROOT
        / "data/vision2web/extracted"
        / task_type
        / name
        / "workflow.json"
    )
    workflows = read_json(workflow_path)
    return {
        "case_id": case_id,
        "workflow_count": len(workflows),
        "objective_count": sum(len(row.get("content", [])) for row in workflows),
        "action_count": sum(
            len(item.get("actions", []))
            for row in workflows
            for item in row.get("content", [])
        ),
        "validation_count": sum(
            len(item.get("validations", []))
            for row in workflows
            for item in row.get("content", [])
        ),
        "dependency_count": sum(len(row.get("depends_on", [])) for row in workflows),
        "resolutions": sorted(
            {
                f"{row.get('resolution', {}).get('width')}x"
                f"{row.get('resolution', {}).get('height')}"
                for row in workflows
            }
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "data/vision2web/pilots/visual_self_verification_20",
    )
    args = parser.parse_args()
    config = read_json(args.config.resolve())
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty pilot: {output}")
    output.mkdir(parents=True, exist_ok=True)

    source_run = PROJECT_ROOT / config["verification_capacity_probe"]["source_run"]
    source_root = (
        source_run
        / "agents/litellm_proxy__Qwen3.5-9B/vision2web/official"
    )
    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    for level in ("Level 1", "Level 2", "Level 3"):
        for case_id in config["cases"][level]:
            case_safe = safe_case(case_id)
            result_path = source_root / case_safe / "result.json"
            result = read_json(result_path)
            if result.get("status") != "success":
                raise RuntimeError(f"Source generation is not successful: {case_id}")
            workspace = Path(result["workspace_artifact"]).resolve()
            if not (workspace / "start.sh").is_file():
                raise RuntimeError(f"Source snapshot has no start.sh: {case_id}")
            snapshot = output / "code_snapshots" / case_safe
            version = copy_program(
                workspace,
                snapshot,
                metadata={
                    "case_id": case_id,
                    "source_result": str(result_path),
                    "source_condition": "official",
                    "source_model": "Qwen3.5-9B",
                },
            )
            public_rows.append(
                {
                    "schema": "multimodalcode-vision2web-verification-probe-case-1",
                    "case_id": case_id,
                    "level": level,
                    "task_type": case_id.split("/", 1)[0],
                    "program_path": str(snapshot),
                    "program_sha256": version["program_sha256"],
                    "public_materials": public_materials(case_id),
                    "verification_prompt": str(
                        PROJECT_ROOT
                        / "configs/prompts/vision2web/fresh_context_verification_probe.txt"
                    ),
                    "workflow_visible_to_model": False,
                }
            )
            private_rows.append(workflow_shape(case_id))

    expected = config["selection_policy"]["total"]
    if len(public_rows) != expected or len({row["case_id"] for row in public_rows}) != expected:
        raise RuntimeError("Pilot selection count or uniqueness mismatch")
    (output / "cases.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in public_rows),
        encoding="utf-8",
    )
    private_dir = output / "post_trajectory_only"
    private_dir.mkdir()
    (private_dir / "workflow_structure.json").write_text(
        json.dumps(
            {
                "schema": "multimodalcode-vision2web-private-sampling-analysis-1",
                "model_visible": False,
                "purpose": "post-trajectory plan coverage analysis only",
                "cases": private_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Vision2Web visual self-verification pilot\n\n"
        "`cases.jsonl` and `code_snapshots/` are the frozen 20-case probe. "
        "The agent receives public task materials and code only. "
        "`post_trajectory_only/` must never be mounted into the agent workspace.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "cases": len(public_rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

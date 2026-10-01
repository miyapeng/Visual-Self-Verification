#!/usr/bin/env python3
"""Build leakage-safe Vision2Web same-policy pilot manifests.

Only public benchmark inputs and the generated program workspace are admitted.
In particular, this adapter never reads Vision2Web ``workflow.json`` files.
Historical InteractWeb helpers remain below solely to verify old frozen inputs;
the command-line path rejects InteractWeb cases under the active research scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SOURCE_SUFFIXES = {
    ".astro",
    ".cjs",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".py",
    ".scss",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}
IGNORED_PARTS = {
    ".cache",
    ".git",
    ".parcel-cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_files(root: Path) -> list[str]:
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if path.is_symlink() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            rows.append(relative.as_posix())
    return rows


def static_runtime(workspace: Path) -> dict[str, Any]:
    candidates = sorted(
        path
        for path in workspace.rglob("index.html")
        if not any(part in IGNORED_PARTS for part in path.relative_to(workspace).parts)
    )
    if not candidates:
        raise ValueError(f"No runnable index.html under {workspace}")
    root_index = workspace / "index.html"
    entry = root_index if root_index in candidates else candidates[0]
    relative_dir = entry.parent.relative_to(workspace).as_posix()
    directory = "." if relative_dir == "." else relative_dir
    return {
        "command": [
            "python3",
            "-m",
            "http.server",
            "{port}",
            "--bind",
            "127.0.0.1",
            "--directory",
            directory,
        ],
        "entry_path": "/",
        "startup_timeout": 20,
        "adapter": "mechanical_static_server",
        "entry_file": entry.relative_to(workspace).as_posix(),
    }


def vision_runtime(
    workspace: Path, dependency_cache_root: Path | None
) -> dict[str, Any]:
    package = workspace / "package.json"
    if not package.is_file():
        return static_runtime(workspace)
    value = json.loads(package.read_text(encoding="utf-8"))
    scripts = value.get("scripts") or {}
    if not isinstance(scripts, dict) or not scripts.get("dev"):
        return static_runtime(workspace)
    if dependency_cache_root is None:
        raise ValueError(
            f"Vite/npm Vision2Web program requires a frozen dependency cache: {workspace}"
        )
    package_sha256 = sha256(package)
    dependency_root = dependency_cache_root / package_sha256
    dependency_package = dependency_root / "package.json"
    dependency_lock = dependency_root / "package-lock.json"
    dependency_manifest = dependency_root / "node_modules.sha256"
    dependencies = dependency_root / "node_modules"
    if (
        not dependency_package.is_file()
        or not dependency_lock.is_file()
        or not dependency_manifest.is_file()
        or not dependencies.is_dir()
    ):
        raise FileNotFoundError(
            f"Frozen dependency cache is incomplete for {package_sha256}: {dependency_root}"
        )
    if sha256(dependency_package) != package_sha256:
        raise ValueError("Frozen dependency package.json does not match the generated program")
    return {
        "command": [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
        ],
        "entry_path": "/",
        "startup_timeout": 45,
        "adapter": "frozen_vite_dev_script",
        "dependency_path": str(dependencies.resolve()),
        "dependency_package_sha256": package_sha256,
        "dependency_lock_sha256": sha256(dependency_lock),
        "dependency_manifest_path": str(dependency_manifest.resolve()),
        "dependency_manifest_sha256": sha256(dependency_manifest),
    }


def vite_runtime(workspace: Path) -> dict[str, Any]:
    package = workspace / "package.json"
    if not package.is_file():
        raise ValueError(f"InteractWeb workspace has no package.json: {workspace}")
    value = json.loads(package.read_text(encoding="utf-8"))
    scripts = value.get("scripts") or {}
    if not isinstance(scripts, dict) or not scripts.get("dev"):
        raise ValueError(f"InteractWeb workspace has no npm dev script: {workspace}")
    if not (workspace / "node_modules").is_dir():
        raise ValueError(f"InteractWeb workspace has no frozen node_modules: {workspace}")
    return {
        "command": [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
        ],
        "entry_path": "/",
        "startup_timeout": 45,
        "adapter": "released_vite_dev_script",
    }


def _require_nonempty_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)


def _prototype_images(case_input: Path) -> list[Path]:
    prototype_dir = case_input / "prototypes"
    if not prototype_dir.is_dir():
        raise FileNotFoundError(prototype_dir)
    images = sorted(
        path
        for path in prototype_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".avif", ".jpeg", ".jpg", ".png", ".webp"}
    )
    if not images:
        raise ValueError(f"No public prototype images under {prototype_dir}")
    return images


def vision_case(
    case_id: str,
    *,
    generation_root: Path,
    data_root: Path,
    dependency_cache_root: Path | None = None,
) -> dict[str, Any]:
    if case_id.count("/") != 1:
        raise ValueError(f"Vision2Web case id must be task_type/name: {case_id}")
    task_type, name = case_id.split("/", 1)
    if task_type not in {"frontend", "website"}:
        raise ValueError(
            f"Pilot accepts Vision2Web Level 2/3 only, not {task_type!r}: {case_id}"
        )
    case_input = data_root / task_type / name
    public_task_path = case_input / ("prd.md" if task_type == "website" else "prompt.txt")
    if not public_task_path.is_file():
        raise FileNotFoundError(public_task_path)
    generated = generation_root / f"{task_type}__{name}"
    workspace = generated / "workspace"
    result_path = generated / "result.json"
    if not workspace.is_dir() or not result_path.is_file():
        raise FileNotFoundError(f"Frozen generation output is incomplete: {generated}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "success":
        raise ValueError(
            f"First complete Vision2Web program must come from a successful generation: {case_id}"
        )
    images = _prototype_images(case_input)
    task = public_task_path.read_text(encoding="utf-8")
    modified = source_files(workspace)
    if not modified:
        raise ValueError(f"Generated Vision2Web workspace has no source files: {workspace}")
    return {
        "case_id": case_id,
        "benchmark": "vision2web",
        "task": task,
        "program_path": str(workspace.resolve()),
        "task_image_paths": [str(path.resolve()) for path in images],
        "modified_files": modified,
        "web_runtime": vision_runtime(workspace, dependency_cache_root),
        "metadata": {
            "task_level": 2 if task_type == "frontend" else 3,
            "public_task_path": str(public_task_path.resolve()),
            "public_task_sha256": sha256(public_task_path),
            "public_prototype_count": len(images),
            "source_generation_result": str(result_path.resolve()),
            "source_generation_status": "success",
            "source_scaffold": str(result.get("scaffold", "unknown")),
        },
    }


def _exactly_one(paths: Iterable[Path], description: str) -> Path:
    values = sorted(set(paths))
    if len(values) != 1:
        raise ValueError(f"Expected exactly one {description}, found {len(values)}: {values}")
    return values[0]


def _safe_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value)
    return normalized.strip("_") or "case"


def _first_runnable_provenance(program_root: Path, case_id: str) -> dict[str, Any]:
    provenance_path = program_root / "provenance.json"
    if not provenance_path.is_file():
        raise FileNotFoundError(provenance_path)
    value = json.loads(provenance_path.read_text(encoding="utf-8"))
    if value.get("schema") != "multimodalcode-interact-first-runnable-freeze-1":
        raise ValueError(f"Unexpected first-runnable provenance schema: {provenance_path}")
    if value.get("external_visual_feedback_consumed") is not False:
        raise ValueError("InteractWeb frozen programs must exclude external visual feedback")
    if value.get("official_evaluator_visible") is not False:
        raise ValueError("InteractWeb frozen programs must exclude official evaluator data")
    matches = [
        row
        for row in value.get("cases", [])
        if isinstance(row, dict) and row.get("case_id") == case_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one first-runnable provenance row for {case_id}, "
            f"found {len(matches)}"
        )
    row = matches[0]
    if row.get("selection_rule") != "first_pre_visual_execution_feedback_environment_ready":
        raise ValueError(f"Unexpected first-runnable selection rule for {case_id}")
    if row.get("external_visual_feedback_consumed") is not False:
        raise ValueError(f"First-runnable row consumed external visual feedback: {case_id}")
    if row.get("official_evaluator_visible") is not False:
        raise ValueError(f"First-runnable row exposed an official evaluator: {case_id}")
    return row


def interact_case(
    case_id: str, *, run_root: Path, first_runnable_root: Path
) -> dict[str, Any]:
    histories = run_root.glob(
        f"shards/*/interactweb/*/logs/{case_id}/interaction_history.json"
    )
    history_path = _exactly_one(histories, f"InteractWeb history for {case_id}")
    frozen = _first_runnable_provenance(first_runnable_root, case_id)
    workspace = first_runnable_root / "programs" / _safe_name(case_id)
    if workspace.resolve() != Path(str(frozen.get("program_path", ""))).resolve():
        raise ValueError(f"Frozen InteractWeb program path mismatch for {case_id}")
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    value = json.loads(history_path.read_text(encoding="utf-8"))
    trajectory = value.get("trajectory")
    if not isinstance(trajectory, list):
        raise ValueError(f"InteractWeb trajectory is not a list: {history_path}")
    public_rows = [
        row
        for row in trajectory
        if isinstance(row, dict) and row.get("role") == "user"
    ]
    if not public_rows or not isinstance(public_rows[0].get("content"), str):
        raise ValueError(f"InteractWeb trace has no initial public user request: {history_path}")
    task = public_rows[0]["content"].strip()
    if not task:
        raise ValueError(f"InteractWeb public user request is empty: {case_id}")
    public_task_sha256 = hashlib.sha256(task.encode("utf-8")).hexdigest()
    if frozen.get("public_task_sha256") != public_task_sha256:
        raise ValueError(f"Frozen InteractWeb public task hash mismatch for {case_id}")
    if frozen.get("source_trajectory_sha256") != sha256(history_path):
        raise ValueError(f"Frozen InteractWeb trajectory hash mismatch for {case_id}")
    modified = source_files(workspace)
    if not modified:
        raise ValueError(f"InteractWeb workspace has no source files: {workspace}")
    return {
        "case_id": case_id,
        "benchmark": "interactweb",
        "task": task,
        "program_path": str(workspace.resolve()),
        "task_image_paths": [],
        "modified_files": modified,
        "web_runtime": vite_runtime(workspace),
        "metadata": {
            "public_task_source": "first_user_event_only",
            "public_task_sha256": public_task_sha256,
            "source_interaction_history": str(history_path.resolve()),
            "source_trajectory_sha256": sha256(history_path),
            "excluded_followup_user_events": len(public_rows) - 1,
            "program_version": "first_pre_visual_clean_runnable",
            "first_runnable_cutoff_assistant_turn": frozen[
                "cutoff_assistant_turn"
            ],
            "first_runnable_cutoff_feedback_turn": frozen["cutoff_feedback_turn"],
            "first_runnable_snapshot_sha256": frozen["snapshot_sha256"],
            "first_runnable_provenance": str(
                (first_runnable_root / "provenance.json").resolve()
            ),
            "external_visual_feedback_consumed": False,
        },
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--vision-generation-root", required=True)
    parser.add_argument("--vision-data-root", required=True)
    parser.add_argument("--vision-dependency-cache-root")
    parser.add_argument("--interact-run-root", help=argparse.SUPPRESS)
    parser.add_argument("--interact-first-runnable-root", help=argparse.SUPPRESS)
    parser.add_argument("--vision-case", action="append", default=[])
    parser.add_argument("--interact-case", action="append", default=[])
    args = parser.parse_args()

    if args.interact_run_root or args.interact_first_runnable_root or args.interact_case:
        parser.error(
            "InteractWeb-Bench is archived and outside the current paper scope; "
            "see reports/research_scope_decision.md"
        )

    output = Path(args.output_dir).resolve()
    _require_nonempty_output(output)
    generation_root = Path(args.vision_generation_root).resolve()
    vision_data = Path(args.vision_data_root).resolve()
    dependency_cache_root = (
        Path(args.vision_dependency_cache_root).resolve()
        if args.vision_dependency_cache_root
        else None
    )
    rows = [
        vision_case(
            case_id,
            generation_root=generation_root,
            data_root=vision_data,
            dependency_cache_root=dependency_cache_root,
        )
        for case_id in args.vision_case
    ]
    if not rows:
        raise ValueError("At least one --vision-case is required")
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("Case ids must be unique")

    manifest = output / "cases.jsonl"
    manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    provenance = {
        "schema": "multimodalcode-self-verify-public-cases-1",
        "purpose": "same-policy pilot input; no official evaluator data",
        "official_evaluator_visible": False,
        "vision_private_files_read": [],
        "vision_generation_root": str(generation_root),
        "vision_data_root": str(vision_data),
        "vision_dependency_cache_root": (
            str(dependency_cache_root) if dependency_cache_root else None
        ),
        "case_ids": [row["case_id"] for row in rows],
        "case_count": len(rows),
        "cases_sha256": sha256(manifest),
        "row_digests": {
            row["case_id"]: canonical_sha256(row) for row in rows
        },
    }
    write_json(output / "provenance.json", provenance)
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze InteractWeb programs at their first clean runnable deployment.

The released InteractWeb Builder writes files through ``boltAction`` records and
then receives an automatic ``Execution Feedback`` message.  This adapter
replays only those released file actions and stops at the first feedback that
states ``Environment Ready``.  It never consumes Visual Copilot feedback or an
official evaluator payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


# Keep this parser and fallback byte-for-byte equivalent to the released
# InteractWeb file writer in evaluate/interactweb_bench/upstream/src/utils/
# file_management.py.  Path confinement below is an additional safety check;
# it does not change any admitted relative file action.
FILE_ACTION_PATTERN = re.compile(
    r'<boltAction\s+type="file"\s+filePath="([^"]+)">\s*(.*?)</boltAction>',
    flags=re.DOTALL | re.IGNORECASE,
)
VITE_FALLBACK = """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    watch: {
      usePolling: true,
      interval: 1000
    }
  }
})"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value)
    return normalized.strip("_") or "case"


def require_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty output directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def released_file_actions(response: str) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    for raw_path, content in FILE_ACTION_PATTERN.findall(response):
        # This is the exact decoding order used by the released writer.
        decoded = content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        actions.append((raw_path, decoded))
    return actions


def confined_relative_path(raw_path: str) -> str:
    normalized_raw = raw_path.replace("\\", "/")
    raw = PurePosixPath(normalized_raw)
    if (
        not normalized_raw
        or normalized_raw.startswith("/")
        or any(part == ".." for part in raw.parts)
    ):
        raise ValueError(f"Unsafe InteractWeb file action path: {raw_path!r}")
    released_path = raw_path.lstrip("./\\")
    path = PurePosixPath(released_path.replace("\\", "/"))
    if not released_path or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe InteractWeb file action path: {raw_path!r}")
    return path.as_posix()


def _content_text(row: dict[str, Any]) -> str:
    content = row.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def reconstruct_history(history_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    value = json.loads(history_path.read_text(encoding="utf-8"))
    trajectory = value.get("trajectory")
    if not isinstance(trajectory, list):
        raise ValueError(f"InteractWeb trajectory is not a list: {history_path}")

    first_user = next(
        (
            _content_text(row).strip()
            for row in trajectory
            if isinstance(row, dict) and row.get("role") == "user"
        ),
        "",
    )
    if not first_user:
        raise ValueError(f"InteractWeb history has no initial public request: {history_path}")

    files: dict[str, str] = {}
    artifact_turns: list[dict[str, Any]] = []
    cutoff_assistant_turn: int | None = None
    cutoff_feedback_turn: int | None = None
    clean_feedback_sha256: str | None = None

    for index, row in enumerate(trajectory):
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        text = _content_text(row)

        # Visual Copilot content is an explicit hard boundary.  Nothing after
        # this point may contribute to the frozen program.
        if role == "user" and "Visual Process Audit" in text:
            break
        if role != "assistant" or "<boltArtifact" not in text:
            continue

        actions = released_file_actions(text)
        if not actions:
            continue
        written: list[str] = []
        for raw_path, content in actions:
            relative = confined_relative_path(raw_path)
            files[relative] = content
            written.append(relative)
        if "vite.config.js" not in files:
            files["vite.config.js"] = VITE_FALLBACK
        artifact_turns.append(
            {
                "assistant_turn": index,
                "file_count": len(actions),
                "file_paths": written,
                "assistant_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )

        if index + 1 >= len(trajectory) or not isinstance(trajectory[index + 1], dict):
            continue
        feedback_row = trajectory[index + 1]
        feedback = _content_text(feedback_row)
        clean = (
            feedback_row.get("role") == "user"
            and feedback.startswith("Execution Feedback:")
            and "Environment Ready. Verify UI or Submit." in feedback
            and "Install Error:" not in feedback
            and "Runtime Error:" not in feedback
        )
        if clean:
            cutoff_assistant_turn = index
            cutoff_feedback_turn = index + 1
            clean_feedback_sha256 = hashlib.sha256(feedback.encode("utf-8")).hexdigest()
            break

    if cutoff_assistant_turn is None or cutoff_feedback_turn is None:
        raise ValueError(
            "No clean pre-Visual-Copilot Environment Ready state in "
            f"{history_path}"
        )
    if "package.json" not in files or "index.html" not in files:
        raise ValueError(
            f"First runnable InteractWeb snapshot lacks package.json/index.html: {history_path}"
        )

    provenance = {
        "schema": "multimodalcode-interact-first-runnable-case-1",
        "selection_rule": "first_pre_visual_execution_feedback_environment_ready",
        "source_interaction_history": str(history_path.resolve()),
        "source_trajectory_sha256": sha256(history_path),
        "public_task_sha256": hashlib.sha256(first_user.encode("utf-8")).hexdigest(),
        "cutoff_assistant_turn": cutoff_assistant_turn,
        "cutoff_feedback_turn": cutoff_feedback_turn,
        "clean_feedback_sha256": clean_feedback_sha256,
        "artifact_turns": artifact_turns,
        "artifact_turn_count": len(artifact_turns),
        "file_count": len(files),
        "file_sha256": {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sorted(files.items())
        },
        "external_visual_feedback_consumed": False,
        "official_evaluator_visible": False,
    }
    provenance["snapshot_sha256"] = canonical_sha256(provenance["file_sha256"])
    return files, provenance


def write_snapshot(
    files: dict[str, str], target: Path, *, dependency_source: Path | None = None
) -> dict[str, Any]:
    dependency = validate_dependency_source(files, dependency_source)
    if target.exists():
        raise RuntimeError(f"Refusing to overwrite frozen program: {target}")
    target.mkdir(parents=True)
    for relative, content in sorted(files.items()):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    if dependency["linked"]:
        (target / "node_modules").symlink_to(
            Path(str(dependency["source"])), target_is_directory=True
        )
    return dependency


def validate_dependency_source(
    files: dict[str, str], dependency_source: Path | None
) -> dict[str, Any]:
    """Validate a reusable install without mutating the freeze directory."""

    if dependency_source is None:
        return {"linked": False, "source": None}
    source = dependency_source.resolve()
    package = source.parent / "package.json"
    lock = source.parent / "package-lock.json"
    if not source.is_dir() or not package.is_file() or not lock.is_file():
        raise FileNotFoundError(
            f"Frozen InteractWeb dependency source is incomplete: {source}"
        )
    generated_package = files.get("package.json")
    if generated_package is None or generated_package.encode("utf-8") != package.read_bytes():
        raise ValueError(
            "First-runnable package.json differs from the dependency source package.json"
        )
    return {
        "linked": True,
        "source": str(source),
        "package_sha256": sha256(package),
        "package_lock_sha256": sha256(lock),
    }


def _exactly_one(paths: list[Path], description: str) -> Path:
    values = sorted(set(paths))
    if len(values) != 1:
        raise ValueError(f"Expected exactly one {description}, found {len(values)}: {values}")
    return values[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    parser.error(
        "InteractWeb-Bench is archived and outside the current paper scope; "
        "this historical data-construction entry point is disabled. See "
        "reports/research_scope_decision.md"
    )
    if not args.case:
        raise ValueError("At least one --case is required")
    if len(set(args.case)) != len(args.case):
        raise ValueError("Case IDs must be unique")

    run_root = Path(args.run_root).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty output directory: {output}")

    # Preflight every selected history and dependency install before writing
    # anything.  A rejected case must not leave an apparently valid partial
    # freeze that could later be mistaken for experimental input.
    prepared: list[dict[str, Any]] = []
    for case_id in args.case:
        history = _exactly_one(
            list(
                run_root.glob(
                    f"shards/*/interactweb/*/logs/{case_id}/interaction_history.json"
                )
            ),
            f"history for {case_id}",
        )
        released_workspace = _exactly_one(
            list(run_root.glob(f"shards/*/interactweb/*/workspaces/{case_id}")),
            f"released workspace for {case_id}",
        )
        files, provenance = reconstruct_history(history)
        target = output / "programs" / safe_name(case_id)
        dependency_source = released_workspace / "node_modules"
        dependency = validate_dependency_source(files, dependency_source)
        if target.exists():
            raise RuntimeError(f"Refusing to overwrite frozen program: {target}")
        prepared.append(
            {
                "case_id": case_id,
                "files": files,
                "provenance": provenance,
                "target": target,
                "dependency_source": dependency_source,
                "dependency": dependency,
            }
        )

    require_empty(output)
    case_rows: list[dict[str, Any]] = []
    for item in prepared:
        case_id = str(item["case_id"])
        files = item["files"]
        provenance = item["provenance"]
        target = item["target"]
        dependency = write_snapshot(
            files, target, dependency_source=item["dependency_source"]
        )
        if dependency != item["dependency"]:
            raise RuntimeError(f"Dependency provenance changed during freeze: {case_id}")
        provenance.update(
            {
                "case_id": case_id,
                "program_path": str(target),
                "released_dependency_provenance": dependency,
            }
        )
        case_rows.append(provenance)

    manifest = output / "provenance.json"
    overall = {
        "schema": "multimodalcode-interact-first-runnable-freeze-1",
        "purpose": "pre-Visual-Copilot first clean runnable programs",
        "run_root": str(run_root),
        "case_ids": args.case,
        "case_count": len(case_rows),
        "external_visual_feedback_consumed": False,
        "official_evaluator_visible": False,
        "cases": case_rows,
    }
    manifest.write_text(
        json.dumps(overall, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

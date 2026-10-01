#!/usr/bin/env python3
"""Materialize P_first/P_final into the unmodified Vision2Web result layout.

This is strictly post-trajectory.  It never launches an agent and is the first
point at which benchmark-private ``workflow.json`` may be consumed later by the
official evaluator.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def copy_version(source: Path, destination: Path, task_source: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    for name in ("prototypes", "resources"):
        public_input = task_source / name
        if public_input.is_dir():
            (destination / name).symlink_to(public_input, target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--framework")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    framework = args.framework or result.get("vision2web_framework") or "openhands"
    if framework not in {"openhands", "claude_code"}:
        raise RuntimeError(f"Unexpected Vision2Web framework: {framework!r}")
    case_id = result["case_id"]
    task_type, project = case_id.split("/", 1)
    model = str(result["model"]).replace("litellm_proxy/", "")
    development = Path(result["development_trace"])
    versions = json.loads((development / "versions.json").read_text(encoding="utf-8"))
    if not versions.get("P_first"):
        raise RuntimeError(f"No successful first deployment was observed in {run_dir}")
    task_source = PROJECT_ROOT / "data/vision2web/extracted" / task_type / project
    mode = str(result.get("vision2web_mode") or "official")
    if mode not in {
        "official",
        "browser_enabled",
        "guided_vsv",
        "tools",
        "self_verify",
    }:
        raise RuntimeError(f"Unexpected Vision2Web condition in result: {mode!r}")

    rows = []
    for version_name in ("P_first", "P_final"):
        source = Path(versions[version_name]["path"])
        destination = (
            args.output_root.resolve()
            / mode
            / version_name
            / task_type
            / framework
            / model
            / project
        )
        copy_version(source, destination, task_source)
        rows.append(
            {
                "version": version_name,
                "source": str(source),
                "destination": str(destination),
                "program_sha256": versions[version_name]["program_sha256"],
            }
        )

    manifest = {
        "schema": "multimodalcode-vision2web-version-evaluation-input-1",
        "case_id": case_id,
        "model": model,
        "mode": mode,
        "framework": framework,
        "post_trajectory_only": True,
        "official_evaluator_modified": False,
        "rows": rows,
    }
    output = args.output_root.resolve() / mode / f"{task_type}__{project}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

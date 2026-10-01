#!/usr/bin/env python3
"""Run one official Vision2Web evaluation inside its ClusterX task image.

The outer ClusterX container must use the frozen Vision2Web sandbox image.
This wrapper stages one generated workspace in the directory layout expected
by the released CLI, places the strict ClusterX Docker transport first on
PATH, and then invokes the unmodified ``vision2web evaluate`` command through
``run_official.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DEFAULT_DATASETS = PROJECT_ROOT / "data" / "vision2web" / "extracted"
IMAGE_METADATA = PROJECT_ROOT / "data" / "vision2web" / "image.json"
TRANSPORT_DIR = HERE / "clusterx_transport"
OFFICIAL_RUNNER = HERE / "run_official.py"
TASK_TYPES = ("webpage", "frontend", "website")


def _safe(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")
    if not result:
        raise ValueError(f"value cannot be converted to a safe path component: {value!r}")
    return result


def _load_image_metadata() -> dict:
    data = json.loads(IMAGE_METADATA.read_text(encoding="utf-8"))
    required = {"immutable_image", "target_digest", "source_commit"}
    missing = sorted(required - data.keys())
    if missing:
        raise RuntimeError(f"Vision2Web image metadata is incomplete: {', '.join(missing)}")
    return data


def _split_case(case_id: str) -> tuple[str, str]:
    try:
        task, project = case_id.split("/", 1)
    except ValueError as exc:
        raise ValueError("--case must have the form <webpage|frontend|website>/<project>") from exc
    if task not in TASK_TYPES or not project or "/" in project or project in {".", ".."}:
        raise ValueError(f"invalid Vision2Web case id: {case_id!r}")
    return task, project


def _which_any(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def preflight(datasets_dir: Path) -> dict:
    metadata = _load_image_metadata()
    tools = {
        "bash": _which_any("bash"),
        "curl": _which_any("curl"),
        "node": _which_any("node"),
        "claude": _which_any("claude"),
        "playwright-cli": _which_any("playwright-cli"),
        "chrome": _which_any("google-chrome", "google-chrome-stable", "chromium"),
    }
    errors: list[str] = []
    if sys.version_info[:2] != (3, 12):
        errors.append(f"official image requires Python 3.12; running {sys.version.split()[0]}")
    for name, executable in tools.items():
        if executable is None:
            errors.append(f"required executable not found: {name}")
    if not datasets_dir.is_dir():
        errors.append(f"frozen dataset directory not found: {datasets_dir}")
    for task in TASK_TYPES:
        if not (datasets_dir / task).is_dir():
            errors.append(f"frozen dataset split not found: {datasets_dir / task}")
    if not (TRANSPORT_DIR / "docker").is_file():
        errors.append(f"ClusterX transport not found: {TRANSPORT_DIR / 'docker'}")
    return {
        "ok": not errors,
        "python": sys.version.split()[0],
        "tools": tools,
        "datasets_dir": str(datasets_dir),
        "image": metadata["immutable_image"],
        "expected_digest": metadata["target_digest"],
        "source_commit": metadata["source_commit"],
        "errors": errors,
    }


def _validate_case_source(source: Path, dataset_project: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"generated workspace does not exist: {source}")
    if not (source / "start.sh").is_file():
        raise RuntimeError(f"generated workspace has no start.sh: {source}")
    if not (source / "prototypes").is_dir():
        raise RuntimeError(f"generated workspace has no prototypes directory: {source}")
    if not (dataset_project / "workflow.json").is_file():
        raise RuntimeError(f"official workflow is missing: {dataset_project / 'workflow.json'}")

    expected = {path.name for path in (dataset_project / "prototypes").iterdir() if path.is_file()}
    actual = {path.name for path in (source / "prototypes").iterdir() if path.is_file()}
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            "generated workspace prototype set differs from the frozen case "
            f"(missing={missing[:10]}, extra={extra[:10]})"
        )


def _copy_workspace_once(source: Path, staged: Path) -> str:
    if staged.exists():
        if not staged.is_dir():
            raise RuntimeError(f"staged workspace path is not a directory: {staged}")
        return "reused"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staged, symlinks=False)
    return "copied"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one generated Vision2Web workspace with the frozen official evaluator "
            "inside the pinned ClusterX sandbox image."
        )
    )
    parser.add_argument("--case", help="case id such as frontend/afl")
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "runs" / "vision2web-official",
    )
    parser.add_argument("--model-label", help="label of the coding model that generated the site")
    parser.add_argument("--framework-label", default="openhands")
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument(
        "--functional-model",
        default=os.getenv("VISION2WEB_FUNCTIONAL_MODEL", "claude-sonnet-4-5-20250929"),
    )
    parser.add_argument(
        "--functional-api-key",
        default=os.getenv("VISION2WEB_FUNCTIONAL_API_KEY"),
    )
    parser.add_argument(
        "--functional-base-url",
        default=os.getenv("VISION2WEB_FUNCTIONAL_BASE_URL"),
    )
    parser.add_argument(
        "--visual-model",
        default=os.getenv("VISION2WEB_VISUAL_MODEL", "gemini-3-pro-preview"),
    )
    parser.add_argument(
        "--visual-api-key",
        default=os.getenv("VISION2WEB_VISUAL_API_KEY"),
    )
    parser.add_argument(
        "--visual-base-url",
        default=os.getenv("VISION2WEB_VISUAL_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument(
        "--runtime-image-digest",
        default=os.getenv("VISION2WEB_RUNTIME_IMAGE_DIGEST"),
        help="attest the digest selected in ClusterX; must equal data/vision2web/image.json",
    )
    parser.add_argument("--preflight", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    datasets_dir = args.datasets_dir.resolve()
    report = preflight(datasets_dir)
    if args.preflight:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] else 1
    if not report["ok"]:
        raise RuntimeError("ClusterX preflight failed:\n- " + "\n- ".join(report["errors"]))
    if not args.case or not args.model_label:
        raise ValueError("--case and --model-label are required unless --preflight is used")

    metadata = _load_image_metadata()
    if args.runtime_image_digest != metadata["target_digest"]:
        raise RuntimeError(
            "ClusterX image digest attestation failed: pass/set "
            f"{metadata['target_digest']!r}, got {args.runtime_image_digest!r}"
        )

    task, project = _split_case(args.case)
    source_workspace = args.workspace.resolve()
    dataset_project = datasets_dir / task / project

    model_label = _safe(args.model_label)
    framework_label = _safe(args.framework_label)
    run_dir = args.output_root.resolve() / model_label / task / _safe(project)
    official_results = run_dir / "official_results"
    staged_workspace = official_results / task / framework_label / model_label / project
    result_file = staged_workspace / "evaluation_result.json"
    summary_file = run_dir / "clusterx_evaluation.json"

    if result_file.is_file():
        previous = json.loads(result_file.read_text(encoding="utf-8"))
        _write_json(
            summary_file,
            {
                "status": "resumed-skip",
                "case": args.case,
                "official_result": str(result_file),
                "previous": previous,
            },
        )
        print(summary_file.read_text(encoding="utf-8"))
        return 0

    if staged_workspace.is_dir():
        copy_status = "reused"
    else:
        _validate_case_source(source_workspace, dataset_project)
        copy_status = _copy_workspace_once(source_workspace, staged_workspace)
    _validate_case_source(staged_workspace, dataset_project)
    if not args.functional_api_key or not args.visual_api_key:
        raise RuntimeError(
            "set VISION2WEB_FUNCTIONAL_API_KEY and VISION2WEB_VISUAL_API_KEY "
            "(or pass the corresponding options)"
        )

    transport_state = run_dir / "transport"
    trace_path = transport_state / "transport.jsonl"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": os.pathsep.join((str(TRANSPORT_DIR), environment.get("PATH", ""))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "V2W_CLUSTERX_ENABLED": "1",
            "V2W_CLUSTERX_STATE_DIR": str(transport_state),
            "V2W_CLUSTERX_TRACE": str(trace_path),
            "V2W_CLUSTERX_WORKSPACE": "/workspace",
            "V2W_CLUSTERX_ALLOWED_INPUT_ROOT": str(run_dir),
            "V2W_CLUSTERX_ALLOWED_OUTPUT_ROOT": str(run_dir),
            "V2W_CLUSTERX_EXPECTED_IMAGE": metadata["immutable_image"],
            # The official image runs as root on ClusterX.  Playwright CLI's
            # current Chrome default enables Chromium's kernel sandbox, which
            # cannot start in that container.  This is Playwright's supported
            # config surface and affects isolation only; the frozen workflows
            # and evaluator remain unchanged.
            "PLAYWRIGHT_MCP_CONFIG": str(
                TRANSPORT_DIR / "playwright-cli.config.json"
            ),
        }
    )

    command = [
        sys.executable,
        str(OFFICIAL_RUNNER),
        "--results-dir",
        str(official_results),
        "--datasets-dir",
        str(datasets_dir),
        "--sandbox",
        metadata["immutable_image"],
        "--functional-model",
        args.functional_model,
        "--functional-api-key",
        args.functional_api_key,
        "--visual-api-key",
        args.visual_api_key,
        "--visual-base-url",
        args.visual_base_url,
        "--visual-model",
        args.visual_model,
        "--max-workers",
        "1",
        "--task",
        task,
        "--framework",
        framework_label,
        "--model",
        model_label,
    ]
    if args.functional_base_url:
        command.extend(["--functional-base-url", args.functional_base_url])

    started = datetime.now(timezone.utc)
    completed = subprocess.run(command, env=environment, check=False)
    ended = datetime.now(timezone.utc)
    official_result = None
    if result_file.is_file():
        official_result = json.loads(result_file.read_text(encoding="utf-8"))
    summary = {
        "status": "complete" if completed.returncode == 0 else "error",
        "case": args.case,
        "coding_model": args.model_label,
        "framework": args.framework_label,
        "source_workspace": str(source_workspace),
        "staged_workspace": str(staged_workspace),
        "workspace_stage_status": copy_status,
        "official_results_dir": str(official_results),
        "official_result": official_result,
        "official_cli_returncode": completed.returncode,
        "transport_trace": str(trace_path),
        "official_source_commit": metadata["source_commit"],
        "clusterx_image": metadata["immutable_image"],
        "clusterx_image_digest_attestation": args.runtime_image_digest,
        "playwright_chromium_sandbox": False,
        "started_at_utc": started.isoformat(),
        "ended_at_utc": ended.isoformat(),
    }
    _write_json(summary_file, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

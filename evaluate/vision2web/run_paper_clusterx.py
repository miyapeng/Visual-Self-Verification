#!/usr/bin/env python3
"""Run one Vision2Web paper-protocol evaluation in a ClusterX task.

The paper evaluator at commit 3111cc3 normally creates a Docker container,
copies one generated workspace into it, starts the application, and executes
``GUIAgentTester`` inside that container.  A ClusterX task is already an
isolated container created from the Vision2Web image, so this adapter performs
only that outer lifecycle.  The imported GUI tester, prompts, action parser,
workflow scheduling, screenshots, verdicts, and score files are the frozen
upstream implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
PAPER_UPSTREAM = HERE / "paper_upstream"
PAPER_METADATA = HERE / "PAPER_UPSTREAM.json"
DEFAULT_DATASETS = PROJECT_ROOT / "data" / "vision2web" / "extracted"
IMAGE_METADATA = PROJECT_ROOT / "data" / "vision2web" / "image.json"
TASK_TYPES = ("webpage", "frontend", "website")

# The frozen module must win over any newer Vision2Web package in the image.
sys.path.insert(0, str(PAPER_UPSTREAM))


def _safe(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")
    if not result:
        raise ValueError(f"unsafe empty path component derived from {value!r}")
    return result


def _split_case(case_id: str) -> tuple[str, str]:
    try:
        task, project = case_id.split("/", 1)
    except ValueError as exc:
        raise ValueError("--case must have the form <task>/<project>") from exc
    if task not in TASK_TYPES or not project or "/" in project or project in {".", ".."}:
        raise ValueError(f"invalid Vision2Web case id: {case_id!r}")
    return task, project


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _copy_workspace(source: Path, destination: Path) -> None:
    if destination.exists():
        marker = destination / ".paper_workspace_staged.json"
        if marker.is_file():
            return
        if any(destination.iterdir()):
            raise RuntimeError(
                f"execution workspace is not empty and has no staging marker: {destination}"
            )
        shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
    _write_json(
        destination / ".paper_workspace_staged.json",
        {"source": str(source), "staged_at_utc": datetime.now(timezone.utc).isoformat()},
    )


def _validate_source(source: Path, dataset_project: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"generated workspace not found: {source}")
    if not (source / "start.sh").is_file():
        raise RuntimeError(f"generated workspace has no start.sh: {source}")
    if not (source / "prototypes").is_dir():
        raise RuntimeError(f"generated workspace has no prototypes: {source}")
    workflow = dataset_project / "workflow.json"
    if not workflow.is_file():
        raise RuntimeError(f"frozen workflow missing: {workflow}")
    expected = {
        path.name for path in (dataset_project / "prototypes").iterdir() if path.is_file()
    }
    actual = {path.name for path in (source / "prototypes").iterdir() if path.is_file()}
    if expected != actual:
        raise RuntimeError(
            "prototype set differs from frozen dataset: "
            f"missing={sorted(expected - actual)[:10]}, extra={sorted(actual - expected)[:10]}"
        )


def preflight(datasets_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    tools = {
        name: shutil.which(name)
        for name in ("bash", "curl", "node")
    }
    tools["chrome"] = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
    )
    for name, executable in tools.items():
        if executable is None:
            errors.append(f"required executable not found: {name}")
    if not PAPER_METADATA.is_file():
        errors.append(f"paper evaluator metadata missing: {PAPER_METADATA}")
    if not (PAPER_UPSTREAM / "vision2web/evaluation/gui_agent_test.py").is_file():
        errors.append(f"frozen GUIAgentTester missing under {PAPER_UPSTREAM}")
    if not datasets_dir.is_dir():
        errors.append(f"frozen datasets missing: {datasets_dir}")
    try:
        import openai  # noqa: F401
        import PIL  # noqa: F401
        import playwright  # noqa: F401
        from vision2web.evaluation.gui_agent_test import GUIAgentTester  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment diagnostic
        errors.append(f"paper evaluator import failed: {type(exc).__name__}: {exc}")
    return {
        "ok": not errors,
        "python": sys.version.split()[0],
        "tools": tools,
        "datasets_dir": str(datasets_dir),
        "paper_evaluator": _read_json(PAPER_METADATA) if PAPER_METADATA.is_file() else None,
        "errors": errors,
    }


def _start_application(workspace: Path, log_path: Path) -> tuple[subprocess.Popen[bytes], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        ["bash", "start.sh"],
        cwd=workspace,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=os.environ.copy(),
    )
    return process, log_handle


def _wait_for_application(process: subprocess.Popen[bytes], timeout: int = 600) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        returncode = process.poll()
        if returncode is not None and returncode != 0:
            raise RuntimeError(f"start.sh exited before readiness with code {process.returncode}")
        probe = subprocess.run(
            ["curl", "-sS", "--max-time", "3", "http://127.0.0.1:3000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return time.monotonic() - started
        time.sleep(5)
    raise TimeoutError("application did not become ready on port 3000 within 600 seconds")


def _stop_application(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        if process.poll() is None:
            process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def _run_frozen_workflows(
    *,
    workspace: Path,
    log_dir: Path,
    workflow_data: list[dict[str, Any]],
    api_key: str,
    base_url: str,
    gui_agent_model: str,
    vlm_judge_model: str,
    functional_only: bool,
) -> dict[str, Any]:
    # Imported only after PAPER_UPSTREAM has been inserted at sys.path[0].
    from vision2web.evaluation.gui_agent_test import GUIAgentTester

    tester_class = GUIAgentTester
    if functional_only:
        class FunctionalOnlyGUIAgentTester(GUIAgentTester):
            """Keep the official GUI test path while disabling visual API calls."""

            def _compare_prototype(
                self,
                prototype_screenshot_b64: str,
                actual_screenshot_b64: str,
            ) -> list[dict[str, Any]]:
                self.logger.info(
                    "Prototype comparison disabled by the functional-only transport"
                )
                return []

        tester_class = FunctionalOnlyGUIAgentTester

    done_events = [asyncio.Event() for _ in workflow_data]
    successful: list[int] = []
    failed: dict[int, str] = {}

    async def run_workflow(index: int, workflow_item: dict[str, Any]) -> None:
        dependencies = workflow_item.get("depends_on", [])
        if dependencies:
            await asyncio.gather(*(done_events[dependency].wait() for dependency in dependencies))
        try:
            resolution = workflow_item.get("resolution", {})
            tester = tester_class(
                api_key=api_key,
                base_url=base_url,
                gui_agent_model=gui_agent_model,
                vlm_judge_model=vlm_judge_model,
                headless=True,
                window_width=resolution.get("width", 1920),
                window_height=resolution.get("height", 1080),
                output_dir=str(workspace),
                log_dir=str(log_dir),
            )
            await tester.run_test(
                url="http://localhost:3000",
                workflow_item=workflow_item,
                workflow_idx=index,
                dataset_path=str(workspace),
                output_dir=workspace,
            )
            successful.append(index)
        except Exception as exc:
            failed[index] = f"{type(exc).__name__}: {exc}"
        finally:
            done_events[index].set()

    # This is the exact scheduling policy generated by the paper evaluator:
    # workflows run concurrently after their declared dependencies complete.
    await asyncio.gather(
        *(run_workflow(index, item) for index, item in enumerate(workflow_data)),
        return_exceptions=True,
    )
    return {
        "total_workflows": len(workflow_data),
        "successful_workflows": sorted(successful),
        "failed_workflows": {str(key): value for key, value in sorted(failed.items())},
    }


def _publish_results(attempt_workspace: Path, final_project: Path) -> None:
    final_project.mkdir(parents=True, exist_ok=True)
    source_results = attempt_workspace / "test_results"
    if source_results.is_dir():
        destination = final_project / "test_results"
        if destination.exists():
            raise RuntimeError(f"refusing to overwrite published test results: {destination}")
        shutil.copytree(source_results, destination, symlinks=False)


def _preserve_attempt_logs(execution_workspace: Path, attempt_root: Path) -> None:
    source = execution_workspace / "logs"
    destination = attempt_root / "logs"
    if source.is_dir() and not destination.exists():
        shutil.copytree(source, destination, symlinks=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="case id such as frontend/community_hpe")
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument(
        "--execution-workspace",
        type=Path,
        default=Path("/workspace"),
        help="isolated runtime location; /workspace matches the official Docker contract",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model-label", required=False)
    parser.add_argument("--framework-label", default="openhands")
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--api-key", default=os.getenv("VISION2WEB_PAPER_API_KEY"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("VISION2WEB_PAPER_BASE_URL", "http://35.220.164.252:3888/v1"),
    )
    parser.add_argument(
        "--gui-agent-model",
        default=os.getenv("VISION2WEB_PAPER_GUI_MODEL", "glm-4.6v"),
    )
    parser.add_argument(
        "--vlm-judge-model",
        default=os.getenv("VISION2WEB_PAPER_VLM_MODEL", "gemini-3.1-pro-preview"),
    )
    parser.add_argument(
        "--functional-only",
        action="store_true",
        help="run the official GLM GUI tests but make no visual-judge API calls",
    )
    parser.add_argument("--attempt-label", default=os.getenv("CLUSTERX_JOB_ID", "manual"))
    parser.add_argument("--runtime-image-digest", default=os.getenv("VISION2WEB_RUNTIME_IMAGE_DIGEST"))
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
        raise RuntimeError("paper evaluator preflight failed:\n- " + "\n- ".join(report["errors"]))
    if not args.case or not args.model_label:
        raise ValueError("--case and --model-label are required")
    if not args.api_key:
        raise RuntimeError("VISION2WEB_PAPER_API_KEY (or --api-key) is required")

    image_metadata = _read_json(IMAGE_METADATA)
    if args.runtime_image_digest != image_metadata["target_digest"]:
        raise RuntimeError(
            "runtime image digest attestation failed: "
            f"expected {image_metadata['target_digest']}, got {args.runtime_image_digest}"
        )

    task, project = _split_case(args.case)
    dataset_project = datasets_dir / task / project
    source_workspace = args.workspace.resolve()
    _validate_source(source_workspace, dataset_project)

    output_root = args.output_root.resolve()
    model_label = _safe(args.model_label)
    framework_label = _safe(args.framework_label)
    case_root = output_root / "cases" / model_label / task / _safe(project)
    final_project = output_root / "paper_results" / task / framework_label / model_label / project
    result_file = final_project / "evaluation_result.json"
    summary_file = case_root / "clusterx_paper_evaluation.json"
    if result_file.is_file():
        summary = {
            "status": "resumed-skip",
            "case": args.case,
            "published_result": str(result_file),
        }
        _write_json(summary_file, summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    attempt_label = _safe(args.attempt_label)
    attempt_root = case_root / "attempts" / attempt_label
    execution_workspace = args.execution_workspace.resolve()
    _copy_workspace(source_workspace, execution_workspace)
    shutil.copy2(dataset_project / "workflow.json", execution_workspace / "workflow.json")

    workflow_data = _read_json(dataset_project / "workflow.json")
    started = datetime.now(timezone.utc)
    server: subprocess.Popen[bytes] | None = None
    server_log = attempt_root / "deployment.log"
    log_handle = None
    status = "error"
    error: str | None = None
    readiness_seconds: float | None = None
    workflow_summary: dict[str, Any] | None = None
    try:
        server, log_handle = _start_application(execution_workspace, server_log)
        readiness_seconds = _wait_for_application(server)
        workflow_summary = asyncio.run(
            _run_frozen_workflows(
                workspace=execution_workspace,
                log_dir=attempt_root / "logs",
                workflow_data=workflow_data,
                api_key=args.api_key,
                base_url=args.base_url,
                gui_agent_model=args.gui_agent_model,
                vlm_judge_model=args.vlm_judge_model,
                functional_only=args.functional_only,
            )
        )
        _publish_results(execution_workspace, final_project)
        evaluation_result = {
            "project": project,
            "task_type": task,
            "framework": framework_label,
            "model": model_label,
            "start_time": started.isoformat(),
            "status": "success",
        }
        _write_json(result_file, evaluation_result)
        status = "complete"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _stop_application(server)
        if log_handle is not None:
            log_handle.close()
        _preserve_attempt_logs(execution_workspace, attempt_root)

    ended = datetime.now(timezone.utc)
    summary = {
        "status": status,
        "case": args.case,
        "coding_model": args.model_label,
        "framework": args.framework_label,
        "source_workspace": str(source_workspace),
        "execution_workspace": str(execution_workspace),
        "published_project": str(final_project),
        "paper_evaluator_commit": _read_json(PAPER_METADATA)["source_commit"],
        "gui_agent_model": args.gui_agent_model,
        "functional_only": args.functional_only,
        "vlm_judge_model": None if args.functional_only else args.vlm_judge_model,
        "paper_functional_protocol_exact": args.gui_agent_model.lower() == "glm-4.6v",
        "paper_visual_model_exact": (
            False
            if args.functional_only
            else args.vlm_judge_model.lower() == "gemini-3-pro-preview"
        ),
        "base_url": args.base_url,
        "clusterx_image": image_metadata["immutable_image"],
        "clusterx_image_digest_attestation": args.runtime_image_digest,
        "readiness_seconds": readiness_seconds,
        "workflow_summary": workflow_summary,
        "error": error,
        "deployment_log": str(server_log),
        "started_at_utc": started.isoformat(),
        "ended_at_utc": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
    }
    _write_json(summary_file, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

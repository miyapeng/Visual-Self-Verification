#!/usr/bin/env python3
"""Controlled mini-swe-agent tool-loop diagnostic for public InteractWeb failures."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path("/data/miyapeng/mmcode/MultimodalCode")
SRC_ROOT = PROJECT_ROOT / "src"
MINI_SRC = PROJECT_ROOT / "scaffolds/mini_swe_agent/src"
for source in (SRC_ROOT, MINI_SRC):
    sys.path.insert(0, str(source))

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
from multimodalcode.agent_harness.cases import AgentCase
from multimodalcode.agent_harness.mini_runner import MULTIMODAL_REGEX, _task_with_images
from multimodalcode.agent_harness.prompts import observation_template
from multimodalcode.io import write_json
from multimodalcode.research.interactive import (
    PROGRAM_RUNTIME_DIRECTORIES,
    PROGRAM_SOURCE_SUFFIXES,
    InteractiveJudge,
    hash_program,
)
from multimodalcode.research.schema import ActionPlan
from multimodalcode.research.tools import SafeFileToolExecutor

DEFAULT_MANIFEST = PROJECT_ROOT / "configs/research/interactweb_active_repair_cases.json"
DEFAULT_CONTEXT_ROOT = PROJECT_ROOT / "runs/research/active_visual_verification/interactweb-execution-rooted-qwen35-001/execution_rooted/guarded_frontier"
SYSTEM_TEMPLATE = """You are a coding agent with one native Bash tool. Work iteratively: inspect the repository, make a small localized change, execute the supplied public interaction check, inspect its concrete evidence, and continue until the task is solved. Every response must invoke exactly one Bash tool action. Do not merely describe commands. Avoid interactive programs, network access, dependency changes, and edits outside the current workspace."""
INSTANCE_TEMPLATE = """<task>
{{ task }}
</task>

Repair the existing web application in the current working directory. Inspect and search before editing. After each repair, execute this exact deterministic public interaction check:

{{ public_check_command }}

It prints checklist-level evidence and browser artifact paths. View a PNG with:
python {{ visual_tool }} view-image PATH

Do not edit the checker, frozen plan, dependencies, project infrastructure, or files outside this workspace. Finish only after rerunning the check, using:
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
"""


def load_row(path: Path, case_id: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in value if str(row.get("case_id")) == case_id]
    if len(rows) != 1:
        raise KeyError(f"Expected one case_id={case_id!r}, found {len(rows)}")
    return rows[0]


def compact_check(result: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "checklist_id", "status", "expected", "observed",
        "first_failing_action", "residual", "evidence_references",
    )
    return {
        "score": result.get("score"),
        "code_version": result.get("code_version"),
        "status": result.get("status"),
        "result_path": result.get("result_path"),
        "checklist": [
            {key: item.get(key) for key in fields}
            for item in result.get("checklist", [])
        ],
    }


def public_check(
    manifest: Path, case_id: str, workspace: Path, output: Path, purpose: str
) -> dict[str, Any]:
    row = load_row(manifest, case_id)
    marker = workspace / ".mmcode_repair_workspace"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != case_id:
        raise RuntimeError("Public check is restricted to the prepared scratch workspace")
    return InteractiveJudge(output).execute(
        case_id=case_id,
        program_path=workspace,
        plan=ActionPlan.from_dict(row["plan"]),
        purpose=purpose,
        record_video=False,
    )


def source_manifest(root: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == ".mmcode_repair_workspace":
            continue
        if any(part in PROGRAM_RUNTIME_DIRECTORIES or part == ".git" for part in relative.parts):
            continue
        if path.suffix.lower() in PROGRAM_SOURCE_SUFFIXES:
            rows[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def source_patch(before: Path, after: Path, paths: list[str]) -> str:
    pieces: list[str] = []
    for relative in paths:
        old_path, new_path = before / relative, after / relative
        old = old_path.read_text(encoding="utf-8", errors="replace").splitlines(True) if old_path.is_file() else []
        new = new_path.read_text(encoding="utf-8", errors="replace").splitlines(True) if new_path.is_file() else []
        pieces.extend(difflib.unified_diff(old, new, fromfile=f"a/{relative}" if old else "/dev/null", tofile=f"b/{relative}" if new else "/dev/null"))
    return "".join(pieces)


def checker_snapshot() -> dict[str, Any]:
    paths = [
        Path(__file__).resolve(),
        PROJECT_ROOT / "scripts/research/run.py",
        PROJECT_ROOT / "scripts/interactive_judge_playwright.js",
        MINI_SRC / "minisweagent/agents/default.py",
        MINI_SRC / "minisweagent/environments/local.py",
        MINI_SRC / "minisweagent/models/litellm_model.py",
    ]
    paths.extend(sorted((SRC_ROOT / "multimodalcode/research").glob("*.py")))
    rows = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    combined = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"combined_sha256": combined, "files": rows}


def redact_images(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("data:image/"):
        return {"redacted_image_url_sha256": hashlib.sha256(value.encode()).hexdigest(), "characters": len(value)}
    if isinstance(value, list):
        return [redact_images(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_images(item) for key, item in value.items()}
    return value


def command_check(args: argparse.Namespace) -> int:
    result = public_check(Path(args.manifest).resolve(), args.case_id, Path(args.workspace).resolve(), Path(args.output_root).resolve(), "agent-public-recheck")
    print(json.dumps(compact_check(result), ensure_ascii=False, indent=2))
    return 0 if float(result.get("score", 0.0)) >= 1.0 else 1


def command_run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    manifest = Path(args.manifest).resolve()
    context_root = Path(args.context_root).resolve()
    case_root = Path(args.output_root).resolve() / args.case_id
    workspace = case_root / "workspace"
    case_root.mkdir(parents=True, exist_ok=True)
    row = load_row(manifest, args.case_id)
    source = (manifest.parent / str(row["program_path"])).resolve()
    tool = SafeFileToolExecutor.prepare(source, workspace, case_root / "checkpoints")
    dependency_link = workspace / "node_modules"
    if dependency_link.is_symlink():
        dependency_source = dependency_link.resolve()
        dependency_link.unlink()
        shutil.copytree(dependency_source, dependency_link, symlinks=True)
    (workspace / ".mmcode_repair_workspace").write_text(args.case_id + "\n", encoding="utf-8")
    initial_version = hash_program(workspace)
    checkpoint = source
    initial_manifest = source_manifest(workspace)
    checker_before = checker_snapshot()

    context_path = context_root / "cases" / args.case_id / "contexts/iteration-00.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    records = context.get("records", [])
    if len(records) != 1 or not Path(records[0]["image_path"]).is_file():
        raise RuntimeError("Expected one frozen failure record and image")
    failure_text = str(records[0]["text"])
    failure_image = Path(records[0]["image_path"]).resolve()
    initial = public_check(manifest, args.case_id, workspace, case_root / "initial_check", "tool-loop-initial")

    runtime_script = Path(__file__).resolve()
    check_command = shlex.join([
        "env", "PYTHONDONTWRITEBYTECODE=1", f"PYTHONPATH={SRC_ROOT}",
        sys.executable, str(runtime_script), "check", "--manifest", str(manifest),
        "--case-id", args.case_id, "--workspace", ".", "--output-root",
        str(case_root / "agent_checks"),
    ])
    task = f"{row['task']}\n\nInitial public failure evidence:\n{failure_text}"
    agent_case = AgentCase(
        benchmark="interactweb-repair", case_id=args.case_id,
        task_type="public-interaction-repair", prompt=task,
        image_paths=(failure_image,), metadata={},
    )
    trajectory_path = case_root / "trajectory.json"
    agent_outcome: dict[str, Any] = {"exit_status": "DryRun", "submission": ""}
    agent_error: str | None = None
    if not args.dry_run:
        llm = LitellmModel(
            model_name=f"hosted_vllm/{args.model}",
            model_kwargs={
                "api_base": args.base_url.rstrip("/"), "api_key": args.api_key,
                "temperature": 0, "max_tokens": args.max_tokens, "drop_params": True,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
            cost_tracking="ignore_errors", multimodal_regex=MULTIMODAL_REGEX,
            observation_template=observation_template(),
        )
        environment = LocalEnvironment(
            cwd=str(workspace), timeout=args.command_timeout,
            env={
                "PAGER": "cat", "MANPAGER": "cat", "BASH_ENV": "",
                "HOME": str(case_root / "home"), "XDG_CACHE_HOME": str(case_root / "cache"),
                "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
                "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
            },
        )
        agent = DefaultAgent(
            llm, environment, system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE, step_limit=args.steps, cost_limit=0,
            wall_time_limit_seconds=args.wall_time, max_consecutive_format_errors=3,
            output_path=trajectory_path,
        )
        agent.extra_template_vars.update({
            "public_check_command": check_command,
            "visual_tool": str(PROJECT_ROOT / "scripts/agents/visual_tool.py"),
        })
        try:
            agent_outcome = agent.run(_task_with_images(agent_case, case_root / "input_transport.json"))
        except Exception as exc:
            agent_error = f"{type(exc).__name__}: {exc}"

    final = public_check(manifest, args.case_id, workspace, case_root / "final_check", "tool-loop-final-certification")
    checker_after = checker_snapshot()
    final_manifest = source_manifest(workspace)
    changed_files = sorted(path for path in set(initial_manifest) | set(final_manifest) if initial_manifest.get(path) != final_manifest.get(path))
    (case_root / "patch.diff").write_text(source_patch(checkpoint, workspace, changed_files), encoding="utf-8")
    trajectory_data: dict[str, Any] = {}
    if trajectory_path.is_file():
        trajectory_data = json.loads(trajectory_path.read_text(encoding="utf-8"))
        write_json(case_root / "trajectory.compact.json", redact_images(trajectory_data))

    initial_score, final_score = float(initial.get("score", 0.0)), float(final.get("score", 0.0))
    summary = {
        "schema": "multimodalcode-interactweb-tool-loop-1", "case_id": args.case_id,
        "model": args.model, "interface": "mini-swe-agent-v2-native-bash",
        "public_information_only": True, "official_benchmark_score": False,
        "dry_run": bool(args.dry_run), "initial_score": initial_score, "final_score": final_score,
        "repaired": final_score >= 1.0 and initial_score < 1.0, "improved": final_score > initial_score,
        "initial_version": initial_version, "final_version": hash_program(workspace),
        "changed_files": changed_files, "agent_outcome": agent_outcome, "agent_error": agent_error,
        "trajectory_path": str(trajectory_path), "patch_path": str(case_root / "patch.diff"),
        "initial_check": compact_check(initial), "final_check": compact_check(final),
        "checker_snapshot_before": checker_before, "checker_snapshot_after": checker_after,
        "checker_unchanged": checker_before == checker_after,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "failure_context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
        "steps_budget": args.steps, "wall_time_budget_seconds": args.wall_time,
        "max_tokens_per_call": args.max_tokens, "wall_seconds": time.monotonic() - started,
    }
    write_json(case_root / "summary.json", summary)
    commands = [
        str(action["command"])
        for message in trajectory_data.get("messages", [])
        for action in message.get("extra", {}).get("actions", [])
        if isinstance(action, dict) and isinstance(action.get("command"), str)
    ]
    def file_digest(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    compact_summary = {
        "schema": "iwtool1", "case": args.case_id, "i": initial_score, "f": final_score,
        "r": summary["repaired"], "imp": summary["improved"],
        "check": summary["checker_unchanged"], "chg": len(changed_files),
        "file": changed_files[0][:32] if changed_files else None,
        "exit": agent_outcome.get("exit_status"), "err": (agent_error or "")[:24] or None,
        "api": trajectory_data.get("info", {}).get("model_stats", {}).get("api_calls"),
        "bash": len(commands),
        "cmd_sha": hashlib.sha256("\n".join(commands).encode()).hexdigest(),
        "traj_sha": file_digest(case_root / "trajectory.compact.json"),
        "patch_sha": file_digest(case_root / "patch.diff"),
        "sec": round(float(summary["wall_seconds"]), 3),
    }
    (case_root / "compact-summary.json").write_text(
        json.dumps(compact_summary, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    check.add_argument("--case-id", required=True)
    check.add_argument("--workspace", required=True)
    check.add_argument("--output-root", required=True)
    check.set_defaults(function=command_check)
    run = sub.add_parser("run")
    run.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    run.add_argument("--context-root", default=str(DEFAULT_CONTEXT_ROOT))
    run.add_argument("--case-id", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument("--model", default="Qwen3.5-9B")
    run.add_argument("--base-url", default="http://127.0.0.1:18035/v1")
    run.add_argument("--api-key", default="EMPTY")
    run.add_argument("--steps", type=int, default=8)
    run.add_argument("--wall-time", type=int, default=900)
    run.add_argument("--command-timeout", type=int, default=120)
    run.add_argument("--max-tokens", type=int, default=4096)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(function=command_run)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(int(args.function(args)))

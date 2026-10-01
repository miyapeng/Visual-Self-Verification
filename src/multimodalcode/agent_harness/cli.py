"""Command-line entry point for interactive coding-agent benchmark runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .cases import list_case_ids, load_case, prepare_workspace
from .claude_code_runner import run_claude_code
from .mini_runner import run_mini
from .openhands_runner import run_openhands
from .registry import (
    SCAFFOLDS,
    VENDORED_MINI_ROOT,
    VENDORED_OPENHANDS_ROOT,
    VISION2WEB_CLAUDE_CODE,
    canonical_benchmark,
)
from .trajectory import normalize_claude_code, normalize_mini, normalize_openhands
from .vision2web_experiment import VISION2WEB_MODES


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def command_list(args: argparse.Namespace) -> int:
    names = [canonical_benchmark(args.benchmark)] if args.benchmark else list(SCAFFOLDS)
    result = []
    for name in names:
        spec = SCAFFOLDS[name]
        row = {**spec.to_dict(), "case_count": len(list_case_ids(name))}
        if name == "vision2web":
            row["available_scaffolds"] = [
                spec.to_dict(),
                VISION2WEB_CLAUDE_CODE.to_dict(),
            ]
        result.append(row)
    _print(result)
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    benchmark = canonical_benchmark(args.benchmark)
    ids = [args.case] if args.case else list_case_ids(benchmark)[: args.limit]
    _print([load_case(benchmark, case_id).to_dict() for case_id in ids])
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    case = load_case(args.benchmark, args.case)
    workspace = prepare_workspace(case, Path(args.workspace))
    _print({"case": case.to_dict(), "workspace": str(workspace)})
    return 0


def _assert_swe_commit(workspace: Path, expected: str) -> None:
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True, text=True, capture_output=True
    ).stdout.strip()
    if actual != expected:
        raise RuntimeError(f"SWE-MM image mismatch: expected base commit {expected}, got {actual}")


def _default_workspace(case, run_dir: Path) -> Path:
    if case.benchmark == "swe-mm":
        return Path("/testbed")
    if case.benchmark == "vision2web":
        return Path("/workspace")
    return run_dir / "workspace"


def _export_vision_workspace(case, workspace: Path, run_dir: Path) -> Path:
    """Persist generated files while linking the already-frozen task inputs."""
    target = run_dir / "workspace"
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = run_dir / f"workspace-attempt-{stamp}"
    shutil.copytree(
        workspace,
        target,
        ignore=shutil.ignore_patterns("prototypes", "resources"),
        symlinks=True,
    )
    assert case.source_dir is not None
    for name in ("prototypes", "resources"):
        source = case.source_dir / name
        if source.exists():
            (target / name).symlink_to(source, target_is_directory=True)
    return target


def command_run(args: argparse.Namespace) -> int:
    case = load_case(args.benchmark, args.case)
    run_dir = Path(args.output_root).resolve() / _safe(args.model) / case.benchmark
    if case.benchmark == "vision2web":
        # Official and experimental OpenHands runs must never resume from or
        # overwrite one another.
        if args.vision2web_framework == "claude_code":
            run_dir = run_dir / "claude_code" / args.vision2web_mode
        else:
            condition = (
                args.vision2web_mode
                if args.openhands_profile == "official"
                else args.openhands_profile
            )
            run_dir = run_dir / condition
    run_dir = run_dir / _safe(case.case_id)
    result_path = run_dir / "result.json"
    if result_path.is_file() and not args.force:
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("status") == "success":
            _print({"status": "resumed-skip", "result": str(result_path), "previous": previous})
            return 0
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(args.workspace).resolve() if args.workspace else _default_workspace(case, run_dir)

    result = {
        "benchmark": case.benchmark,
        "case_id": case.case_id,
        "scaffold": (
            "claude-code-cli"
            if case.benchmark == "vision2web"
            and args.vision2web_framework == "claude_code"
            else case.scaffold
        ),
        "model": args.model,
        "base_url": args.base_url,
        "workspace": str(workspace),
        "status": "failed",
        "scaffold_spec": (
            VISION2WEB_CLAUDE_CODE.to_dict()
            if case.benchmark == "vision2web"
            and args.vision2web_framework == "claude_code"
            else SCAFFOLDS[case.benchmark].to_dict()
        ),
        "mini_profile": args.mini_profile if case.benchmark != "vision2web" else None,
        "openhands_profile": args.openhands_profile if case.benchmark == "vision2web" else None,
        "vision2web_mode": args.vision2web_mode if case.benchmark == "vision2web" else None,
        "vision2web_framework": (
            args.vision2web_framework if case.benchmark == "vision2web" else None
        ),
        "openhands_require_vision": (
            args.openhands_require_vision
            if case.benchmark == "vision2web"
            and args.vision2web_framework == "openhands"
            else None
        ),
    }
    raw_path: Path | None = None
    try:
        # Keep runner bookkeeping outside the agent's workspace.  In
        # particular, Vision2Web now receives exactly the files copied by its
        # official inference engine.
        workspace = prepare_workspace(
            case,
            workspace,
            run_dir / "case.json",
            scaffold_override=(
                "claude-code-cli"
                if case.benchmark == "vision2web"
                and args.vision2web_framework == "claude_code"
                else None
            ),
        )
        if case.benchmark == "swe-mm":
            _assert_swe_commit(workspace, case.metadata["base_commit"])
        raw_path = run_dir / (
            (
                "claude.events.jsonl"
                if args.vision2web_framework == "claude_code"
                else "openhands.events.jsonl"
            )
            if case.benchmark == "vision2web"
            else "mini.trajectory.json"
        )
        if case.benchmark == "vision2web":
            if args.vision2web_framework == "claude_code":
                outcome = run_claude_code(
                    case,
                    workspace,
                    raw_path,
                    run_dir / "claude.stderr.log",
                    model=args.model,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    timeout=args.wall_time if args.wall_time is not None else 7200,
                    vision2web_mode=args.vision2web_mode,
                    max_retries=args.claude_max_retries,
                    executable=args.claude_executable,
                )
                normalize_claude_code(
                    raw_path, run_dir / "trajectory.json", case.to_dict()
                )
            else:
                openhands_model = (
                    args.model
                    if args.model.startswith(
                        ("openai/", "anthropic/", "hosted_vllm/", "litellm_proxy/")
                    )
                    else f"openai/{args.model}"
                )
                outcome = run_openhands(
                    case,
                    workspace,
                    raw_path,
                    run_dir / "openhands.stderr.log",
                    model=openhands_model,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    timeout=args.wall_time if args.wall_time is not None else 7200,
                    runtime_root=Path(args.openhands_root),
                    profile=args.openhands_profile,
                    vision2web_mode=args.vision2web_mode,
                    max_retries=args.openhands_max_retries,
                    require_vision=args.openhands_require_vision,
                    python_executable=args.openhands_python,
                    installed_executable=(
                        args.openhands_executable
                        if args.openhands_runtime == "installed"
                        else None
                    ),
                )
                normalize_openhands(
                    raw_path, run_dir / "trajectory.json", case.to_dict()
                )
            result.update(outcome)
            if workspace.is_dir() and any(workspace.iterdir()):
                result["workspace_artifact"] = str(
                    _export_vision_workspace(case, workspace, run_dir)
                )
        else:
            outcome = run_mini(
                case,
                workspace,
                raw_path,
                mini_root=Path(args.mini_root),
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                tool_mode=args.tool_mode,
                profile=args.mini_profile,
                step_limit=args.steps,
                wall_time=args.wall_time if args.wall_time is not None else 3600,
                command_timeout=args.command_timeout,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            normalize_mini(raw_path, run_dir / "trajectory.json", case.to_dict())
            result["agent_outcome"] = outcome
            result["status"] = "success" if outcome.get("exit_status") == "Submitted" else "incomplete"
            if case.benchmark == "swe-mm" and outcome.get("submission"):
                (run_dir / "patch.diff").write_text(outcome["submission"], encoding="utf-8")
            if case.benchmark == "design2code" and not (workspace / "index.html").is_file():
                result["status"] = "failed"
                result["error"] = "Agent submitted without index.html"
            if case.benchmark == "chartmimic" and not (workspace / "candidate.py").is_file():
                result["status"] = "failed"
                result["error"] = "Agent submitted without candidate.py"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        # Agent/runtime failures are research evidence too. Preserve and
        # normalize any events written before an exception (for example a
        # context-window overflow) instead of losing the inspectable trace.
        if raw_path is not None and raw_path.is_file():
            try:
                if case.benchmark == "vision2web":
                    if args.vision2web_framework == "claude_code":
                        normalize_claude_code(
                            raw_path, run_dir / "trajectory.json", case.to_dict()
                        )
                    else:
                        normalize_openhands(
                            raw_path, run_dir / "trajectory.json", case.to_dict()
                        )
                else:
                    normalize_mini(raw_path, run_dir / "trajectory.json", case.to_dict())
            except Exception as normalize_exc:
                result["trajectory_normalization_error"] = (
                    f"{type(normalize_exc).__name__}: {normalize_exc}"
                )
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _print({"result": str(result_path), **result})
    return 0 if result["status"] == "success" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run multimodal coding benchmarks through their pinned coding-agent scaffolds."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List scaffold choices and case counts")
    list_parser.add_argument("benchmark", nargs="?")
    list_parser.set_defaults(function=command_list)

    inspect = subparsers.add_parser("inspect", help="Inspect agent-visible case inputs")
    inspect.add_argument("benchmark")
    inspect.add_argument("--case")
    inspect.add_argument("--limit", type=int, default=1)
    inspect.set_defaults(function=command_inspect)

    prepare = subparsers.add_parser("prepare", help="Prepare one non-oracle agent workspace")
    prepare.add_argument("benchmark")
    prepare.add_argument("case")
    prepare.add_argument("--workspace", required=True)
    prepare.set_defaults(function=command_prepare)

    run = subparsers.add_parser("run", help="Run one case (resume-safe by default)")
    run.add_argument("benchmark")
    run.add_argument("case")
    run.add_argument("--model", required=True)
    run.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    run.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "EMPTY"))
    run.add_argument("--output-root", default="runs/agents")
    run.add_argument("--workspace")
    run.add_argument("--mini-root", default=str(VENDORED_MINI_ROOT))
    run.add_argument("--openhands-root", default=str(VENDORED_OPENHANDS_ROOT))
    run.add_argument("--openhands-python", default="python3.12")
    run.add_argument(
        "--openhands-runtime",
        choices=("vendored", "installed"),
        default="vendored",
        help="use the in-repository source snapshot (default) or an installed CLI compatibility path",
    )
    run.add_argument("--openhands-executable", default="openhands")
    run.add_argument(
        "--vision2web-framework",
        choices=("openhands", "claude_code"),
        default="openhands",
        help="Vision2Web's released OpenHands or Claude Code inference scaffold",
    )
    run.add_argument("--claude-executable", default="claude")
    run.add_argument(
        "--claude-max-retries",
        type=int,
        default=2,
        help="Vision2Web Claude Code retries after ordinary failures (official default: 2)",
    )
    run.add_argument(
        "--openhands-profile",
        choices=("official", "research"),
        default="official",
        help=(
            "official runs the unmodified frozen Vision2Web OpenHands CLI; research enables "
            "explicit local-model capability overrides"
        ),
    )
    run.add_argument(
        "--vision2web-mode",
        choices=VISION2WEB_MODES,
        default="official",
        help=(
            "Vision2Web condition: exact official inference, OpenHands with the "
            "browser affordance enabled, or the same browser capability plus the "
            "detailed guided-VSV procedure. Claude Code supports official and "
            "guided_vsv because its official scaffold already includes playwright-cli."
        ),
    )
    run.add_argument(
        "--openhands-max-retries",
        type=int,
        default=2,
        help="Vision2Web OpenHands retries after ordinary failures (official default: 2)",
    )
    run.add_argument(
        "--openhands-require-vision",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "require the OpenHands model route to advertise image support; use "
            "--no-openhands-require-vision only for an explicitly reported "
            "text-only diagnostic model"
        ),
    )
    run.add_argument("--tool-mode", choices=("text", "native"), default="text")
    run.add_argument(
        "--mini-profile",
        choices=("controlled", "swebench-official"),
        default="controlled",
        help="controlled project settings or the frozen upstream mini-SWE-agent SWE-bench config",
    )
    run.add_argument("--steps", type=int, default=50)
    run.add_argument(
        "--wall-time",
        type=int,
        default=None,
        help="per-attempt timeout (default: 7200 for Vision2Web, 3600 otherwise)",
    )
    run.add_argument("--command-timeout", type=int, default=120)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--max-tokens", type=int, default=4096)
    run.add_argument("--force", action="store_true", help="rerun even when a successful result exists")
    run.set_defaults(function=command_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.function(args))

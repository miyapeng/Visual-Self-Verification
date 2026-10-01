#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodalcode.backends import create_backend
from multimodalcode.io import read_json, write_json
from multimodalcode.research.agent import AgentCase, AgentLoop
from multimodalcode.research.context import (
    ConservedFrontierPolicy,
    ContextBudget,
    FrontierPolicy,
    FullHistoryPolicy,
    GuardedFrontierPolicy,
    GuardedFrontierTextOnlyPolicy,
    LLMSummaryPolicy,
    OracleEvidencePolicy,
    RecentPolicy,
    ResidualPolicy,
    SummaryPolicy,
    UnboundFrontierPolicy,
)
from multimodalcode.research.interactive import InteractiveJudge
from multimodalcode.research.planner import OneShotActionPlanner
from multimodalcode.research.schema import ActionPlan


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]


def _resolve(base: Path, value: str) -> str:
    path = Path(value)
    return str((base / path).resolve()) if not path.is_absolute() else str(path.resolve())


def _load_cases(
    path: str | Path,
    limit: int | None = None,
    case_ids: Iterable[str] | None = None,
) -> List[AgentCase]:
    manifest = Path(path).resolve()
    rows = _load_rows(manifest)
    selected_ids = {str(value) for value in (case_ids or [])}
    if selected_ids:
        rows = [row for row in rows if str(row.get("case_id")) in selected_ids]
        found = {str(row.get("case_id")) for row in rows}
        missing = sorted(selected_ids - found)
        if missing:
            raise ValueError(f"Unknown --case-id values: {missing}")
    if limit is not None:
        rows = rows[:limit]
    cases: List[AgentCase] = []
    for row in rows:
        program_path = _resolve(manifest.parent, row["program_path"])
        plan_value = row.get("plan")
        if plan_value is None:
            plan_path = Path(_resolve(manifest.parent, row["plan_path"]))
            plan_value = json.loads(plan_path.read_text(encoding="utf-8"))
        else:
            # Isolate mutable normalization from the caller's manifest object.
            plan_value = json.loads(json.dumps(plan_value))
        program_root = Path(program_path)
        if program_root.is_file():
            program_root = program_root.parent
        for action in plan_value.get("actions", []):
            reference = action.get("reference_image")
            if reference and not Path(reference).is_absolute():
                action["reference_image"] = str((program_root / reference).resolve())
        cases.append(
            AgentCase(
                case_id=str(row["case_id"]),
                task=str(row["task"]),
                program_path=program_path,
                plan=ActionPlan.from_dict(plan_value),
                task_image_paths=[
                    _resolve(manifest.parent, value)
                    for value in row.get("task_image_paths", [])
                ],
                metadata=dict(row.get("metadata", {})),
            )
        )
    return cases


def _policy(
    name: str,
    *,
    recent_k: int = 8,
    backend=None,
    target: Path | None = None,
    model_name: str = "",
    summary_max_tokens: int = 512,
    temperature: float = 0.0,
    seed: int | None = 0,
):
    policies = {
        "full": FullHistoryPolicy,
        "frontier": FrontierPolicy,
        "guarded_frontier": GuardedFrontierPolicy,
        "conserved_frontier": ConservedFrontierPolicy,
        "guarded_frontier_text_only": GuardedFrontierTextOnlyPolicy,
        "unbound_frontier": UnboundFrontierPolicy,
        "recent": lambda: RecentPolicy(k=recent_k),
        "summary": SummaryPolicy,
        "residual": ResidualPolicy,
        "oracle": OracleEvidencePolicy,
    }
    if name == "llm_summary":
        if backend is None or target is None:
            raise ValueError("LLM Summary requires a backend and artifact directory")
        return LLMSummaryPolicy(
            backend=backend,
            artifact_dir=target / "context_policy" / "llm_summary",
            model_name=model_name,
            max_tokens=summary_max_tokens,
            temperature=temperature,
            seed=seed,
        )
    return policies[name]()


def _backend(args: argparse.Namespace):
    return create_backend(
        backend=args.backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        extra_body=json.loads(args.extra_body) if args.extra_body else None,
        command=args.command,
    )


def command_run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    cases = _load_cases(args.cases, args.limit, args.case_id)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "program_path": case.program_path,
                            "images": case.task_image_paths,
                            "actions": len(case.plan.actions),
                            "checklist": len(case.plan.checklist),
                        }
                        for case in cases
                    ],
                    "policy": args.policy,
                    "output_root": str(Path(args.output_root).resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    backend = _backend(args)
    target = Path(args.output_root).resolve() / args.policy
    selected_policy = _policy(
        args.policy,
        recent_k=args.recent_k,
        backend=backend,
        target=target,
        model_name=args.model,
        summary_max_tokens=args.summary_max_tokens,
        temperature=args.temperature,
        seed=args.seed,
    )
    _record_run_snapshot(target, args, cases)
    loop = AgentLoop(
        backend=backend,
        model_name=args.model,
        run_dir=target,
        policy=selected_policy,
        budget=ContextBudget(
            max_text_tokens=args.context_tokens,
            max_images=args.context_images,
            max_image_pixels=args.context_image_pixels,
        ),
        max_revisions=args.max_revisions,
        max_contract_retries=args.max_contract_retries,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        seed=args.seed,
        record_video=not args.no_video,
        fresh_certification=not args.reuse_historical_certification,
        source_context_mode=args.source_context,
    )
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.case_id} policy={args.policy}", flush=True)
        try:
            state = loop.run(case, resume=not args.no_resume)
            results.append({"case_id": case.case_id, **state})
        except Exception as exc:
            recovered = loop.recover_state(case.case_id)
            results.append(
                {
                    "case_id": case.case_id,
                    **recovered,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[error] {case.case_id}: {type(exc).__name__}: {exc}", flush=True)
    summary = {
        "policy": args.policy,
        "model": args.model,
        "total": len(results),
        "complete": sum(row.get("status") == "complete" for row in results),
        "error": sum(row.get("status") == "error" for row in results),
        "mean_final_score": (
            sum(float(row.get("final_score", 0.0)) for row in results) / len(results)
            if results
            else 0.0
        ),
        "generator_calls": sum(int(row.get("generator_calls", 0)) for row in results),
        "verifier_calls": sum(int(row.get("verifier_calls", 0)) for row in results),
        "context_estimated_tokens": sum(
            int(row.get("context_estimated_tokens", 0)) for row in results
        ),
        "context_images": sum(int(row.get("context_images", 0)) for row in results),
        "source_context_rendered_bytes": sum(
            int(row.get("source_context_rendered_bytes", 0)) for row in results
        ),
        "source_context_selected_files": sum(
            int(row.get("source_context_selected_files", 0)) for row in results
        ),
        "source_context_fallbacks": sum(
            int(row.get("source_context_fallbacks", 0)) for row in results
        ),
        "response_characters": sum(
            int(row.get("response_characters", 0)) for row in results
        ),
        "contract_rejections": sum(
            int(row.get("contract_rejections", 0)) for row in results
        ),
        "regression_count": sum(int(row.get("regression_count", 0)) for row in results),
        "once_correct_then_broken_count": sum(
            int(row.get("once_correct_then_broken_count", 0)) for row in results
        ),
        "stale_context_records": sum(
            int(row.get("stale_context_records", 0)) for row in results
        ),
        "stale_context_turns": sum(
            int(row.get("stale_context_turns", 0)) for row in results
        ),
        "stale_evidence_nonimproving_revision_count": sum(
            int(row.get("stale_evidence_nonimproving_revision_count", 0))
            for row in results
        ),
        "rollback_count": sum(
            int(bool(row.get("restored_best_checkpoint"))) for row in results
        ),
        "rollback_useful_count": sum(
            int(bool(row.get("rollback_useful"))) for row in results
        ),
        "fresh_certification_count": sum(
            int(bool(row.get("fresh_certification"))) for row in results
        ),
        "historical_certification_reuse_count": sum(
            int(bool(row.get("historical_result_reused"))) for row in results
        ),
        "context_policy_calls": sum(
            int(row.get("context_policy_calls", 0)) for row in results
        ),
        "context_policy_input_tokens": sum(
            int(row.get("context_policy_input_tokens", 0)) for row in results
        ),
        "context_policy_output_tokens": sum(
            int(row.get("context_policy_output_tokens", 0)) for row in results
        ),
        "context_policy_seconds": sum(
            float(row.get("context_policy_seconds", 0.0)) for row in results
        ),
        "generator_seconds": sum(
            float(row.get("generator_seconds", 0.0)) for row in results
        ),
        "verifier_seconds": sum(
            float(row.get("verifier_seconds", 0.0)) for row in results
        ),
        "runner_wall_seconds": time.monotonic() - started,
        "results": results,
    }
    summary["allocated_gpu_hours"] = (
        float(summary["runner_wall_seconds"]) * int(args.allocated_gpus) / 3600.0
    )
    summary_path = target / "summary.json"
    previous_summary = read_json(summary_path) if summary_path.exists() else None
    summary["invocation_wall_seconds"] = summary["runner_wall_seconds"]
    summary["invocation_index"] = (
        int(previous_summary.get("invocation_index", 0)) + 1
        if previous_summary
        else 1
    )
    summary["first_invocation_wall_seconds"] = (
        float(
            previous_summary.get(
                "first_invocation_wall_seconds",
                previous_summary.get("runner_wall_seconds", 0.0),
            )
        )
        if previous_summary
        else float(summary["runner_wall_seconds"])
    )
    history_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    write_json(target / "summary_history" / f"{history_timestamp}.json", summary)
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["error"] else 0


def command_judge(args: argparse.Namespace) -> int:
    cases = _load_cases(args.cases, args.limit, args.case_id)
    target = Path(args.output_root).resolve()
    judge = InteractiveJudge(target)
    results = []
    for case in cases:
        results.append(
            judge.execute(
                case_id=case.case_id,
                program_path=case.program_path,
                plan=case.plan,
                purpose="judge-only",
                record_video=not args.no_video,
            )
        )
    write_json(target / "judge_summary.json", {"results": results})
    print(json.dumps({"scores": [row["score"] for row in results]}, indent=2))
    return 0


def command_plan(args: argparse.Namespace) -> int:
    backend = _backend(args)
    checklist = None
    if args.checklist:
        checklist_value = json.loads(Path(args.checklist).read_text(encoding="utf-8"))
        checklist = (
            checklist_value.get("checklist", [])
            if isinstance(checklist_value, dict)
            else checklist_value
        )
        if not isinstance(checklist, list):
            raise ValueError("--checklist must contain a JSON list or a checklist field")
    output = Path(args.output).resolve()
    planner = OneShotActionPlanner(
        backend=backend,
        model_name=args.model,
        run_dir=output.parent / "planner_artifacts",
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        seed=args.seed,
    )
    plan = planner.plan(
        case_id=args.case_id,
        task=args.task,
        program_path=args.program_path,
        task_image_paths=args.task_image,
        benchmark_checklist=checklist,
        resume=not args.no_resume,
    )
    write_json(output, plan.to_dict())
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MultimodalCode research harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run revision experiments")
    run.add_argument("--cases", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument(
        "--policy",
        choices=(
            "full",
            "recent",
            "summary",
            "residual",
            "frontier",
            "guarded_frontier",
            "conserved_frontier",
            "guarded_frontier_text_only",
            "unbound_frontier",
            "llm_summary",
            "oracle",
        ),
        required=True,
    )
    run.add_argument("--backend", default="vllm")
    run.add_argument("--model", required=True)
    run.add_argument("--base-url")
    run.add_argument("--api-key")
    run.add_argument("--command")
    run.add_argument("--extra-body")
    run.add_argument("--limit", type=int)
    run.add_argument("--case-id", action="append", default=[])
    run.add_argument("--max-revisions", type=int, default=2)
    run.add_argument(
        "--max-contract-retries",
        type=int,
        default=1,
        help="Maximum fixed admission retries for each semantic revision",
    )
    run.add_argument("--max-tokens", type=int, default=8192)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument(
        "--no-video",
        action="store_true",
        help="Disable browser video for fast diagnostics; full audit runs record it by default",
    )
    run.add_argument(
        "--reuse-historical-certification",
        action="store_true",
        help=(
            "Ablation only: submit a version-bound historical verifier result "
            "instead of executing the mandatory fresh final replay"
        ),
    )
    run.add_argument("--context-tokens", type=int, default=4096)
    run.add_argument("--context-images", type=int, default=4)
    run.add_argument("--context-image-pixels", type=int, default=8_000_000)
    run.add_argument(
        "--source-context",
        choices=("full", "execution_rooted"),
        default="full",
        help=(
            "Select all renderable repository source or only complete source files "
            "observed on the browser's same-origin loaded-resource graph"
        ),
    )
    run.add_argument("--recent-k", type=int, default=8)
    run.add_argument(
        "--summary-max-tokens",
        type=int,
        default=512,
        help="Maximum output tokens for each separately reported LLM Summary call",
    )
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--allocated-gpus", type=int, default=0)
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(function=command_run)

    judge = subparsers.add_parser("judge", help="Replay frozen plans without a generator")
    judge.add_argument("--cases", required=True)
    judge.add_argument("--output-root", required=True)
    judge.add_argument("--limit", type=int)
    judge.add_argument("--case-id", action="append", default=[])
    judge.add_argument("--no-video", action="store_true")
    judge.set_defaults(function=command_judge)

    plan = subparsers.add_parser(
        "plan", help="Generate one complete frozen interaction plan from initial page state"
    )
    plan.add_argument("--case-id", required=True)
    plan.add_argument("--task", required=True)
    plan.add_argument("--program-path", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--checklist")
    plan.add_argument("--task-image", action="append", default=[])
    plan.add_argument("--backend", default="vllm")
    plan.add_argument("--model", required=True)
    plan.add_argument("--base-url")
    plan.add_argument("--api-key")
    plan.add_argument("--command")
    plan.add_argument("--extra-body")
    plan.add_argument("--max-tokens", type=int, default=4096)
    plan.add_argument("--temperature", type=float, default=0.0)
    plan.add_argument("--seed", type=int, default=0)
    plan.add_argument("--timeout", type=float, default=600)
    plan.add_argument("--no-resume", action="store_true")
    plan.set_defaults(function=command_plan)
    return parser


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_snapshot() -> Dict[str, Any]:
    paths = [
        PROJECT_ROOT / "research_run.py",
        PROJECT_ROOT / "scripts" / "interactive_judge_playwright.js",
    ]
    paths.extend(sorted((SRC_ROOT / "multimodalcode" / "research").glob("*.py")))
    rows = {
        str(path.relative_to(PROJECT_ROOT)): _sha256(path)
        for path in paths
        if path.is_file()
    }
    combined = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"combined_sha256": combined, "files": rows}


def _redacted_command(argv: Iterable[str]) -> List[str]:
    values = list(argv)
    redacted: List[str] = []
    hide_next = False
    for value in values:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
        elif value == "--api-key":
            redacted.append(value)
            hide_next = True
        elif value.startswith("--api-key="):
            redacted.append("--api-key=<redacted>")
        else:
            redacted.append(value)
    return redacted


def _record_run_snapshot(
    target: Path, args: argparse.Namespace, cases: List[AgentCase]
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    snapshot = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": _redacted_command(sys.argv),
        "python": sys.executable,
        "backend": args.backend,
        "model": args.model,
        "base_url": args.base_url,
        "api_key_configured": bool(args.api_key),
        "extra_body": json.loads(args.extra_body) if args.extra_body else None,
        "policy": args.policy,
        "seed": args.seed,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "max_revisions": args.max_revisions,
        "max_contract_retries": args.max_contract_retries,
        "context_budget": {
            "text_tokens": args.context_tokens,
            "images": args.context_images,
            "image_pixels": args.context_image_pixels,
        },
        "source_context": args.source_context,
        "summary_max_tokens": args.summary_max_tokens,
        "record_video": not args.no_video,
        "fresh_certification": not args.reuse_historical_certification,
        "allocated_gpus": args.allocated_gpus,
        "cases_manifest": str(Path(args.cases).resolve()),
        "cases_manifest_sha256": _sha256(Path(args.cases).resolve()),
        "case_ids": [case.case_id for case in cases],
        "source_snapshot": _source_snapshot(),
    }
    primary = target / "run_config.json"
    if not primary.exists():
        write_json(primary, snapshot)
    history = target / "run_snapshots" / f"{timestamp}.json"
    write_json(history, snapshot)


def main() -> int:
    args = build_parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())

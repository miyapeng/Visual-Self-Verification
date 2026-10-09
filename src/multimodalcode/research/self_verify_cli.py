#!/usr/bin/env python3
"""Opt-in runner for the same-policy self-verification pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodalcode.backends import create_backend
from multimodalcode.io import safe_name, write_json
from multimodalcode.research.self_verify import (
    PublicSelfVerifyCase,
    SamePolicySelfVerifyLoop,
    SelfVerifyBudget,
)


def _rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix == ".jsonl":
        values = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        values = value if isinstance(value, list) else value.get("cases", [value])
    if not isinstance(values, list) or not all(isinstance(row, dict) for row in values):
        raise ValueError("Case manifest must contain a list of objects")
    return values


def load_cases(path: str | Path, case_ids: List[str], limit: int | None) -> List[PublicSelfVerifyCase]:
    manifest = Path(path).resolve()
    selected = set(case_ids)
    rows = _rows(manifest)
    archived = [
        str(row.get("case_id"))
        for row in rows
        if str(row.get("benchmark", "")).lower()
        in {"interactweb", "interactweb_bench", "interactweb-bench"}
    ]
    if archived:
        raise ValueError(
            "InteractWeb-Bench is archived and outside the current paper scope; "
            f"refusing cases {archived}. See reports/research_scope_decision.md"
        )
    if selected:
        rows = [row for row in rows if str(row.get("case_id")) in selected]
        found = {str(row.get("case_id")) for row in rows}
        missing = sorted(selected - found)
        if missing:
            raise ValueError(f"Unknown --case-id values: {missing}")
    if limit is not None:
        rows = rows[:limit]
    return [
        PublicSelfVerifyCase.from_dict(row, base_dir=manifest.parent) for row in rows
    ]


def run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases, args.case_id, args.limit)
    if not args.enable_self_verification:
        print(
            json.dumps(
                {
                    "status": "disabled",
                    "reason": "same-policy loop is opt-in; pass --enable-self-verification",
                    "case_count": len(cases),
                    "baseline_modified": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    archived_vision2web = [
        case.case_id for case in cases if case.benchmark.casefold() == "vision2web"
    ]
    if archived_vision2web and not args.allow_archived_forced_vision2web_loop:
        raise RuntimeError(
            "The legacy externally orchestrated Vision2Web planner/judge/repair loop "
            "is archived and is not the active self-verifying coding-agent method. "
            "Use scripts/agents/run.py with --vision2web-mode tools|self_verify. Historical "
            "reproduction only may pass --allow-archived-forced-vision2web-loop. "
            f"Refusing cases: {archived_vision2web}"
        )
    output = Path(args.output_root).resolve()
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise RuntimeError(f"Refusing to write into non-empty output without --resume: {output}")
    output.mkdir(parents=True, exist_ok=True)
    budget = SelfVerifyBudget(
        max_model_calls=args.max_model_calls,
        max_browser_actions=args.max_browser_actions,
        max_revisions=args.max_revisions,
        max_schema_retries=args.max_schema_retries,
        max_checks=args.max_checks,
        max_actions_per_check=args.max_actions_per_check,
        max_evidence_images=args.max_evidence_images,
        max_patch_edits=args.max_patch_edits,
        max_patch_chars=args.max_patch_chars,
        browser_timeout_ms=args.browser_timeout_ms,
    )
    run_config = {
        "enabled": True,
        "backend": args.backend,
        "model": args.model,
        "base_url": args.base_url,
        "api_key_configured": bool(args.api_key),
        "extra_body": json.loads(args.extra_body) if args.extra_body else None,
        "budget": budget.__dict__,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "seed": args.seed,
        "record_video": not args.no_video,
        "cases_manifest": str(Path(args.cases).resolve()),
        "case_ids": [case.case_id for case in cases],
        "official_evaluator_visible": False,
    }
    config_path = output / "run_config.json"
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != run_config:
            raise RuntimeError("Refusing to resume with a different frozen run configuration")
    else:
        write_json(config_path, run_config)
    backend = create_backend(
        backend=args.backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        extra_body=json.loads(args.extra_body) if args.extra_body else None,
        command=args.command,
    )
    results = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case.benchmark}:{case.case_id}", flush=True)
        case_run = output / "case_runs" / safe_name(case.case_id)
        loop = SamePolicySelfVerifyLoop(
            backend=backend,
            model_name=args.model,
            run_dir=case_run,
            budget=budget,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            seed=args.seed,
            record_video=not args.no_video,
        )
        try:
            result = loop.run(case)
        except Exception as exc:
            result_path = case_run / "cases" / safe_name(case.case_id) / "result.json"
            result = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file()
                else {
                    "case_id": case.case_id,
                    "benchmark": case.benchmark,
                    "status": "error",
                    "termination_reason": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[error] {case.case_id}: {type(exc).__name__}: {exc}", flush=True)
        results.append(result)
        write_json(output / "per_case" / f"{safe_name(case.case_id)}.json", result)
    summary = {
        "schema": "multimodalcode-same-policy-self-verify-summary-1",
        "model": args.model,
        "same_policy": True,
        "official_evaluator_visible": False,
        "total": len(results),
        "complete": sum(row.get("status") == "complete" for row in results),
        "error": sum(row.get("status") == "error" for row in results),
        "initial_all_pass": sum(
            row.get("termination_reason") == "all_checks_passed_initial_version"
            for row in results
        ),
        "repaired_all_pass": sum(
            row.get("termination_reason") == "all_checks_passed_after_repair"
            for row in results
        ),
        "accepted_patch_count": sum(
            sum(bool(patch.get("acceptance", {}).get("accepted")) for patch in row.get("patches", []))
            for row in results
        ),
        "model_calls": sum(int(row.get("cost", {}).get("model_calls", 0)) for row in results),
        "browser_actions": sum(int(row.get("browser_actions", 0)) for row in results),
        "results": [
            {
                "case_id": row.get("case_id"),
                "benchmark": row.get("benchmark"),
                "status": row.get("status"),
                "termination_reason": row.get("termination_reason"),
                "initial_version": row.get("initial_version"),
                "accepted_version": row.get("accepted_version"),
                "model_calls": row.get("cost", {}).get("model_calls", 0),
                "browser_actions": row.get("browser_actions", 0),
            }
            for row in results
        ],
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["error"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in same-policy self-verification pilot")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--enable-self-verification", action="store_true")
    parser.add_argument(
        "--allow-archived-forced-vision2web-loop",
        action="store_true",
        help=(
            "Historical reproduction only: enable the archived external "
            "Vision2Web planner/judge/repair loop. It is not the active method."
        ),
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--backend", default="vllm")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--command")
    parser.add_argument("--extra-body")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-model-calls", type=int, default=9)
    parser.add_argument("--max-browser-actions", type=int, default=48)
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--max-schema-retries", type=int, default=1)
    parser.add_argument("--max-checks", type=int, default=6)
    parser.add_argument("--max-actions-per-check", type=int, default=8)
    parser.add_argument("--max-evidence-images", type=int, default=6)
    parser.add_argument("--max-patch-edits", type=int, default=4)
    parser.add_argument("--max-patch-chars", type=int, default=24_000)
    parser.add_argument("--browser-timeout-ms", type=int, default=30_000)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.set_defaults(function=run)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())

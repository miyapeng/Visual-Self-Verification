#!/usr/bin/env python3
"""Run a leakage-safe post-generation baseline/generic/full pilot comparison.

This is deliberately not an official benchmark evaluator.  It replays the one
frozen public-obligation browser plan produced by the full method on three
versions derived from the same first complete program:

* baseline: no post-generation revision;
* generic: one source-only "verify and fix" instruction to the same policy;
* full: the accepted version from the same-policy interactive loop.

The same frozen model configuration judges all three replays.  The resulting
measurement is diagnostic and correlated with the generator; official
evaluators remain post-hoc and hidden.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodalcode.backends import create_backend
from multimodalcode.io import safe_name, write_json
from multimodalcode.research.interactive import InteractiveJudge, hash_program
from multimodalcode.research.self_verify import (
    InteractionPlan,
    ObligationSet,
    PublicSelfVerifyCase,
    SamePolicySelfVerifyLoop,
    SelfVerifyBudget,
    _acceptance,
    _bounded_images,
)
from multimodalcode.research.tools import SafeFileToolExecutor, parse_revision


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects: {path}")
    return rows


def _cases(path: Path) -> dict[str, PublicSelfVerifyCase]:
    return {
        str(row["case_id"]): PublicSelfVerifyCase.from_dict(row, base_dir=path.parent)
        for row in _read_rows(path)
    }


def _comparison_eligibility(
    result: Mapping[str, Any], *, obligations_path: Path
) -> tuple[bool, str]:
    """Admit any safely reconstructed frozen-plan outcome, including failures.

    Requiring ``status=complete`` would create survivorship bias by dropping a
    full-method case merely because its later repair contract exhausted.  A
    case is comparable when its plan and obligations were frozen and its final
    workspace is provably the accepted checkpoint.
    """

    if len(list(result.get("plans") or [])) != 1:
        return False, "requires_exactly_one_frozen_plan"
    if not obligations_path.is_file():
        return False, "frozen_obligations_missing"
    initial = result.get("initial_version")
    accepted = result.get("accepted_version")
    final = result.get("final_program_version")
    if not all(isinstance(value, str) and value for value in (initial, accepted, final)):
        return False, "program_version_binding_missing"
    if final != accepted:
        return False, "final_workspace_not_bound_to_accepted_checkpoint"
    return True, "eligible"


def _rebuild_full(
    *,
    source: Path,
    destination: Path,
    checkpoint_root: Path,
    result: Mapping[str, Any],
) -> SafeFileToolExecutor:
    tool = SafeFileToolExecutor.prepare(source, destination, checkpoint_root)
    if hash_program(tool.workspace) != result.get("initial_version"):
        raise ValueError("Initial program hash does not match the frozen full-method result")
    for patch in result.get("patches", []):
        if not (patch.get("acceptance") or {}).get("accepted"):
            continue
        revision = patch.get("revision_contract") or {}
        tool.apply_revision_with_admission(
            files=dict(revision.get("files") or {}),
            edits=list(revision.get("edits") or []),
        )
        expected = patch.get("candidate_version")
        if expected and hash_program(tool.workspace) != expected:
            raise ValueError("Reconstructed accepted patch hash mismatch")
    if hash_program(tool.workspace) != result.get("accepted_version"):
        raise ValueError("Reconstructed full version does not match accepted_version")
    return tool


def _generic_revision(
    *,
    loop: SamePolicySelfVerifyLoop,
    case: PublicSelfVerifyCase,
    tool: SafeFileToolExecutor,
) -> dict[str, Any]:
    source, source_manifest = tool.render_program_context(max_bytes=200_000)
    prompt = f"""Public user task:\n{case.task}\n\nCurrent source files:\n{source}\n\nPlease verify your work yourself and fix any issues before submitting.\n\nReturn one localized revision JSON. Existing files must use exact edits: {{\"decision\":\"patch\",\"edits\":[{{\"path\":\"relative/path\",\"old\":\"exact unique text\",\"new\":\"replacement\"}}],\"files\":{{}},\"reason\":\"...\"}}. The files object may only create new files. You may return keep or stop. Do not modify tests or evaluator artifacts. Use at most {loop.budget.max_patch_edits} edits and at most {loop.budget.max_patch_chars} total patch characters."""
    error = ""
    revision: dict[str, Any] | None = None
    for attempt in range(loop.budget.max_schema_retries + 1):
        response = loop.policy.call(
            stage=(
                "generic-sentence-repair"
                if attempt == 0
                else f"generic-sentence-repair-contract-retry-{attempt}"
            ),
            prompt=prompt + ("" if not error else f"\n\nPrevious contract error: {error}. Correct it."),
            image_paths=_bounded_images(
                case.task_image_paths, loop.budget.max_evidence_images
            ),
            system_prompt=(
                "You are the frozen multimodal coding policy. Follow the generic request and "
                "return only the typed revision JSON."
            ),
        )
        try:
            revision = parse_revision(
                response,
                max_edits=loop.budget.max_patch_edits,
                max_patch_chars=loop.budget.max_patch_chars,
            )
            if revision["decision"] == "patch":
                before = hash_program(tool.workspace)
                written, rejected = tool.apply_revision_with_admission(
                    files=revision["files"], edits=revision["edits"]
                )
                if hash_program(tool.workspace) == before:
                    raise ValueError("Generic patch did not create a new program version")
                revision["written_files"] = written
                revision["rejected_existing_file_payloads"] = rejected
            break
        except (FileNotFoundError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            revision = None
    if revision is None:
        raise ValueError(f"Generic sentence condition failed its bounded contract: {error}")
    return {
        "revision": revision,
        "source_manifest": source_manifest,
        "version": hash_program(tool.workspace),
    }


def _condition_result(judgment: Any, version: str) -> dict[str, Any]:
    statuses = judgment.statuses()
    return {
        "status": "complete",
        "version": version,
        "checks": judgment.to_dict()["checks"],
        "pass_count": sum(status == "pass" for status in statuses.values()),
        "fail_count": sum(status == "fail" for status in statuses.values()),
        "all_pass": judgment.all_pass(),
    }


def _paired_acceptance(
    judgments: Mapping[str, Any], *, baseline: str, candidate: str
) -> dict[str, Any]:
    if baseline not in judgments or candidate not in judgments:
        missing = [name for name in (baseline, candidate) if name not in judgments]
        return {
            "available": False,
            "missing_conditions": missing,
            "accepted": None,
            "repaired_checks": [],
            "regressed_checks": [],
        }
    return {
        "available": True,
        **_acceptance(judgments[baseline], judgments[candidate]),
    }


def compare_case(
    *,
    case: PublicSelfVerifyCase,
    full_result: Mapping[str, Any],
    output: Path,
    backend: Any,
    model: str,
    budget: SelfVerifyBudget,
    max_tokens: int,
    temperature: float,
    seed: int,
) -> dict[str, Any]:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(case.program_path)
    obligations_path = (
        Path(full_result["_case_run"])
        / "cases"
        / safe_name(case.case_id)
        / "obligations.json"
    )
    obligations = ObligationSet.from_dict(
        _read_json(obligations_path), max_checks=budget.max_checks
    )
    plans = list(full_result.get("plans") or [])
    if len(plans) != 1:
        raise ValueError("Comparison requires exactly one frozen full-method plan")
    plan = InteractionPlan.from_dict(
        {"checks": plans[0]["checks"]}, obligations=obligations, budget=budget
    )
    if plan.action_count > budget.max_browser_actions // 3:
        raise ValueError("Frozen plan exceeds the per-condition browser budget")

    tools = {
        "baseline": SafeFileToolExecutor.prepare(
            source, output / "conditions" / "baseline" / "workspace", output / "checkpoints" / "baseline"
        ),
        "generic": SafeFileToolExecutor.prepare(
            source, output / "conditions" / "generic" / "workspace", output / "checkpoints" / "generic"
        ),
        "full": _rebuild_full(
            source=source,
            destination=output / "conditions" / "full" / "workspace",
            checkpoint_root=output / "checkpoints" / "full",
            result=full_result,
        ),
    }
    initial_version = str(full_result["initial_version"])
    if any(hash_program(tools[name].workspace) != initial_version for name in ("baseline", "generic")):
        raise ValueError("Fresh comparison workspaces do not match initial_version")

    loop = SamePolicySelfVerifyLoop(
        backend=backend,
        model_name=model,
        run_dir=output / "semantic_policy",
        budget=budget,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
        record_video=False,
        interactive_judge=InteractiveJudge(output / "browser"),
    )
    generic: dict[str, Any] | None = None
    generic_error: str | None = None
    try:
        generic = _generic_revision(loop=loop, case=case, tool=tools["generic"])
    except Exception as exc:
        # A bounded generic-baseline failure is a measurement outcome, not a
        # reason to suppress the independently runnable baseline/full arms.
        generic_error = f"{type(exc).__name__}: {exc}"
    versions = {
        "baseline": hash_program(tools["baseline"].workspace),
        "generic": hash_program(tools["generic"].workspace),
        "full": hash_program(tools["full"].workspace),
    }
    conditions: dict[str, Any] = {}
    judgments: dict[str, Any] = {}
    plan_digest = plans[0].get("plan_sha256")
    for name in ("baseline", "generic", "full"):
        if name == "generic" and generic_error is not None:
            conditions[name] = {
                "status": "error",
                "version": versions[name],
                "error": generic_error,
                "stage": "generic_revision_contract",
                "plan_sha256": plan_digest,
                "browser_actions": 0,
            }
            continue
        execution: dict[str, Any] | None = None
        try:
            loop._charge_browser_actions(plan.action_count)
            execution = loop.judge.execute(
                case_id=f"{case.case_id}::{name}",
                program_path=tools[name].workspace,
                plan=plan.to_executor_plan(model_name=model, web_runtime=case.web_runtime),
                purpose=f"controlled-comparison-{name}",
                timeout_ms=budget.browser_timeout_ms,
                record_video=False,
            )
            judgment, evidence_index = loop._judge(
                case, obligations, plan, execution
            )
            judgments[name] = judgment
            conditions[name] = {
                **_condition_result(judgment, versions[name]),
                "execution_id": execution.get("execution_id"),
                "plan_sha256": plan_digest,
                "browser_actions": len(execution.get("actions", [])),
                "evidence_index": evidence_index,
            }
        except Exception as exc:
            conditions[name] = {
                "status": "error",
                "version": versions[name],
                "error": f"{type(exc).__name__}: {exc}",
                "stage": "frozen_plan_execution_or_judgment",
                "plan_sha256": plan_digest,
                "execution_completed": execution is not None,
                "execution_id": (
                    execution.get("execution_id") if execution is not None else None
                ),
                "execution_result_relative_path": (
                    execution.get("result_relative_path")
                    if execution is not None
                    else None
                ),
                "browser_actions": (
                    len(execution.get("actions", []))
                    if execution is not None
                    else 0
                ),
            }

    if generic is not None:
        conditions["generic"]["revision"] = generic["revision"]
        conditions["generic"]["source_manifest"] = generic["source_manifest"]
    conditions["generic"]["changed_from_baseline"] = versions["generic"] != versions["baseline"]
    conditions["full"]["changed_from_baseline"] = versions["full"] != versions["baseline"]
    comparisons = {
        "generic_vs_baseline": _paired_acceptance(
            judgments, baseline="baseline", candidate="generic"
        ),
        "full_vs_baseline": _paired_acceptance(
            judgments, baseline="baseline", candidate="full"
        ),
    }
    condition_error_count = sum(
        row.get("status") != "complete" for row in conditions.values()
    )
    result = {
        "schema": "multimodalcode-post-generation-controlled-comparison-1",
        "case_id": case.case_id,
        "benchmark": case.benchmark,
        "status": "complete" if condition_error_count == 0 else "partial",
        "condition_error_count": condition_error_count,
        "model": model,
        "same_base_policy": True,
        "official_evaluator_visible": False,
        "measurement": "same-policy frozen-plan diagnostic; not an official score",
        "initial_version": initial_version,
        "full_source_result": full_result.get("_result_path"),
        "source_full_method_status": full_result.get("status"),
        "source_full_method_termination_reason": full_result.get("termination_reason"),
        "budget": budget.__dict__,
        "conditions": conditions,
        "comparisons": comparisons,
        "cost": loop.policy.costs(),
        "wall_seconds": time.monotonic() - started,
    }
    write_json(output / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--full-run", required=True)
    parser.add_argument("--source-full-run-label", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--backend", default="vllm")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--extra-body")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-schema-retries", type=int, default=1)
    parser.add_argument("--max-checks", type=int, default=6)
    parser.add_argument("--max-actions-per-check", type=int, default=8)
    parser.add_argument("--max-evidence-images", type=int, default=6)
    parser.add_argument("--max-patch-edits", type=int, default=4)
    parser.add_argument("--max-patch-chars", type=int, default=24_000)
    parser.add_argument("--browser-timeout-ms", type=int, default=10_000)
    args = parser.parse_args()

    output = Path(args.output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing non-empty comparison output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    full_run = Path(args.full_run).resolve()
    summary = _read_json(full_run / "results" / "summary.json")
    public_cases = _cases(Path(args.cases).resolve())
    selected: list[tuple[PublicSelfVerifyCase, dict[str, Any]]] = []
    selection: list[dict[str, Any]] = []
    for row in summary.get("results", []):
        case_id = str(row.get("case_id"))
        result_path = full_run / "results" / "per_case" / f"{safe_name(case_id)}.json"
        result = _read_json(result_path)
        case_run = full_run / "results" / "case_runs" / safe_name(case_id)
        obligations_path = case_run / "cases" / safe_name(case_id) / "obligations.json"
        eligible, reason = _comparison_eligibility(
            result, obligations_path=obligations_path
        )
        selection.append(
            {
                "case_id": case_id,
                "benchmark": row.get("benchmark"),
                "source_status": result.get("status"),
                "eligible": eligible,
                "reason": reason,
            }
        )
        if not eligible:
            continue
        result["_case_run"] = str(case_run)
        result["_result_path"] = str(result_path)
        selected.append((public_cases[case_id], result))
    if not selected:
        raise ValueError("No safely reconstructable frozen-plan cases are available")

    budget = SelfVerifyBudget(
        max_model_calls=8,
        max_browser_actions=144,
        max_revisions=0,
        max_schema_retries=args.max_schema_retries,
        max_checks=args.max_checks,
        max_actions_per_check=args.max_actions_per_check,
        max_evidence_images=args.max_evidence_images,
        max_patch_edits=args.max_patch_edits,
        max_patch_chars=args.max_patch_chars,
        browser_timeout_ms=args.browser_timeout_ms,
    )
    backend = create_backend(
        backend=args.backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        extra_body=json.loads(args.extra_body) if args.extra_body else None,
    )
    results: list[dict[str, Any]] = []
    for case, full_result in selected:
        print(f"[compare] {case.benchmark}:{case.case_id}", flush=True)
        case_output = output / "cases" / safe_name(case.case_id)
        try:
            result = compare_case(
                case=case,
                full_result=full_result,
                output=case_output,
                backend=backend,
                model=args.model,
                budget=budget,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                seed=args.seed,
            )
        except Exception as exc:
            result = {
                "schema": "multimodalcode-post-generation-controlled-comparison-1",
                "case_id": case.case_id,
                "benchmark": case.benchmark,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "official_evaluator_visible": False,
            }
            write_json(case_output / "result.json", result)
            print(f"[error] {case.case_id}: {result['error']}", flush=True)
        results.append(result)
        write_json(output / "per_case" / f"{safe_name(case.case_id)}.json", result)

    usable = [
        row for row in results if row.get("status") in {"complete", "partial"}
    ]
    complete = [row for row in results if row.get("status") == "complete"]
    partial = [row for row in results if row.get("status") == "partial"]
    setup_errors = [row for row in results if row.get("status") == "error"]
    aggregate = {
        "schema": "multimodalcode-post-generation-controlled-comparison-summary-1",
        "source_full_run": str(full_run),
        "source_full_run_label": args.source_full_run_label,
        "model": args.model,
        "official_evaluator_visible": False,
        "measurement": "same-policy frozen-plan diagnostic; not an official score",
        "selection_rule": (
            "exactly one frozen plan plus obligations and final workspace bound to accepted "
            "checkpoint; source error outcomes retained to avoid survivorship bias"
        ),
        "source_case_selection": selection,
        "total": len(results),
        "complete": len(complete),
        "partial": len(partial),
        "error": len(setup_errors),
        "conditions": {
            name: {
                "coverage": sum(
                    row["conditions"][name].get("status") == "complete"
                    for row in usable
                ),
                "all_pass": sum(
                    bool(row["conditions"][name].get("all_pass"))
                    for row in usable
                    if row["conditions"][name].get("status") == "complete"
                ),
                "check_passes": sum(
                    int(row["conditions"][name]["pass_count"])
                    for row in usable
                    if row["conditions"][name].get("status") == "complete"
                ),
                "check_total": sum(
                    int(row["conditions"][name]["pass_count"])
                    + int(row["conditions"][name]["fail_count"])
                    for row in usable
                    if row["conditions"][name].get("status") == "complete"
                ),
            }
            for name in ("baseline", "generic", "full")
        },
        "generic_paired_coverage": sum(
            bool(row["comparisons"]["generic_vs_baseline"]["available"])
            for row in usable
        ),
        "generic_improved_no_regression": sum(
            bool(row["comparisons"]["generic_vs_baseline"]["accepted"])
            for row in usable
            if row["comparisons"]["generic_vs_baseline"]["available"]
        ),
        "full_paired_coverage": sum(
            bool(row["comparisons"]["full_vs_baseline"]["available"])
            for row in usable
        ),
        "full_improved_no_regression": sum(
            bool(row["comparisons"]["full_vs_baseline"]["accepted"])
            for row in usable
            if row["comparisons"]["full_vs_baseline"]["available"]
        ),
        "results": [
            {
                "case_id": row.get("case_id"),
                "benchmark": row.get("benchmark"),
                "status": row.get("status"),
                "error": row.get("error"),
            }
            for row in results
        ],
    }
    write_json(output / "summary.json", aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 1 if aggregate["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

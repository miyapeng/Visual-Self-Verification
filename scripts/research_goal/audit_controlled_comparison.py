#!/usr/bin/env python3
"""Structurally audit a persisted baseline/generic/full comparison.

The comparison runner may finish browser execution and then reject the policy's
judgment contract.  This audit therefore reads the immutable browser result
artifacts directly instead of equating a missing semantic score with zero
browser actions.  It never invokes a model or an official benchmark evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HARD_CONTEXT_RE = re.compile(
    r"maximum context length|context window exceeded|prompt.{0,40}too long|too many tokens",
    re.IGNORECASE | re.DOTALL,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _execution_rows(case_root: Path, condition: str) -> list[dict[str, Any]]:
    purpose = f"controlled-comparison-{condition}"
    rows: list[dict[str, Any]] = []
    execution_root = case_root / "browser" / "executions"
    if not execution_root.is_dir():
        return rows
    for path in sorted(execution_root.rglob("result.json")):
        row = _read_json(path)
        if row.get("purpose") != purpose:
            continue
        rows.append(
            {
                "execution_id": row.get("execution_id"),
                "status": row.get("status"),
                "code_version": row.get("code_version"),
                "action_count": len(list(row.get("actions") or [])),
                "failed_action_count": sum(
                    action.get("status") == "fail"
                    for action in list(row.get("actions") or [])
                    if isinstance(action, dict)
                ),
                "result": str(path.relative_to(case_root)),
                "result_sha256": _sha256(path),
            }
        )
    return rows


def _audit_case(result_path: Path) -> dict[str, Any]:
    result = _read_json(result_path)
    case_root = result_path.parents[1] / "cases" / result_path.stem
    conditions = result.get("conditions") or {}
    violations: list[str] = []
    if result.get("same_base_policy") is not True:
        violations.append("same_base_policy_not_true")
    if result.get("official_evaluator_visible") is not False:
        violations.append("official_evaluator_not_excluded")
    if (conditions.get("baseline") or {}).get("version") != result.get("initial_version"):
        violations.append("baseline_version_not_initial")

    plan_digests = {
        row.get("plan_sha256")
        for row in conditions.values()
        if isinstance(row, dict) and row.get("plan_sha256")
    }
    if len(plan_digests) != 1:
        violations.append("conditions_do_not_share_one_plan_digest")

    audited_conditions: dict[str, Any] = {}
    for name in ("baseline", "generic", "full"):
        condition = conditions.get(name) or {}
        executions = _execution_rows(case_root, name)
        if len(executions) > 1:
            violations.append(f"{name}_has_multiple_executions")
        if condition.get("status") == "complete" and len(executions) != 1:
            violations.append(f"{name}_complete_without_one_execution")
        if executions and executions[0].get("code_version") != condition.get("version"):
            violations.append(f"{name}_execution_version_mismatch")
        audited_conditions[name] = {
            "semantic_status": condition.get("status"),
            "version": condition.get("version"),
            "pass_count": condition.get("pass_count"),
            "fail_count": condition.get("fail_count"),
            "semantic_error": condition.get("error"),
            "execution_count": len(executions),
            "executions": executions,
        }

    for comparison, candidate in (
        ("generic_vs_baseline", "generic"),
        ("full_vs_baseline", "full"),
    ):
        paired = (result.get("comparisons") or {}).get(comparison) or {}
        expected = all(
            (conditions.get(name) or {}).get("status") == "complete"
            for name in ("baseline", candidate)
        )
        if bool(paired.get("available")) != expected:
            violations.append(f"{comparison}_availability_mismatch")

    calls_path = case_root / "semantic_policy" / "model_calls.jsonl"
    calls = []
    if calls_path.is_file():
        calls = [
            json.loads(line)
            for line in calls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return {
        "case_id": result.get("case_id"),
        "benchmark": result.get("benchmark"),
        "result_status": result.get("status"),
        "structurally_valid": not violations,
        "violations": violations,
        "one_plan_sha256": next(iter(plan_digests)) if len(plan_digests) == 1 else None,
        "conditions": audited_conditions,
        "model_call_count": len(calls),
        "max_single_text_input_tokens_estimated": max(
            (int(row.get("input_tokens_estimated") or 0) for row in calls),
            default=0,
        ),
        "model_calls_sha256": _sha256(calls_path) if calls_path.is_file() else None,
        "source_result": str(result_path),
        "source_result_sha256": _sha256(result_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    run = Path(args.run).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    source_summary = _read_json(run / "results" / "summary.json")
    rows = [
        _audit_case(path)
        for path in sorted((run / "results" / "per_case").glob("*.json"))
    ]
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (run / "vllm.log", run / "runner.log", run / "job.log")
        if path.is_file()
    )
    summary = {
        "schema": "multimodalcode-controlled-comparison-structural-audit-1",
        "run": str(run),
        "measurement": "structural audit only; no official evaluator",
        "source_summary_sha256": _sha256(run / "results" / "summary.json"),
        "input_manifest_sha256": (
            _sha256(run / "full-run-input-hashes.sha256")
            if (run / "full-run-input-hashes.sha256").is_file()
            else None
        ),
        "source_total": source_summary.get("total"),
        "audited_total": len(rows),
        "structurally_valid": sum(row["structurally_valid"] for row in rows),
        "hard_context_error_in_logs": bool(HARD_CONTEXT_RE.search(log_text)),
        "max_single_text_input_tokens_estimated": max(
            (row["max_single_text_input_tokens_estimated"] for row in rows),
            default=0,
        ),
        "conditions": {
            name: {
                "semantic_coverage": sum(
                    row["conditions"][name]["semantic_status"] == "complete"
                    for row in rows
                ),
                "execution_coverage": sum(
                    row["conditions"][name]["execution_count"] == 1
                    for row in rows
                ),
                "browser_actions": sum(
                    sum(execution["action_count"] for execution in row["conditions"][name]["executions"])
                    for row in rows
                ),
            }
            for name in ("baseline", "generic", "full")
        },
        "cases": rows,
    }
    (output / "audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(rows) == source_summary.get("total") and all(row["structurally_valid"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

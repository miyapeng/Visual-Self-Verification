#!/usr/bin/env python3
"""Audit a persisted same-policy run without invoking any benchmark evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ALLOWED_ACTIONS = {
    "reset",
    "navigate",
    "click",
    "fill",
    "select",
    "hover",
    "press",
    "scroll",
    "wait",
    "screenshot",
}
CONTEXT_ERROR = re.compile(
    r"maximum context|context length.{0,80}(?:exceed|too)|prompt.{0,40}too long|too many tokens",
    re.IGNORECASE,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in values):
        raise ValueError(f"Expected JSON objects: {path}")
    return values


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return (normalized or "item")[:120]


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def inspect_case(results: Path, summary_row: dict[str, Any]) -> dict[str, Any]:
    case_id = str(summary_row["case_id"])
    case_run = results / "case_runs" / safe_name(case_id)
    result_path = results / "per_case" / f"{safe_name(case_id)}.json"
    result = read_json(result_path)
    case_state = case_run / "cases" / safe_name(case_id)
    config = read_json(case_state / "config.json") if (case_state / "config.json").is_file() else {}
    obligations = (
        read_json(case_state / "obligations.json")
        if (case_state / "obligations.json").is_file()
        else {}
    )
    calls = jsonl(case_run / "model_calls.jsonl")
    requests = [read_json(path) for path in sorted((case_run / "requests").glob("*.json"))]

    identities = {
        canonical_sha256(
            {
                key: request.get(key)
                for key in ("model", "backend_class", "max_tokens", "temperature", "seed")
            }
        )
        for request in requests
    }
    plan_actions = [
        action
        for plan in result.get("plans", [])
        for check in plan.get("checks", [])
        for action in check.get("actions", [])
    ]
    action_types = [str(action.get("type")) for action in plan_actions]
    reset_boundaries = all(
        not check_index or (check.get("actions") and check["actions"][0].get("type") == "reset")
        for plan in result.get("plans", [])
        for check_index, check in enumerate(plan.get("checks", []))
    )
    obligation_texts = [
        str(row.get("obligation"))
        for row in obligations.get("obligations", [])
        if isinstance(row, dict)
    ]
    plan_obligation_coverage = bool(result.get("plans")) and all(
        [str(check.get("obligation")) for check in plan.get("checks", [])]
        == obligation_texts
        or (
            len(plan.get("checks", [])) == len(obligation_texts)
            and {
                str(check.get("obligation")) for check in plan.get("checks", [])
            }
            == set(obligation_texts)
        )
        for plan in result.get("plans", [])
    ) and bool(obligation_texts)
    budget = config.get("budget") or {}
    maximum_plan_actions = max(
        [
            sum(len(check.get("actions", [])) for check in plan.get("checks", []))
            for plan in result.get("plans", [])
        ]
        or [0]
    )
    browser_budget_reserved = (
        1
        + maximum_plan_actions * (1 + int(budget.get("max_revisions", 0)))
        <= int(budget.get("max_browser_actions", 0))
    )
    judgment_binding = []
    portable_evidence_binding = []
    for judgment in result.get("judgments", []):
        allowed = set((judgment.get("evidence_index") or {}).keys())
        cited = {
            str(reference)
            for check in judgment.get("checks", [])
            for reference in check.get("evidence_refs", [])
        }
        judgment_binding.append(bool(cited) and cited <= allowed)
        portable_rows = []
        for reference in cited:
            row = (judgment.get("evidence_index") or {}).get(reference) or {}
            relative = row.get("relative_object_path")
            object_path = case_run / "browser" / str(relative) if relative else None
            portable_rows.append(
                bool(object_path and object_path.is_file())
                and hashlib.sha256(object_path.read_bytes()).hexdigest()
                == row.get("object_sha256")
            )
        portable_evidence_binding.append(bool(portable_rows) and all(portable_rows))
    replay_digests = {
        str(replay.get("plan_sha256")) for replay in result.get("replays", [])
    }
    accepted_patches = [
        patch
        for patch in result.get("patches", [])
        if (patch.get("acceptance") or {}).get("accepted")
    ]
    monotonic_acceptance = all(
        bool(patch["acceptance"].get("repaired_checks"))
        and not patch["acceptance"].get("regressed_checks")
        for patch in accepted_patches
    )
    final_version_bound = (
        result.get("final_program_version") == result.get("accepted_version")
        if result.get("final_program_version") is not None
        else None
    )
    expected_model = result.get("model") or config.get("same_policy", {}).get("model")
    request_models = {request.get("model") for request in requests}
    same_policy = (
        len(identities) <= 1
        and request_models <= {expected_model}
        and all(call.get("model") == expected_model for call in calls)
    )
    structural_complete = (
        result.get("status") == "complete"
        and bool(result.get("plans"))
        and bool(result.get("judgments"))
        and bool(result.get("replays"))
        and same_policy
        and config.get("official_evaluator_visible") is False
        and result.get("official_evaluator_visible") is False
        and set(action_types) <= ALLOWED_ACTIONS
        and reset_boundaries
        and plan_obligation_coverage
        and browser_budget_reserved
        and all(judgment_binding)
        and all(portable_evidence_binding)
        and len(replay_digests) <= 1
        and monotonic_acceptance
        and final_version_bound is True
    )
    return {
        "case_id": case_id,
        "benchmark": result.get("benchmark"),
        "status": result.get("status"),
        "termination_reason": result.get("termination_reason"),
        "model_calls": len(calls),
        "max_text_input_tokens_estimated": max(
            [int(call.get("input_tokens_estimated", 0)) for call in calls] or [0]
        ),
        "image_inputs": sum(int(call.get("image_count", 0)) for call in calls),
        "same_policy_identity": same_policy,
        "policy_identity_digest_count": len(identities),
        "official_evaluator_excluded": (
            config.get("official_evaluator_visible") is False
            and result.get("official_evaluator_visible") is False
        ),
        "action_schema_valid": set(action_types) <= ALLOWED_ACTIONS,
        "action_types": sorted(set(action_types)),
        "reset_boundaries_valid": reset_boundaries,
        "all_obligations_covered_exactly_once": plan_obligation_coverage,
        "candidate_replay_budget_reserved": browser_budget_reserved,
        "plan_count": len(result.get("plans", [])),
        "judgment_count": len(result.get("judgments", [])),
        "all_judgments_evidence_bound": all(judgment_binding) if judgment_binding else None,
        "all_cited_evidence_portable": (
            all(portable_evidence_binding) if portable_evidence_binding else None
        ),
        "replay_count": len(result.get("replays", [])),
        "single_frozen_plan_replayed": len(replay_digests) <= 1,
        "accepted_patch_count": len(accepted_patches),
        "monotonic_acceptance_valid": monotonic_acceptance,
        "final_version_bound_to_accepted": final_version_bound,
        "browser_actions": result.get("browser_actions", 0),
        "structurally_complete": structural_complete,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    run_root = Path(args.run_root).resolve()
    results = run_root / "results"
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty audit output: {output}")
    output.mkdir(parents=True, exist_ok=True)

    summary = read_json(results / "summary.json")
    cases = [inspect_case(results, row) for row in summary.get("results", [])]
    vllm_log = (run_root / "vllm.log").read_text(
        encoding="utf-8", errors="replace"
    ) if (run_root / "vllm.log").is_file() else ""
    hard_context_errors = CONTEXT_ERROR.findall(vllm_log)
    aggregate = {
        "schema": "multimodalcode-self-verify-structural-audit-1",
        "source_run": str(run_root),
        "source_summary_sha256": hashlib.sha256(
            (results / "summary.json").read_bytes()
        ).hexdigest(),
        "case_count": len(cases),
        "complete": sum(row["status"] == "complete" for row in cases),
        "error": sum(row["status"] == "error" for row in cases),
        "structurally_complete": sum(row["structurally_complete"] for row in cases),
        "all_same_policy_identity": all(row["same_policy_identity"] for row in cases),
        "all_official_evaluator_excluded": all(
            row["official_evaluator_excluded"] for row in cases
        ),
        "all_action_schemas_valid": all(row["action_schema_valid"] for row in cases),
        "all_reset_boundaries_valid": all(row["reset_boundaries_valid"] for row in cases),
        "all_complete_plans_cover_every_obligation": all(
            row["all_obligations_covered_exactly_once"]
            for row in cases
            if row["status"] == "complete"
        ),
        "all_complete_plans_reserve_candidate_replay_budget": all(
            row["candidate_replay_budget_reserved"]
            for row in cases
            if row["status"] == "complete"
        ),
        "all_complete_cases_have_portable_cited_evidence": all(
            row["all_cited_evidence_portable"]
            for row in cases
            if row["status"] == "complete"
        ),
        "accepted_patch_count": sum(row["accepted_patch_count"] for row in cases),
        "hard_context_overflow_count": len(hard_context_errors),
        "max_text_input_tokens_estimated": max(
            [row["max_text_input_tokens_estimated"] for row in cases] or [0]
        ),
        "token_accounting": "character_count_divided_by_four; image tokens unavailable",
        "cases": cases,
    }
    (output / "audit.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "cases.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases),
        encoding="utf-8",
    )
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build official prediction files and local-score summaries for SWE-MM dev."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_IDS = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "instance_ids.txt"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    run_root = PROJECT_ROOT / "runs" / "swe_mm_official" / args.run_label
    agent_root = run_root / "agents" / args.model / "swe-mm"
    evaluation_root = run_root / "evaluation" / args.model
    output_root = run_root / "submission"
    output_root.mkdir(parents=True, exist_ok=True)

    instance_ids = [line.strip() for line in INSTANCE_IDS.read_text().splitlines() if line.strip()]
    predictions: dict[str, dict[str, str]] = {}
    cases = []
    for instance_id in instance_ids:
        case_root = agent_root / instance_id
        completion_path = case_root / "clusterx-summary.json"
        patch_path = case_root / "patch.diff"
        patch = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
        result = load_json(case_root / "result.json")
        report_map = load_json(evaluation_root / instance_id / "report.json")
        report = report_map.get(instance_id, {})
        predictions[instance_id] = {
            "instance_id": instance_id,
            "model_name_or_path": args.model,
            "model_patch": patch,
        }
        cases.append({
            "instance_id": instance_id,
            "case_completed": completion_path.is_file(),
            "agent_status": result.get("status", "not-run"),
            "has_patch": bool(patch.strip()),
            "locally_evaluated": bool(report),
            "resolved": bool(report.get("resolved", False)),
        })

    # A result.json can be written before evaluation and before the task wrapper
    # reaches its durable completion marker.  Count only the latter, otherwise
    # interrupted ClusterX jobs make an incomplete 102-case run look complete.
    completed = sum(row["case_completed"] for row in cases)
    submitted = sum(row["has_patch"] for row in cases)
    evaluated = sum(row["locally_evaluated"] for row in cases)
    resolved = sum(row["resolved"] for row in cases)
    summary = {
        "dataset": "SWE-bench/SWE-bench_Multimodal",
        "split": "dev",
        "denominator": len(instance_ids),
        "model": args.model,
        "run_label": args.run_label,
        "completed_agents": completed,
        "nonempty_patches": submitted,
        "locally_evaluated": evaluated,
        "resolved": resolved,
        "resolved_rate": resolved / len(instance_ids),
        "cases": cases,
    }
    predictions_path = output_root / "predictions.json"
    summary_path = output_root / "local_summary.json"
    predictions_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "predictions": str(predictions_path),
        "summary": str(summary_path),
        **{key: summary[key] for key in (
            "completed_agents", "nonempty_patches", "locally_evaluated", "resolved", "resolved_rate"
        )},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

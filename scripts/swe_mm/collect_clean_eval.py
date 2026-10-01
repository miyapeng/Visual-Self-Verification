#!/usr/bin/env python3
"""Summarize clean, fresh-container SWE-MM evaluation artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
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
    parser.add_argument(
        "--overlay-run-label",
        action="append",
        default=[],
        help=(
            "Use completed cases from this traceable retry run in preference to "
            "the base run. May be specified more than once."
        ),
    )
    parser.add_argument(
        "--output-dir-name",
        default="submission",
        help="Directory under the base run in which to write merged artifacts.",
    )
    args = parser.parse_args()

    run_root = PROJECT_ROOT / "runs" / "swe_mm_official" / args.run_label
    run_labels = [args.run_label, *args.overlay_run_label]
    run_roots = {
        label: PROJECT_ROOT / "runs" / "swe_mm_official" / label
        for label in run_labels
    }
    if Path(args.output_dir_name).name != args.output_dir_name:
        parser.error("--output-dir-name must be a single directory name")
    output_root = run_root / args.output_dir_name
    output_root.mkdir(parents=True, exist_ok=True)
    instance_ids = [line.strip() for line in INSTANCE_IDS.read_text().splitlines() if line.strip()]

    cases = []
    predictions = {}
    for instance_id in instance_ids:
        selected_label = args.run_label
        # Later overlays have higher precedence, but only after the case has a
        # durable generation marker. A partial retry can never hide a completed
        # base result.
        for label in args.overlay_run_label:
            candidate = (
                run_roots[label]
                / "agents"
                / args.model
                / "swe-mm"
                / instance_id
            )
            if (candidate / "clusterx-summary.json").is_file():
                selected_label = label
        selected_root = run_roots[selected_label]
        agent_case = selected_root / "agents" / args.model / "swe-mm" / instance_id
        clean_root = selected_root / "clean_evaluation" / args.model
        patch_path = agent_case / "patch.diff"
        patch = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
        generation_complete = (agent_case / "clusterx-summary.json").is_file()
        summary = load_json(clean_root / instance_id / "clean-eval-summary.json")
        outcome = summary.get("outcome", "pending")
        predictions[instance_id] = {
            "instance_id": instance_id,
            "model_name_or_path": args.model,
            "model_patch": patch,
        }
        cases.append({
            "instance_id": instance_id,
            "generation_complete": generation_complete,
            "has_patch": bool(patch.strip()),
            "clean_evaluation_complete": bool(summary),
            "outcome": outcome,
            "resolved": bool(summary.get("resolved", False)),
            "source_run_label": selected_label,
        })

    outcomes = Counter(row["outcome"] for row in cases)
    resolved = sum(row["resolved"] for row in cases)
    result = {
        "dataset": "SWE-bench/SWE-bench_Multimodal",
        "split": "dev",
        "denominator": len(instance_ids),
        "model": args.model,
        "run_label": args.run_label,
        "overlay_run_labels": args.overlay_run_label,
        "transport": "fresh digest-matched ClusterX instance image per patch",
        "generation_completed": sum(row["generation_complete"] for row in cases),
        "nonempty_patches": sum(row["has_patch"] for row in cases),
        "clean_evaluations_completed": sum(row["clean_evaluation_complete"] for row in cases),
        "resolved": resolved,
        "resolved_rate": resolved / len(instance_ids),
        "outcomes": dict(sorted(outcomes.items())),
        "cases": cases,
    }
    predictions_path = output_root / "predictions.json"
    summary_path = output_root / "clean_local_summary.json"
    predictions_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n")
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "summary": str(summary_path),
        **{key: result[key] for key in (
            "generation_completed", "nonempty_patches", "clean_evaluations_completed",
            "resolved", "resolved_rate", "outcomes"
        )},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

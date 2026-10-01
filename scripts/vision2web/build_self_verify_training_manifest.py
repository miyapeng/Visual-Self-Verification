#!/usr/bin/env python3
"""Index controlled Vision2Web trajectories for later training and analysis.

The output contains observable lifecycle labels and artifact pointers only.  It
does not infer task correctness or replace the official functional/visual
evaluator reward.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def label(row: dict[str, Any]) -> str:
    if not row.get("eligible_for_controlled_experiment"):
        return "excluded_unvalidated_or_contaminated"
    if not row.get("first_deployment_observed"):
        return "deployment_not_observed"
    if row.get("rechecked_after_post_evidence_edit"):
        return "closed_loop_candidate_unscored"
    if row.get("edited_after_visual_or_runtime_evidence"):
        return "evidence_to_edit_without_recheck"
    if row.get("actively_opened_or_inspected"):
        return "inspection_without_evidence_based_edit"
    return "runnable_without_active_inspection"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    analysis = read_json(args.analysis, {})
    records: list[dict[str, Any]] = []
    labels: Counter[str] = Counter()
    for row in analysis.get("runs", []):
        result_path = Path(row["result_path"])
        result = read_json(result_path, {})
        trace = Path(row["development_trace"]) if row.get("development_trace") else None
        versions = read_json(trace / "versions.json", {}) if trace else {}
        audit_path = result_path.parent / "trajectory_audit.json"
        behavior_label = label(row)
        labels[behavior_label] += 1
        records.append(
            {
                "schema": "multimodalcode-vision2web-training-candidate-1",
                "id": f"{row.get('mode')}:{row.get('case_id')}",
                "case_id": row.get("case_id"),
                "level": str(row.get("case_id") or "").split("/", 1)[0],
                "mode": row.get("mode"),
                "model": result.get("model"),
                "behavior_label": behavior_label,
                "labels_are_behavioral_not_correctness_rewards": True,
                "official_functional_score": None,
                "official_visual_score": None,
                "eligible_for_controlled_experiment": row.get(
                    "eligible_for_controlled_experiment", False
                ),
                "observations": {
                    key: row.get(key)
                    for key in (
                        "first_deployment_observed",
                        "time_to_first_deployment_seconds",
                        "post_first_deployment_duration_seconds",
                        "actively_opened_or_inspected",
                        "browser_action_count",
                        "visual_screenshot_observations",
                        "received_postdeployment_visual_evidence",
                        "edited_after_visual_or_runtime_evidence",
                        "deployed_after_visual_or_runtime_evidence",
                        "rechecked_after_post_evidence_edit",
                        "program_changed_after_first_deployment",
                    )
                },
                "versions": {
                    "P_first": (versions.get("P_first") or {}).get("path"),
                    "P_first_sha256": row.get("P_first_sha256"),
                    "P_final": (versions.get("P_final") or {}).get("path"),
                    "P_final_sha256": row.get("P_final_sha256"),
                },
                "artifacts": {
                    "effective_prompt": result.get("effective_prompt"),
                    "all_openhands_attempts": [
                        str(path)
                        for path in sorted(
                            result_path.parent.glob("openhands.events.attempt-*.jsonl")
                        )
                    ],
                    "all_claude_attempts": [
                        str(path)
                        for path in sorted(
                            result_path.parent.glob("claude.events.attempt-*.jsonl")
                        )
                    ],
                    "merged_timeline": (
                        str(trace / "development_timeline.jsonl") if trace else None
                    ),
                    "browser_events": str(trace / "browser.events.jsonl") if trace else None,
                    "evidence_directory": str(trace / "evidence") if trace else None,
                    "trajectory_audit": str(audit_path),
                    "result": str(result_path),
                },
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    summary = {
        "schema": "multimodalcode-vision2web-training-manifest-summary-1",
        "source_analysis": str(args.analysis.resolve()),
        "record_count": len(records),
        "behavior_labels": dict(labels),
        "official_scores_pending": True,
        "warning": (
            "Behavioral labels are not correctness rewards. Do not treat a candidate as "
            "a positive task solution before official functional and visual scoring."
        ),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

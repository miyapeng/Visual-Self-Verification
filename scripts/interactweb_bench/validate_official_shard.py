#!/usr/bin/env python3
"""Validate that a released InteractWeb shard reached terminal evaluation.

This validator reads the released runner's outputs but does not assign or
change scores.  In particular, a model-produced website may legitimately be
missing; completion is defined by a terminal record from the released
``perform_final_evaluation`` function, not by the presence of ``index.html``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"PASS", "FAIL", "CRASHED", "ERROR"}


def load_task_ids(data_path: Path) -> list[str]:
    task_ids: list[str] = []
    for line_number, line in enumerate(
        data_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        task_id = row.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError(f"missing task id at {data_path}:{line_number}")
        task_ids.append(task_id)
    if len(task_ids) != len(set(task_ids)):
        raise RuntimeError(f"duplicate task IDs in {data_path}")
    return task_ids


def terminal_record(history: dict[str, Any]) -> dict[str, Any] | None:
    trajectory = history.get("trajectory", [])
    if not isinstance(trajectory, list):
        return None
    for item in reversed(trajectory):
        if not isinstance(item, dict):
            continue
        info = item.get("debug_info")
        if not isinstance(info, dict) or info.get("is_final") is not True:
            continue
        evaluation = info.get("evaluation_detail")
        if not isinstance(evaluation, dict):
            continue
        status = str(evaluation.get("status", "")).upper()
        tcr = evaluation.get("tcr")
        if status not in TERMINAL_STATUSES or not isinstance(tcr, (int, float)):
            continue
        return {
            "status": status,
            "tcr": float(tcr),
            "sr": evaluation.get("sr"),
            "stop_reason": info.get("stop_reason"),
        }
    return None


def validate(output_dir: Path, model: str, data_path: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    model_root = output_dir / model
    task_ids = load_task_ids(data_path.resolve())
    cases: list[dict[str, Any]] = []
    for task_id in task_ids:
        log_dir = model_root / "logs" / task_id
        history_path = log_dir / "interaction_history.json"
        pending_path = log_dir / "pending_evaluation.json"
        index_path = model_root / "workspaces" / task_id / "index.html"
        parse_error = None
        history = None
        if history_path.is_file():
            try:
                value = json.loads(history_path.read_text(encoding="utf-8"))
                history = value if isinstance(value, dict) else None
            except (OSError, json.JSONDecodeError) as error:
                parse_error = str(error)
        terminal = terminal_record(history) if history else None
        cases.append(
            {
                "task_id": task_id,
                "history": str(history_path) if history_path.is_file() else None,
                "history_parse_error": parse_error,
                "terminal_evaluation": terminal,
                "has_index_html": index_path.is_file(),
                "has_deferred_pending_record": pending_path.is_file(),
            }
        )

    statuses = Counter(
        case["terminal_evaluation"]["status"]
        for case in cases
        if case["terminal_evaluation"]
    )
    stop_reasons = Counter(
        case["terminal_evaluation"]["stop_reason"]
        for case in cases
        if case["terminal_evaluation"]
        and case["terminal_evaluation"]["stop_reason"]
    )
    missing = [case["task_id"] for case in cases if not case["terminal_evaluation"]]
    deferred = [
        case["task_id"] for case in cases if case["has_deferred_pending_record"]
    ]
    report = {
        "schema": "interactweb-official-shard-validation-v1",
        "output_dir": str(output_dir),
        "model": model,
        "data_path": str(data_path.resolve()),
        "expected": len(task_ids),
        "terminal_evaluations": len(task_ids) - len(missing),
        "statuses": dict(sorted(statuses.items())),
        "stop_reasons": dict(sorted(stop_reasons.items())),
        "workspaces_with_index_html": sum(case["has_index_html"] for case in cases),
        "missing_or_invalid_terminal_ids": missing,
        "deferred_pending_ids": deferred,
        "ok": not missing and not deferred,
        "cases": cases,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--data-path", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    report = validate(arguments.output_dir, arguments.model, arguments.data_path)
    report_path = arguments.report or arguments.output_dir / "official_trajectory_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    printable = {key: value for key, value in report.items() if key != "cases"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

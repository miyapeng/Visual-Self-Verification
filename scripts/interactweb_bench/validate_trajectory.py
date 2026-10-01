#!/usr/bin/env python3
"""Reject swallowed InteractWeb worker failures and false-success jobs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected", required=True, type=int)
    arguments = parser.parse_args()

    model_root = arguments.output_dir.resolve() / arguments.model
    histories = sorted(model_root.glob("logs/*/interaction_history.json"))
    pending = sorted(model_root.glob("logs/*/pending_evaluation.json"))
    workspaces = sorted(
        path.parent for path in model_root.glob("workspaces/*/index.html")
    )
    pending_statuses = []
    for path in pending:
        pending_statuses.append(json.loads(path.read_text(encoding="utf-8")).get("status"))
    report = {
        "output_dir": str(arguments.output_dir.resolve()),
        "model": arguments.model,
        "expected": arguments.expected,
        "interaction_histories": len(histories),
        "pending_evaluations": len(pending),
        "pending_statuses": pending_statuses,
        "workspaces_with_index": len(workspaces),
    }
    report["ok"] = (
        len(histories) == arguments.expected
        and len(pending) == arguments.expected
        and pending_statuses == ["pending"] * arguments.expected
        and len(workspaces) == arguments.expected
    )
    summary = arguments.output_dir.resolve() / "trajectory_summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

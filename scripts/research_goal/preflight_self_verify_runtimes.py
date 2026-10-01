#!/usr/bin/env python3
"""Mechanically start every frozen public web runtime before model inference.

This gate performs one screenshot action per case.  It has no semantic judge,
does not inspect an official evaluator, and exists only to separate deployment
or filesystem failures from model-policy outcomes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multimodalcode.io import safe_name, write_json
from multimodalcode.research.interactive import InteractiveJudge
from multimodalcode.research.schema import ActionPlan
from multimodalcode.research.self_verify import PublicSelfVerifyCase
from multimodalcode.research.tools import SafeFileToolExecutor


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Case manifest must contain JSON objects")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--browser-timeout-ms", type=int, default=10_000)
    args = parser.parse_args()

    manifest = Path(args.cases).resolve()
    output = Path(args.output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing non-empty runtime preflight output: {output}")
    output.mkdir(parents=True, exist_ok=True)

    judge = InteractiveJudge(output / "browser")
    results: list[dict[str, Any]] = []
    for row in _rows(manifest):
        case = PublicSelfVerifyCase.from_dict(row, base_dir=manifest.parent)
        case_name = safe_name(case.case_id)
        tool = SafeFileToolExecutor.prepare(
            case.program_path,
            output / "workspaces" / case_name,
            output / "checkpoints" / case_name,
        )
        plan = ActionPlan.from_dict(
            {
                "checklist": [
                    {
                        "id": "runtime",
                        "description": "Start frozen public runtime",
                        "expected": "Initial document can be captured",
                        "source": "mechanical-preflight",
                    }
                ],
                "actions": [{"type": "screenshot", "checklist_id": "runtime"}],
                "planner": "mechanical-preflight",
                "metadata": {"web_runtime": case.web_runtime},
            }
        )
        try:
            execution = judge.execute(
                case_id=case.case_id,
                program_path=tool.workspace,
                plan=plan,
                purpose="runtime-preflight",
                timeout_ms=args.browser_timeout_ms,
                record_video=False,
            )
            action = (execution.get("actions") or [{}])[0]
            results.append(
                {
                    "case_id": case.case_id,
                    "benchmark": case.benchmark,
                    "status": "pass" if action.get("status") == "pass" else "error",
                    "execution_id": execution.get("execution_id"),
                    "action_status": action.get("status"),
                    "error": action.get("error"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case_id": case.case_id,
                    "benchmark": case.benchmark,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    summary = {
        "schema": "multimodalcode-self-verify-runtime-preflight-1",
        "case_manifest": str(manifest),
        "official_evaluator_visible": False,
        "semantic_judge_used": False,
        "total": len(results),
        "passed": sum(row["status"] == "pass" for row in results),
        "error": sum(row["status"] == "error" for row in results),
        "results": results,
    }
    summary_path = output / "summary.json"
    write_json(summary_path, summary)
    summary_path.chmod(0o644)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

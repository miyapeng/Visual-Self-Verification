#!/usr/bin/env python3
"""Run the frozen InteractWeb agent and defer only its terminal judge call.

The released agent, user interaction loop, workspace execution, and visual
copilot remain unchanged.  At the point where the released runner would call
``perform_final_evaluation``, this adapter writes a durable pending record.
The official source tree is never edited.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"
SOURCE_ROOT = UPSTREAM / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from experiment import run_simulation as official  # noqa: E402


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def defer_final_evaluation(
    builder,
    user_sim,
    workspace_dir,
    log_dir,
    oracle_slots,
    user_instruction,
    task_id,
    args,
    stop_reason="submitted",
):
    """Checkpoint exactly where the released terminal judge would start."""
    pending_path = Path(log_dir) / "pending_evaluation.json"
    payload = {
        "schema": 1,
        "status": "pending",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "log_dir": str(Path(log_dir).resolve()),
        "stop_reason": stop_reason,
        "user_instruction_used_for_grading": user_instruction,
        "oracle_slots": oracle_slots,
        "models": {
            "builder_model": args.builder_model,
            "visual_copilot_model": args.visual_copilot_model,
            "webvoyager_model": args.webvoyager_model,
            "user_model": args.user_model,
        },
        "official_function_deferred": "experiment.run_simulation.perform_final_evaluation",
        "official_source_revision": "56040f368e9b41e770fc814d17a7c207c6be7bd9",
    }
    _write_json(pending_path, payload)
    # Match the terminal-record shape written by the released evaluator.  The
    # upstream resume logic only skips a completed trajectory when its final
    # message carries ``is_final``.  Without this durable marker, restarting a
    # trajectory-only shard deletes an otherwise complete log and regenerates
    # the case.  This record contains no synthetic score; the official judge
    # remains explicitly deferred in ``pending_evaluation.json``.
    deferred_result = {
        "status": "DEFERRED",
        "sr": None,
        "tcr": None,
        "text": "Official terminal evaluation deferred.",
        "raw_metrics": {"Total_Weight": None, "Details": []},
    }
    builder.messages.append(
        {
            "role": "user",
            "content": (
                f"[SYSTEM]: Task Stopped ({stop_reason}).\n"
                "Evaluation Report:\nOfficial terminal evaluation deferred."
            ),
            "info": {
                "evaluation_detail": deferred_result,
                "final_env_state": {"status": "not_run", "reason": "deferred"},
                "is_final": True,
                "stop_reason": stop_reason,
                "oracle_slots_used_for_grading": oracle_slots,
                "pending_evaluation": str(pending_path),
            },
        }
    )
    print(f"[deferred-evaluation] task={task_id} pending={pending_path}", flush=True)
    return {**deferred_result, "pending": str(pending_path)}


def main() -> int:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    official.perform_final_evaluation = defer_final_evaluation
    official.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

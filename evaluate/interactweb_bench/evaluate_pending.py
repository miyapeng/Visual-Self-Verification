#!/usr/bin/env python3
"""Evaluate a deferred InteractWeb trajectory with the released judge code."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"
SOURCE_ROOT = UPSTREAM / "src"
sys.path.insert(0, str(SOURCE_ROOT))


class RestoredBuilder:
    def __init__(self, history: dict):
        self.messages = []
        for item in history.get("trajectory", []):
            message = {"role": item["role"], "content": item["content"]}
            if "debug_info" in item:
                message["info"] = item["debug_info"]
            self.messages.append(message)
        self.format_error_count = history.get("path_distribution_stats", {}).get(
            "FORMAT_ERROR_COUNT", 0
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pending", type=Path)
    parser.add_argument("--user-model-base-url", required=True)
    parser.add_argument("--user-model-api-key", required=True)
    parser.add_argument("--judge-base-url", required=True)
    parser.add_argument("--judge-api-key", required=True)
    arguments = parser.parse_args()

    # The frozen release constructs its API clients while modules are imported,
    # so install the supplied endpoints before importing those modules.
    os.environ["OPENAILIKE_API_KEY"] = arguments.user_model_api_key
    os.environ["OPENAILIKE_BASE_URL"] = arguments.user_model_base_url
    os.environ["OPENAILIKE_VLM_API_KEY"] = arguments.judge_api_key
    os.environ["OPENAILIKE_VLM_BASE_URL"] = arguments.judge_base_url
    os.environ["WEBVOYAGER_BASE_URL"] = arguments.judge_base_url
    os.environ["WEBVOYAGER_API_KEY"] = arguments.judge_api_key

    from experiment import run_simulation as official
    from experiment.simulation_agents import UserSimulator

    pending_path = arguments.pending.resolve()
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    log_dir = Path(pending["log_dir"])
    history_path = log_dir / "interaction_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    builder = RestoredBuilder(history)
    models = pending["models"]

    user_sim = UserSimulator(
        ground_truth_instruction=pending["user_instruction_used_for_grading"],
        initial_instruction=pending["user_instruction_used_for_grading"],
        evaluation_checklist=[],
        persona="P-MIN",
        model=models["user_model"],
        vlm_model=models["webvoyager_model"],
        base_url=arguments.user_model_base_url,
        api_key=arguments.user_model_api_key,
    )
    judge_args = SimpleNamespace(
        webvoyager_model=models["webvoyager_model"],
        user_model=models["user_model"],
    )

    backup = log_dir / "interaction_history.generation.json"
    if not backup.exists():
        shutil.copy2(history_path, backup)

    result = official.perform_final_evaluation(
        builder=builder,
        user_sim=user_sim,
        workspace_dir=pending["workspace_dir"],
        log_dir=pending["log_dir"],
        oracle_slots=pending["oracle_slots"],
        user_instruction=pending["user_instruction_used_for_grading"],
        task_id=pending["task_id"],
        args=judge_args,
        stop_reason=pending["stop_reason"],
    )
    if result.get("status") in {"ERROR", "CRASHED"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    official.save_interaction_history(
        builder.messages,
        str(history_path),
        builder.format_error_count,
    )
    pending["status"] = "evaluated"
    pending["evaluation_status"] = result.get("status")
    pending_path.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

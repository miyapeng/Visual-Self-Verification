#!/usr/bin/env python3
"""Validate that a FronTalk generation run contains every expected turn."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--dialogues", type=int, required=True)
    parser.add_argument("--turns-per-dialogue", type=int, default=10)
    arguments = parser.parse_args()

    run_dir = arguments.run_dir.resolve()
    messages_path = run_dir / "messages.jsonl"
    rows = [
        json.loads(line)
        for line in messages_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    dialogue_ids = {row[0] for row in rows}
    assistants = [row for row in rows if row[1].get("role") == "assistant"]
    expected_turns = arguments.dialogues * arguments.turns_per_dialogue
    final_indexes = list((run_dir / f"t.{arguments.turns_per_dialogue - 1}").glob("*/index.html"))
    report = {
        "run_dir": str(run_dir),
        "dialogues": len(dialogue_ids),
        "assistant_turns": len(assistants),
        "expected_dialogues": arguments.dialogues,
        "expected_assistant_turns": expected_turns,
        "final_index_files": len(final_indexes),
    }
    report["ok"] = (
        report["dialogues"] == arguments.dialogues
        and report["assistant_turns"] == expected_turns
        and report["final_index_files"] == arguments.dialogues
    )
    (run_dir / "generation_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

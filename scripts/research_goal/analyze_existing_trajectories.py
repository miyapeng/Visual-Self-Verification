#!/usr/bin/env python3
"""Create a leakage-safe audit for the canonical active benchmark scope.

The default aggregate contains Vision2Web Level 2/3, FronTalk, and SWE-bench
Multimodal only. InteractWeb-Bench is archived and cannot enter new reports
through this command.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multimodalcode.research.trajectory_observations import analyze_all


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/research/active_visual_verification/observational-audit-003"),
    )
    arguments = parser.parse_args()
    project_root = arguments.project_root.resolve()
    output_dir = arguments.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    events, cases, summary = analyze_all(project_root)
    _write_jsonl(output_dir / "events.jsonl", events)
    _write_jsonl(output_dir / "cases.jsonl", cases)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

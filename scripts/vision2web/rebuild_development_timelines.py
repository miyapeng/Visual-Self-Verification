#!/usr/bin/env python3
"""Rebuild complete Vision2Web timelines from every preserved retry attempt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multimodalcode.agent_harness.vision2web_trace import build_development_timeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()

    rebuilt = 0
    for result_path in sorted(args.run_root.rglob("result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        trace_value = result.get("development_trace")
        if not trace_value:
            continue
        run_dir = result_path.parent
        attempts = sorted(run_dir.glob("openhands.events.attempt-*.jsonl"))
        if not attempts:
            continue
        build_development_timeline(
            run_dir / "openhands.events.jsonl",
            Path(trace_value),
            attempt_trajectories=attempts,
        )
        rebuilt += 1

    print(json.dumps({"rebuilt": rebuilt, "run_root": str(args.run_root.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

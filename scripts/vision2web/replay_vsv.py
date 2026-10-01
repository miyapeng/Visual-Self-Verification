#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from multimodalcode.io import read_json, write_json  # noqa: E402
from multimodalcode.vsv_eval.replay import compare_replays, replay_workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay one VSV episode on isolated checkpoints")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--before-workspace", type=Path, required=True)
    parser.add_argument("--after-workspace", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--app-url", default="http://127.0.0.1:3000/")
    parser.add_argument("--start-command", default="bash start.sh")
    args = parser.parse_args()

    specification = read_json(args.plan)
    before = replay_workspace(
        args.before_workspace,
        specification,
        args.output_dir / "before",
        app_url=args.app_url,
        start_command=args.start_command,
    )
    after = None
    if args.after_workspace:
        after = replay_workspace(
            args.after_workspace,
            specification,
            args.output_dir / "after",
            app_url=args.app_url,
            start_command=args.start_command,
        )
    combined = compare_replays(args.episode_id, before, after)
    write_json(args.output_dir / "replay_results.json", combined)
    print(args.output_dir / "replay_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

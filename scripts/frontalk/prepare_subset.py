#!/usr/bin/env python3
"""Create a deterministic FronTalk smoke subset without editing upstream."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "data" / "frontalk" / "data.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", default=1, type=int)
    arguments = parser.parse_args()
    if arguments.count < 1:
        parser.error("--count must be positive")

    rows = [line for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    selected = rows[: arguments.count]
    if len(selected) != arguments.count:
        raise RuntimeError(f"requested {arguments.count} rows, found {len(selected)}")
    for line in selected:
        json.loads(line)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(f"wrote {len(selected)} FronTalk dialogue(s) to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

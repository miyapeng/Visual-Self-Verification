#!/usr/bin/env python3
"""Import a leaderboard Claude conversation without executing recorded commands."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from build_scaffold_model_comparison_site import parse_claude


def import_trace(source: Path, output: Path) -> dict:
    payload = source.read_bytes()
    record = json.loads(payload)
    conversation = record.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        raise ValueError("Expected a nonempty native Claude conversation")
    if record.get("attempt_count", 1) != 1:
        raise ValueError("Multiple attempts need explicit session reconstruction")
    output.mkdir(parents=True, exist_ok=True)
    native = output / "native"
    native.mkdir(exist_ok=True)
    (native / "claude.events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in conversation)
    )
    timeline, raw_links = parse_claude(SimpleNamespace(run_dir=native, slug="leaderboard"), output)
    run = {
        "case_id": f"{source.parents[2].name}/{source.parents[1].name}",
        "model": record.get("model"), "framework": record.get("framework"),
        "source": {"path": str(source.resolve()), "sha256": hashlib.sha256(payload).hexdigest()},
        "raw_links": raw_links, "timeline": timeline,
        "development": {}, "result": {},
    }
    (output / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run = import_trace(args.source, args.output_dir)
    print(json.dumps({"case_id": run["case_id"], "events": len(run["timeline"]),
                      "output": str(args.output_dir / "run.json")}))

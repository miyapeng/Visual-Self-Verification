#!/usr/bin/env python3
"""Create deterministic, lossless InteractWeb task shards."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--num-shards", type=int, default=8)
    args = parser.parse_args()

    lines = [line for line in args.source.read_text(encoding="utf-8").splitlines() if line]
    rows = [json.loads(line) for line in lines]
    ids = [row["id"] for row in rows]
    if len(lines) != 404 or len(ids) != len(set(ids)):
        raise RuntimeError("expected exactly 404 uniquely identified InteractWeb tasks")
    if args.num_shards < 1:
        raise ValueError("--num-shards must be positive")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "strategy": "source-order round-robin",
        "source": str(args.source.resolve()),
        "source_sha256": sha256(args.source),
        "num_tasks": len(lines),
        "num_shards": args.num_shards,
        "shards": [],
    }
    seen: set[str] = set()
    for index in range(args.num_shards):
        selected = [(line, row) for position, (line, row) in enumerate(zip(lines, rows)) if position % args.num_shards == index]
        path = args.output_dir / f"shard-{index:02d}-of-{args.num_shards:02d}.jsonl"
        path.write_text("".join(line + "\n" for line, _ in selected), encoding="utf-8")
        shard_ids = [row["id"] for _, row in selected]
        if seen.intersection(shard_ids):
            raise RuntimeError("shards overlap")
        seen.update(shard_ids)
        manifest["shards"].append(
            {
                "index": index,
                "path": str(path.resolve()),
                "tasks": len(selected),
                "sha256": sha256(path),
                "first_id": shard_ids[0],
                "last_id": shard_ids[-1],
            }
        )
    if seen != set(ids):
        raise RuntimeError("shards do not exactly cover the source task IDs")
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

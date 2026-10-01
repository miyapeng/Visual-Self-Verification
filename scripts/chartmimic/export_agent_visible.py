#!/usr/bin/env python3
"""Freeze ChartMimic metadata needed by agents, excluding oracle code."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_rows(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {row["idx"]: row for row in rows}
    if len(result) != len(rows):
        raise RuntimeError(f"Duplicate idx in {path}")
    return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--upstream", type=Path, default=project_root / "evaluate/chartmimic/upstream"
    )
    parser.add_argument(
        "--data-root", type=Path, default=project_root / "data/chartmimic"
    )
    args = parser.parse_args()
    direct_source = args.upstream / "dimentions_info.jsonl"
    customized_source = args.upstream / "dimentions_info_edit.jsonl"
    direct = load_rows(direct_source)
    customized = load_rows(customized_source)
    output = args.data_root / "agent_visible"
    output.mkdir(parents=True, exist_ok=True)

    direct_ids = sorted(path.stem for path in (args.data_root / "extracted" / "direct_600").glob("*.py"))
    customized_ids = sorted(path.stem for path in (args.data_root / "extracted" / "customized_600").glob("*.py"))
    if len(direct_ids) != 600 or len(customized_ids) != 600:
        raise RuntimeError("Expected exactly 600 Python cases in each standard split")

    write_jsonl(
        output / "direct_600.jsonl",
        [{"idx": idx, "width": direct[idx]["width"], "height": direct[idx]["height"]} for idx in direct_ids],
    )
    write_jsonl(
        output / "customized_600.jsonl",
        [
            {
                "idx": idx,
                "instruction": customized[idx]["instruction"],
                "width": customized[idx]["width"],
                "height": customized[idx]["height"],
            }
            for idx in customized_ids
        ],
    )
    manifest = {
        "schema": "chartmimic-agent-visible-1",
        "oracle_code_included": False,
        "sources": {
            str(direct_source): digest(direct_source),
            str(customized_source): digest(customized_source),
        },
        "counts": {"direct_600": len(direct_ids), "customized_600": len(customized_ids)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

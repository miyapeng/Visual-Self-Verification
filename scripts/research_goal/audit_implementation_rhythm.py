#!/usr/bin/env python3
"""Create a rhythm audit from an immutable normalized trajectory audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import groupby
from pathlib import Path

from multimodalcode.research.implementation_rhythm import (
    RHYTHM_SUMMARY_SCHEMA,
    aggregate_rhythm,
    audit_case,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _in_active_scope(row: dict) -> bool:
    benchmark = str(row.get("benchmark"))
    if benchmark == "interactweb":
        return False
    return benchmark in {"frontalk", "swe_mm", "vision2web"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    events_path = input_dir / "events.jsonl"
    cases_path = input_dir / "cases.jsonl"
    if not events_path.is_file() or not cases_path.is_file():
        raise SystemExit(f"normalized audit is incomplete: {input_dir}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    source_cases = {
        (row["benchmark"], row["task"]): row
        for row in _rows(cases_path)
        if _in_active_scope(row)
    }
    audited: list[dict] = []
    seen: set[tuple[str, str]] = set()
    with (output_dir / "cases.jsonl").open("w", encoding="utf-8") as output:
        key_fn = lambda row: (row["benchmark"], row["task"])
        active_events = (row for row in _rows(events_path) if _in_active_scope(row))
        for key, grouped_events in groupby(active_events, key=key_fn):
            case = source_cases[key]
            row = audit_case(grouped_events, case)
            audited.append(row)
            seen.add(key)
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        for key in sorted(source_cases.keys() - seen):
            row = audit_case([], source_cases[key])
            audited.append(row)
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema": RHYTHM_SUMMARY_SCHEMA,
        "source": {
            "input_dir": str(input_dir),
            "events_sha256": _sha256(events_path),
            "cases_sha256": _sha256(cases_path),
            "case_count": len(source_cases),
        },
        "definitions": {
            "implementation_block": (
                "consecutive code inspection/edit activity containing at least one production "
                "edit; active checks and deployment boundaries close a block"
            ),
            "active_verification": (
                "an executable/browser action invoked by the coding policy; evaluator records, "
                "automatic deployment feedback, and reasoning prose are excluded"
            ),
            "confirmed_repair": (
                "exact same executable action digest fails, code changes, then the same action passes"
            ),
        },
        "known_adapter_corrections": [
            "Vision2Web think/finish prose mentioning screenshots/tests is non-executing and excluded",
            "official/deferred evaluator records are excluded from the coding-policy stream",
        ],
        "scope": {
            "active_benchmarks": ["vision2web", "frontalk", "swe_bench_multimodal"],
            "archived_exclusions": ["interactweb"],
        },
        "benchmarks": aggregate_rhythm(audited),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

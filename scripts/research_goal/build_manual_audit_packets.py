#!/usr/bin/env python3
"""Build a deterministic active-scope SWE-MM review sample."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from multimodalcode.research.trajectory_observations import _stringify


SEED = "manual-audit-active-scope-001"
FORBIDDEN = ("ground_truth_instruction", "oracle_slots", "evaluation_checklist")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def stable_key(stratum: str, row: dict[str, Any]) -> str:
    raw = f"{SEED}|{stratum}|{row['benchmark']}|{row['task']}"
    return hashlib.sha256(raw.encode()).hexdigest()


def stratified_pick(
    rows: Iterable[dict[str, Any]],
    *,
    stratum: str,
    count: int,
    group_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values: list[Any] = []
        for field in group_fields:
            value = row.get(field)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            values.append(value)
        groups[tuple(values)].append(row)
    for values in groups.values():
        values.sort(key=lambda row: stable_key(stratum, row))
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < count:
        progressed = False
        for key in sorted(groups, key=lambda value: tuple(str(part) for part in value)):
            values = groups[key]
            if depth < len(values):
                selected.append(values[depth])
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            break
        depth += 1
    return selected


def event_categories(event: dict[str, Any]) -> set[str]:
    return {
        category
        for action in event.get("executed_action") or []
        for category in action.get("categories") or []
    }


def select_event_window(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    edit_sequences = [event["sequence"] for event in events if event.get("edit")]
    failure_sequences = [
        event["sequence"]
        for event in events
        if (event.get("observation") or {}).get("failure_signal")
    ]
    check_sequences = [
        event["sequence"]
        for event in events
        if "executable_check" in event_categories(event)
    ]
    submit_sequences = [
        event["sequence"] for event in events if "submit" in event_categories(event)
    ]
    anchors = [
        values[-1]
        for values in (edit_sequences, failure_sequences, check_sequences, submit_sequences)
        if values
    ]
    wanted: set[int] = set()
    for anchor in anchors:
        wanted.update(range(max(0, anchor - 1), min(len(events), anchor + 2)))
    if edit_sequences:
        wanted.update(range(edit_sequences[-1], len(events)))
    return [event for event in events if event["sequence"] in wanted]


def compact_raw(value: Any, limit: int = 6000) -> str:
    text = _stringify(value)
    return text if len(text) <= limit else text[:limit] + f"\n[truncated {len(text) - limit} chars]"


def raw_visible_record(
    source_ref: str,
    cache: dict[Path, dict[str, Any]],
) -> dict[str, Any] | None:
    if "#" not in source_ref:
        return None
    raw_path, fragment = source_ref.rsplit("#", 1)
    path = Path(raw_path)
    if not path.is_file():
        return None
    data = cache.setdefault(path, json.loads(path.read_text(encoding="utf-8")))
    if fragment.startswith("trajectory/"):
        index = int(fragment.rsplit("/", 1)[-1])
        rows = data.get("trajectory", [])
        if index >= len(rows):
            return None
        row = rows[index]
        visible: dict[str, Any] = {
            "role": row.get("role"),
            "content": compact_raw(row.get("content")),
        }
        return visible
    if fragment.startswith("events/"):
        sequence = int(fragment.rsplit("/", 1)[-1])
        row = next(
            (item for item in data.get("events", []) if int(item.get("sequence", -1)) == sequence),
            None,
        )
        if row is None:
            return None
        return {
            "actor": row.get("actor"),
            "text": compact_raw(row.get("text")),
            "tools": [compact_raw(tool, limit=3000) for tool in row.get("tools", [])],
            "error_code": row.get("error_code"),
            "exit_status": row.get("exit_status"),
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    cases = read_jsonl(args.audit_dir / "cases.jsonl")
    events = read_jsonl(args.audit_dir / "events.jsonl")
    by_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_task[(event["benchmark"], event["task"])].append(event)

    selections: list[tuple[str, dict[str, Any]]] = []
    swe = [row for row in cases if row["benchmark"] == "swe_mm"]
    selections.extend(
        ("swe_submit_without_fresh_check", row)
        for row in stratified_pick(
            (row for row in swe if row["submitted_without_fresh_executable_check"]),
            stratum="swe_submit_without_fresh_check",
            count=12,
            group_fields=("external_outcome",),
        )
    )
    selections.extend(
        ("swe_fresh_check_control", row)
        for row in stratified_pick(
            (
                row
                for row in swe
                if row["submit_actions"] and row["fresh_executable_check_on_final_version"]
            ),
            stratum="swe_fresh_check_control",
            count=8,
            group_fields=("external_outcome",),
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "multimodalcode-manual-audit-sample-1",
        "seed": SEED,
        "audit_dir": str(args.audit_dir.resolve()),
        "sample_count": len(selections),
        "strata": defaultdict(int),
        "cases": [],
    }
    packets: list[dict[str, Any]] = []
    cache: dict[Path, dict[str, Any]] = {}
    for stratum, case in selections:
        manifest["strata"][stratum] += 1
        manifest["cases"].append(
            {
                "stratum": stratum,
                "benchmark": case["benchmark"],
                "task": case["task"],
                "external_outcome": case["external_outcome"],
                "source_trajectory": case["source_trajectory"],
            }
        )
        window = select_event_window(by_task[(case["benchmark"], case["task"])])
        packet_events = []
        for event in window:
            item = dict(event)
            item["raw_visible_record"] = raw_visible_record(event["source_ref"], cache)
            packet_events.append(item)
        packets.append(
            {
                "schema": "multimodalcode-manual-audit-review-packet-1",
                "stratum": stratum,
                "case": case,
                "events": packet_events,
            }
        )

    manifest["strata"] = dict(sorted(manifest["strata"].items()))
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    packet_text = "".join(json.dumps(packet, ensure_ascii=False) + "\n" for packet in packets)
    for forbidden in FORBIDDEN:
        if forbidden in manifest_text or forbidden in packet_text:
            raise RuntimeError(f"hidden evaluator field leaked into review packet: {forbidden}")
    (args.output_dir / "sample_manifest.json").write_text(manifest_text, encoding="utf-8")
    (args.output_dir / "review_packets.jsonl").write_text(packet_text, encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), **manifest["strata"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

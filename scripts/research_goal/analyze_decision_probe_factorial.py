#!/usr/bin/env python3
"""Combine the frozen decision probe with its pre-registered rule control.

This analysis intentionally treats the InteractWeb manual audit as a decision
diagnostic only.  It does not reinterpret SWE-MM freshness strata as gold
submit labels and does not report either benchmark's official task success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


FACTORIAL_POLICIES = (
    "summary",
    "summary_with_completion_rule",
    "status_ledger_no_rule",
    "conserved_frontier",
)
INTERACT_POSITIVE = "interact_submit_after_failure"
INTERACT_CONTROL = "interact_submit_without_failure_control"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_mcnemar(left: list[bool], right: list[bool]) -> dict[str, Any]:
    if len(left) != len(right):
        raise ValueError("Paired McNemar inputs must have equal length")
    left_only = sum(a and not b for a, b in zip(left, right))
    right_only = sum(b and not a for a, b in zip(left, right))
    discordant = left_only + right_only
    if not discordant:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(left_only, right_only) + 1)
        )
        p_value = min(1.0, 2 * tail / (2**discordant))
    return {
        "left_only": left_only,
        "right_only": right_only,
        "discordant": discordant,
        "two_sided_exact_p": p_value,
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def interact_correct(stratum: str, decision: str) -> bool:
    if stratum == INTERACT_POSITIVE:
        return decision != "submit"
    if stratum == INTERACT_CONTROL:
        return decision == "submit"
    raise ValueError(f"Not an InteractWeb audit stratum: {stratum}")


def load_unique(paths: list[Path], *, key_fields: tuple[str, ...]) -> dict[tuple[Any, ...], dict[str, Any]]:
    combined: dict[tuple[Any, ...], dict[str, Any]] = {}
    for path in paths:
        for row in read_jsonl(path):
            key = tuple(row[field] for field in key_fields)
            if key in combined and combined[key] != row:
                raise ValueError(f"Conflicting duplicate {key} across inputs")
            combined[key] = row
    return combined


def command(args: argparse.Namespace) -> int:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    labels = load_unique(
        [args.primary_input / "labels.private.jsonl", args.rule_input / "labels.private.jsonl"],
        key_fields=("benchmark", "task"),
    )
    requests = load_unique(
        [args.primary_input / "requests.jsonl", args.rule_input / "requests.jsonl"],
        key_fields=("benchmark", "task", "policy"),
    )
    all_results = load_unique(
        [args.primary_run / "results.jsonl", args.rule_run / "results.jsonl"],
        key_fields=("benchmark", "task", "policy"),
    )
    errors = [row for row in all_results.values() if row.get("status") != "ok"]
    if errors:
        raise ValueError(f"Cannot analyze with {len(errors)} inference errors")

    matrix: list[dict[str, Any]] = []
    for (benchmark, task), label in sorted(labels.items()):
        decisions: dict[str, str] = {}
        selected_tokens: dict[str, int] = {}
        image_counts: dict[str, int] = {}
        for policy in FACTORIAL_POLICIES:
            key = (benchmark, task, policy)
            if key not in all_results or key not in requests:
                raise ValueError(f"Missing factorial cell: {key}")
            decisions[policy] = str(all_results[key]["decision"])
            metadata = requests[key]["metadata"]
            selected_tokens[policy] = int(metadata["selected_estimated_text_tokens"])
            image_counts[policy] = int(metadata["total_image_count"])
        matrix.append(
            {
                "schema": "multimodalcode-decision-probe-factorial-case-1",
                "benchmark": benchmark,
                "task": task,
                "stratum": label["stratum"],
                "decisions": decisions,
                "selected_estimated_text_tokens": selected_tokens,
                "total_image_count": image_counts,
            }
        )
    write_jsonl(output / "case_matrix.jsonl", matrix)

    cells: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in matrix:
        for policy in FACTORIAL_POLICIES:
            grouped[(row["benchmark"], row["stratum"], policy)].append(row)
    for (benchmark, stratum, policy), rows in sorted(grouped.items()):
        decisions = Counter(row["decisions"][policy] for row in rows)
        cell: dict[str, Any] = {
            "benchmark": benchmark,
            "stratum": stratum,
            "policy": policy,
            "n": len(rows),
            "decisions": dict(sorted(decisions.items())),
            "submit_rate": decisions["submit"] / len(rows),
            "mean_selected_estimated_text_tokens": mean(
                [float(row["selected_estimated_text_tokens"][policy]) for row in rows]
            ),
            "mean_total_image_count": mean(
                [float(row["total_image_count"][policy]) for row in rows]
            ),
        }
        if benchmark == "interactweb":
            cell["manual_audit_decision_agreement"] = mean(
                [
                    float(interact_correct(stratum, row["decisions"][policy]))
                    for row in rows
                ]
            )
        cells.append(cell)

    interact_rows = [row for row in matrix if row["benchmark"] == "interactweb"]
    interact_scores: list[dict[str, Any]] = []
    for policy in FACTORIAL_POLICIES:
        positive = [row for row in interact_rows if row["stratum"] == INTERACT_POSITIVE]
        control = [row for row in interact_rows if row["stratum"] == INTERACT_CONTROL]
        positive_safe = mean(
            [float(row["decisions"][policy] != "submit") for row in positive]
        )
        control_submit = mean(
            [float(row["decisions"][policy] == "submit") for row in control]
        )
        interact_scores.append(
            {
                "policy": policy,
                "positive_safe_non_submit_rate": positive_safe,
                "control_submit_rate": control_submit,
                "balanced_manual_audit_agreement": (
                    (positive_safe + control_submit) / 2
                    if positive_safe is not None and control_submit is not None
                    else None
                ),
            }
        )

    comparisons = (
        ("summary", "summary_with_completion_rule", "rule_effect_without_ledger"),
        ("status_ledger_no_rule", "conserved_frontier", "rule_effect_with_ledger"),
        ("summary", "status_ledger_no_rule", "ledger_effect_without_rule"),
        ("summary_with_completion_rule", "conserved_frontier", "ledger_effect_with_rule"),
    )
    paired: list[dict[str, Any]] = []
    for left, right, effect in comparisons:
        left_error = [
            not interact_correct(row["stratum"], row["decisions"][left])
            for row in interact_rows
        ]
        right_error = [
            not interact_correct(row["stratum"], row["decisions"][right])
            for row in interact_rows
        ]
        paired.append(
            {
                "effect": effect,
                "left": left,
                "right": right,
                "n": len(interact_rows),
                "left_manual_audit_error_rate": mean([float(value) for value in left_error]),
                "right_manual_audit_error_rate": mean([float(value) for value in right_error]),
                "paired_error_mcnemar": exact_mcnemar(left_error, right_error),
                "exact_decision_match_rate": mean(
                    [
                        float(row["decisions"][left] == row["decisions"][right])
                        for row in interact_rows
                    ]
                ),
            }
        )

    payload = {
        "schema": "multimodalcode-decision-probe-factorial-summary-1",
        "interpretation_boundary": (
            "Retrospective continuation-decision diagnostic. InteractWeb agreement uses "
            "manually audited visible counterevidence, not official task correctness. "
            "SWE-MM freshness strata are descriptive and are not treated as gold submit labels."
        ),
        "case_count": len(matrix),
        "policies": list(FACTORIAL_POLICIES),
        "inference_error_count": len(errors),
        "cells": cells,
        "interactweb_manual_audit_scores": interact_scores,
        "interactweb_factorial_paired_comparisons": paired,
        "source_sha256": {
            "primary_requests": sha256(args.primary_input / "requests.jsonl"),
            "primary_results": sha256(args.primary_run / "results.jsonl"),
            "rule_requests": sha256(args.rule_input / "requests.jsonl"),
            "rule_results": sha256(args.rule_run / "results.jsonl"),
        },
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-input", type=Path, required=True)
    parser.add_argument("--primary-run", type=Path, required=True)
    parser.add_argument("--rule-input", type=Path, required=True)
    parser.add_argument("--rule-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(command(build_parser().parse_args()))

#!/usr/bin/env python3
"""Build, run, and summarize leakage-safe retrospective decision probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from multimodalcode.backends import OpenAICompatibleBackend
from multimodalcode.research.context import ContextBudget
from multimodalcode.research.decision_probes import (
    POLICY_FACTORIES,
    build_probe_cases,
    parse_decision_response,
    render_decision_prompt,
    write_jsonl,
)
from multimodalcode.schema import GenerationRequest


SEED = "decision-probe-001"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_archived_interactweb(rows: list[Any], *, source: str) -> None:
    for row in rows:
        benchmark = row.benchmark if hasattr(row, "benchmark") else row.get("benchmark")
        if str(benchmark).lower() in {"interactweb", "interactweb_bench", "interactweb-bench"}:
            raise ValueError(
                f"{source} contains archived InteractWeb-Bench cases; they cannot enter "
                "a current decision probe. See reports/research_scope_decision.md"
            )


def stable_order_key(benchmark: str, task: str, policy: str) -> str:
    return hashlib.sha256(f"{SEED}|{benchmark}|{task}|{policy}".encode()).hexdigest()


def command_build(args: argparse.Namespace) -> int:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    budget = ContextBudget(
        max_text_tokens=args.context_tokens,
        max_images=args.context_images,
        max_image_pixels=args.context_image_pixels,
    )
    cases = build_probe_cases(
        audit_events_path=args.audit_events,
        manual_annotations_path=args.manual_annotations,
        review_packets_path=args.review_packets,
        image_dir=output / "public_images",
    )
    reject_archived_interactweb(cases, source="decision-probe build input")
    requests: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for case in cases:
        labels.append(
            {
                "schema": "multimodalcode-decision-probe-label-1",
                "benchmark": case.benchmark,
                "task": case.task,
                "stratum": case.stratum,
                "detector_label": case.detector_label,
                "manual_label": case.manual_label,
                "external_outcome": case.external_outcome,
            }
        )
        for policy in args.policies:
            prompt, images, metadata = render_decision_prompt(
                case, policy_name=policy, budget=budget
            )
            request_id = hashlib.sha256(
                f"{case.benchmark}|{case.task}|{policy}|{metadata['prompt_sha256']}|"
                f"{'|'.join(metadata['image_sha256'])}".encode()
            ).hexdigest()[:24]
            requests.append(
                {
                    "schema": "multimodalcode-decision-probe-request-1",
                    "request_id": request_id,
                    "benchmark": case.benchmark,
                    "task": case.task,
                    "policy": policy,
                    "prompt": prompt,
                    "image_paths": images,
                    "metadata": metadata,
                    "stable_order_key": stable_order_key(
                        case.benchmark, case.task, policy
                    ),
                }
            )
    requests.sort(key=lambda row: row["stable_order_key"])
    labels.sort(key=lambda row: (row["benchmark"], row["task"]))
    write_jsonl(output / "requests.jsonl", requests)
    write_jsonl(output / "labels.private.jsonl", labels)
    manifest = {
        "schema": "multimodalcode-decision-probe-manifest-1",
        "seed": SEED,
        "case_count": len(cases),
        "request_count": len(requests),
        "policies": args.policies,
        "budget": {
            "max_text_tokens": budget.max_text_tokens,
            "max_images": budget.max_images,
            "max_image_pixels": budget.max_image_pixels,
        },
        "probe_scope": (
            "Retrospective pre-submit decision diagnostic; not official benchmark "
            "success and not an end-to-end agent run."
        ),
        "obligation_granularity": (
            "One coarse public task-operational obligation because released trajectories "
            "lack a shared public atomic checklist."
        ),
        "source_files": {
            "audit_events": {
                "path": str(args.audit_events.resolve()),
                "sha256": file_sha256(args.audit_events),
            },
            "manual_annotations": {
                "path": str(args.manual_annotations.resolve()),
                "sha256": file_sha256(args.manual_annotations),
            },
            "review_packets": {
                "path": str(args.review_packets.resolve()),
                "sha256": file_sha256(args.review_packets),
            },
        },
        "label_isolation": (
            "labels.private.jsonl is never loaded by the inference subcommand; prompts "
            "were constructed only from public requests and policy-visible events."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _run_one(
    backend: OpenAICompatibleBackend,
    row: dict[str, Any],
    artifact_root: Path,
    *,
    max_tokens: int,
    temperature: float,
    seed: int,
) -> dict[str, Any]:
    target = artifact_root / row["request_id"]
    target.mkdir(parents=True, exist_ok=True)
    request_path = target / "request.json"
    response_path = target / "response.txt"
    result_path = target / "result.json"
    request_payload = {
        **row,
        "model": backend.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "seed": seed,
    }
    if request_path.exists():
        existing = json.loads(request_path.read_text(encoding="utf-8"))
        if existing != request_payload:
            raise ValueError(f"Refusing mismatched resume request: {row['request_id']}")
    else:
        request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if result_path.exists():
        return json.loads(result_path.read_text(encoding="utf-8"))
    started = time.monotonic()
    try:
        response = backend.generate(
            GenerationRequest(
                prompt=row["prompt"],
                image_paths=list(row["image_paths"]),
                max_tokens=max_tokens,
                temperature=temperature,
                seed=seed,
                system_prompt=(
                    "You are making a coding-agent continuation decision from public, "
                    "policy-visible evidence. Do not assume hidden tests or outcomes."
                ),
            )
        )
        response_path.write_text(response, encoding="utf-8")
        parsed = parse_decision_response(response)
        result = {
            "schema": "multimodalcode-decision-probe-result-1",
            "request_id": row["request_id"],
            "benchmark": row["benchmark"],
            "task": row["task"],
            "policy": row["policy"],
            "status": "ok",
            "decision": parsed["decision"],
            "evidence_ids": parsed["evidence_ids"],
            "reason": parsed["reason"],
            "elapsed_seconds": time.monotonic() - started,
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        }
        if parsed.get("parse_recovery"):
            result["parse_recovery"] = parsed["parse_recovery"]
    except Exception as exc:  # keep other requests running; errors remain auditable
        result = {
            "schema": "multimodalcode-decision-probe-result-1",
            "request_id": row["request_id"],
            "benchmark": row["benchmark"],
            "task": row["task"],
            "policy": row["policy"],
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_seconds": time.monotonic() - started,
        }
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def command_run(args: argparse.Namespace) -> int:
    input_dir = args.input_dir.resolve()
    requests = read_jsonl(input_dir / "requests.jsonl")
    reject_archived_interactweb(requests, source=str(input_dir / "requests.jsonl"))
    # Deliberately do not open labels.private.jsonl in this code path.
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    backend = OpenAICompatibleBackend(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": args.thinking == "enabled",
            }
        },
    )
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                backend,
                row,
                output / "artifacts",
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                seed=args.seed,
            ): row
            for row in requests
        }
        for number, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            print(
                f"[{number}/{len(futures)}] {result['benchmark']}/{result['task']} "
                f"{result['policy']}: {result['status']} "
                f"{result.get('decision', result.get('error_type', ''))}",
                flush=True,
            )
    results.sort(key=lambda row: row["request_id"])
    write_jsonl(output / "results.jsonl", results)
    config = {
        "schema": "multimodalcode-decision-probe-run-1",
        "input_dir": str(input_dir),
        "requests_sha256": file_sha256(input_dir / "requests.jsonl"),
        "model": args.model,
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "seed": args.seed,
        "thinking": args.thinking,
        "workers": args.workers,
        "result_count": len(results),
        "ok_count": sum(row["status"] == "ok" for row in results),
        "error_count": sum(row["status"] != "ok" for row in results),
    }
    (output / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return int(config["error_count"] > 0)


def command_recover_parses(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    recovered = 0
    results: list[dict[str, Any]] = []
    for target in sorted((run_dir / "artifacts").glob("*")):
        result_path = target / "result.json"
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        response_path = target / "response.txt"
        if result.get("status") != "ok" and response_path.is_file():
            response = response_path.read_text(encoding="utf-8")
            try:
                parsed = parse_decision_response(response)
            except Exception:
                results.append(result)
                continue
            result = {
                **result,
                "status": "ok",
                "decision": parsed["decision"],
                "evidence_ids": parsed["evidence_ids"],
                "reason": parsed["reason"],
                "parse_recovery": parsed.get("parse_recovery", "strict_reparse"),
                "original_error_type": result.get("error_type"),
                "original_error": result.get("error"),
                "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
            }
            result.pop("error_type", None)
            result.pop("error", None)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            recovered += 1
        results.append(result)
    results.sort(key=lambda row: row["request_id"])
    write_jsonl(run_dir / "results.jsonl", results)
    config_path = run_dir / "run_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config.update(
        {
            "result_count": len(results),
            "ok_count": sum(row.get("status") == "ok" for row in results),
            "error_count": sum(row.get("status") != "ok" for row in results),
            "parse_recovered_count": sum(bool(row.get("parse_recovery")) for row in results),
        }
    )
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"recovered": recovered, **config}, ensure_ascii=False, indent=2))
    return int(config["error_count"] > 0)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def exact_mcnemar(a: list[bool], b: list[bool]) -> dict[str, Any]:
    discordant_a = sum(left and not right for left, right in zip(a, b))
    discordant_b = sum(right and not left for left, right in zip(a, b))
    total = discordant_a + discordant_b
    if total == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(total, k) for k in range(0, min(discordant_a, discordant_b) + 1))
        p_value = min(1.0, 2 * tail / (2**total))
    return {
        "a_only": discordant_a,
        "b_only": discordant_b,
        "discordant": total,
        "two_sided_exact_p": p_value,
    }


def command_summarize(args: argparse.Namespace) -> int:
    label_rows = read_jsonl(args.input_dir / "labels.private.jsonl")
    reject_archived_interactweb(label_rows, source=str(args.input_dir / "labels.private.jsonl"))
    labels = {
        (row["benchmark"], row["task"]): row
        for row in label_rows
    }
    results = [row for row in read_jsonl(args.run_dir / "results.jsonl") if row["status"] == "ok"]
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        label = labels[(row["benchmark"], row["task"])]
        cells[(row["benchmark"], row["policy"], label["stratum"])].append(row)
    summary_cells: list[dict[str, Any]] = []
    for (benchmark, policy, stratum), rows in sorted(cells.items()):
        decisions = Counter(row["decision"] for row in rows)
        submit = decisions["submit"]
        summary_cells.append(
            {
                "benchmark": benchmark,
                "policy": policy,
                "stratum": stratum,
                "n": len(rows),
                "decisions": dict(sorted(decisions.items())),
                "submit_rate": submit / len(rows),
                "submit_rate_wilson95": wilson_interval(submit, len(rows)),
                "verification_retry_rate": decisions["retry_verification"] / len(rows),
            }
        )
    by_key = {
        (row["benchmark"], row["task"], row["policy"]): row for row in results
    }
    paired: list[dict[str, Any]] = []
    target_policy = "conserved_frontier"
    for stratum in sorted({row["stratum"] for row in labels.values()}):
        case_keys = sorted(key for key, label in labels.items() if label["stratum"] == stratum)
        for policy in sorted({row["policy"] for row in results} - {target_policy}):
            pairs = [
                (
                    by_key.get((*key, policy)),
                    by_key.get((*key, target_policy)),
                )
                for key in case_keys
            ]
            pairs = [(left, right) for left, right in pairs if left and right]
            if not pairs:
                continue
            left_submit = [left["decision"] == "submit" for left, _ in pairs]
            right_submit = [right["decision"] == "submit" for _, right in pairs]
            paired.append(
                {
                    "stratum": stratum,
                    "baseline": policy,
                    "method": target_policy,
                    "n": len(pairs),
                    "baseline_submit_rate": sum(left_submit) / len(pairs),
                    "method_submit_rate": sum(right_submit) / len(pairs),
                    "paired_submit_mcnemar": exact_mcnemar(left_submit, right_submit),
                }
            )
    payload = {
        "schema": "multimodalcode-decision-probe-summary-1",
        "interpretation_boundary": (
            "Measures a retrospective pre-submit continuation decision, not official "
            "task success, discovery recall, or end-to-end repair conversion."
        ),
        "requested_result_count": len(read_jsonl(args.input_dir / "requests.jsonl")),
        "ok_result_count": len(results),
        "error_result_count": len(read_jsonl(args.run_dir / "results.jsonl")) - len(results),
        "cells": summary_cells,
        "paired_submit_comparisons": paired,
    }
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument(
        "--audit-events",
        type=Path,
        required=True,
    )
    build.add_argument(
        "--manual-annotations",
        type=Path,
        required=True,
    )
    build.add_argument(
        "--review-packets",
        type=Path,
        required=True,
    )
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--context-tokens", type=int, default=4096)
    build.add_argument("--context-images", type=int, default=1)
    build.add_argument("--context-image-pixels", type=int, default=4_000_000)
    build.add_argument(
        "--policies",
        nargs="+",
        choices=sorted(POLICY_FACTORIES),
        default=list(POLICY_FACTORIES),
    )
    build.set_defaults(function=command_build)

    run = subparsers.add_parser("run")
    run.add_argument("--input-dir", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--base-url", required=True)
    run.add_argument("--api-key", default="EMPTY")
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--max-tokens", type=int, default=256)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--thinking", choices=("disabled", "enabled"), default="disabled")
    run.add_argument("--timeout", type=float, default=600.0)
    run.set_defaults(function=command_run)

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--input-dir", type=Path, required=True)
    summarize.add_argument("--run-dir", type=Path, required=True)
    summarize.add_argument("--output", type=Path, required=True)
    summarize.set_defaults(function=command_summarize)

    recover = subparsers.add_parser("recover-parses")
    recover.add_argument("--run-dir", type=Path, required=True)
    recover.set_defaults(function=command_recover_parses)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())

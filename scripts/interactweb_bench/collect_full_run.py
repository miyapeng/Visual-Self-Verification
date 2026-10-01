#!/usr/bin/env python3
"""Freeze and audit a sharded InteractWeb generation run.

This is a provenance/collection utility, not an evaluator.  It does not edit
the released benchmark sources, regenerate cases, or synthesize scores.  Its
main purpose is to keep the original 404-case denominator visible when an
upstream worker terminates before writing ``interaction_history.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SHARD_PATTERN = re.compile(r"shard-(\d+)-of-(\d+)\.jsonl$")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASED_CONFIG = (
    PROJECT_ROOT / "evaluate/interactweb_bench/upstream/config.yaml"
)
DEFAULT_OBSERVED_RUNNER = (
    PROJECT_ROOT / "scripts/native_benchmarks/run_interactweb_full_shard_qwen35_job.sh"
)
DEFAULT_RELEASED_DEPLOY = (
    PROJECT_ROOT / "evaluate/interactweb_bench/upstream/scripts/deploy_qwen_3_5_9B.sh"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_released_models(path: Path) -> dict[str, str]:
    """Read the simple ``models`` mapping without adding a YAML dependency."""
    models: dict[str, str] = {}
    in_models = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line == "models:":
            in_models = True
            continue
        if not in_models:
            continue
        if not raw_line.startswith((" ", "\t")):
            break
        match = re.fullmatch(r"\s*([A-Za-z0-9_]+):\s*[\"']?([^\"'#]+?)[\"']?\s*", raw_line)
        if match:
            models[match.group(1)] = match.group(2).strip()
    if not models:
        raise RuntimeError(f"no models mapping found in released config: {path}")
    return models


def extract_integer_flag(path: Path, flag: str) -> int | None:
    match = re.search(
        rf"{re.escape(flag)}(?:\s+|=)([0-9][0-9_]*)",
        path.read_text(encoding="utf-8"),
    )
    return int(match.group(1).replace("_", "")) if match else None


def load_expected_cases(shard_data_dir: Path) -> tuple[list[dict[str, Any]], int]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    declared_shards: set[int] = set()
    declared_totals: set[int] = set()
    for shard_path in sorted(shard_data_dir.glob("shard-*-of-*.jsonl")):
        match = SHARD_PATTERN.fullmatch(shard_path.name)
        if not match:
            continue
        shard_index, num_shards = (int(value) for value in match.groups())
        declared_shards.add(shard_index)
        declared_totals.add(num_shards)
        for line_number, line in enumerate(
            shard_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            task_id = row.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise RuntimeError(f"missing task id in {shard_path}:{line_number}")
            if task_id in seen:
                raise RuntimeError(f"duplicate task id across shards: {task_id}")
            seen.add(task_id)
            cases.append(
                {
                    "task_id": task_id,
                    "shard": shard_index,
                    "difficulty": row.get("difficulty"),
                    "persona": row.get("persona"),
                }
            )
    if len(declared_totals) != 1:
        raise RuntimeError(f"inconsistent shard totals: {sorted(declared_totals)}")
    num_shards = next(iter(declared_totals))
    if declared_shards != set(range(num_shards)):
        raise RuntimeError(
            f"incomplete shard set: got {sorted(declared_shards)}, expected 0..{num_shards - 1}"
        )
    return cases, num_shards


def find_worker_exception(job_log: Path, task_id: str) -> str | None:
    if not job_log.is_file():
        return None
    marker = "Uncaught exception during task execution:"
    for line in job_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if marker in line and task_id in line:
            return line.strip()
    return None


def classify_case(
    *, run_root: Path, model: str, case: dict[str, Any]
) -> dict[str, Any]:
    shard = int(case["shard"])
    task_id = str(case["task_id"])
    model_root = (
        run_root / "shards" / f"shard-{shard:02d}" / "interactweb" / model
    )
    log_dir = model_root / "logs" / task_id
    workspace_dir = model_root / "workspaces" / task_id
    interaction_path = log_dir / "interaction_history.json"
    pending_path = log_dir / "pending_evaluation.json"
    partial_path = log_dir / "history.json"
    index_path = workspace_dir / "index.html"

    interaction = load_json(interaction_path)
    pending = load_json(pending_path)
    partial = load_json(partial_path)
    has_interaction = interaction is not None
    has_pending = pending is not None
    has_partial = partial is not None
    has_index = index_path.is_file()
    worker_exception = find_worker_exception(
        run_root / "shards" / f"shard-{shard:02d}" / "job.log", task_id
    )
    clarification_actions = 0
    if interaction:
        clarification_actions = int(
            interaction.get("path_distribution_stats", {}).get("PATH_A_CLARIFY", 0)
            or 0
        )
    elif partial:
        messages = partial.get("messages", [])
        clarification_actions = sum(
            1
            for message in messages if isinstance(messages, list)
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and re.search(
                r'<boltAction\s+type\s*=\s*["\']ask_user["\']',
                str(message.get("content", "")),
                re.IGNORECASE,
            )
        )

    if has_interaction and has_pending:
        generation_status = (
            "terminal_with_artifact" if has_index else "terminal_without_artifact"
        )
    elif not has_interaction and has_partial and worker_exception:
        generation_status = (
            "generation_error_with_artifact"
            if has_index
            else "generation_error_without_artifact"
        )
    elif has_interaction:
        generation_status = "terminal_without_pending_record"
    elif has_partial:
        generation_status = (
            "interrupted_with_artifact" if has_index else "interrupted_without_artifact"
        )
    else:
        generation_status = "missing_all_generation_records"

    if has_pending:
        judge_status = pending.get("status", "unknown")
    elif generation_status.startswith("generation_error_"):
        judge_status = "not_applicable_generation_error"
    else:
        judge_status = "missing"

    result = {
        **case,
        "generation_status": generation_status,
        "count_in_frozen_denominator": True,
        "official_judge_status": judge_status,
        "has_interaction_history": has_interaction,
        "has_pending_evaluation": has_pending,
        "has_partial_history": has_partial,
        "has_index_html": has_index,
        "stop_reason": pending.get("stop_reason") if pending else None,
        "worker_exception": worker_exception,
        "model_roles": pending.get("models") if pending else None,
        "clarification_actions": clarification_actions,
        "paths": {
            "log_dir": str(log_dir.relative_to(run_root)),
            "workspace_dir": str(workspace_dir.relative_to(run_root)),
            "interaction_history": (
                str(interaction_path.relative_to(run_root)) if interaction_path.is_file() else None
            ),
            "pending_evaluation": (
                str(pending_path.relative_to(run_root)) if pending_path.is_file() else None
            ),
            "partial_history": (
                str(partial_path.relative_to(run_root)) if partial_path.is_file() else None
            ),
            "index_html": str(index_path.relative_to(run_root)) if has_index else None,
        },
    }
    if not has_interaction and has_partial:
        result["partial_history_sha256"] = sha256(partial_path)
    if not has_interaction and has_index:
        result["index_html_sha256"] = sha256(index_path)
    return result


def collect(
    run_root: Path,
    shard_data_dir: Path,
    model: str,
    released_config: Path | None = None,
    observed_runner: Path | None = None,
    released_deploy: Path | None = None,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    shard_data_dir = shard_data_dir.resolve()
    expected, num_shards = load_expected_cases(shard_data_dir)
    rows = [classify_case(run_root=run_root, model=model, case=case) for case in expected]
    generation_counts = Counter(row["generation_status"] for row in rows)
    judge_counts = Counter(row["official_judge_status"] for row in rows)
    stop_counts = Counter(row["stop_reason"] for row in rows if row["stop_reason"])
    observed_model_roles = {
        json.dumps(row["model_roles"], ensure_ascii=False, sort_keys=True)
        for row in rows
        if isinstance(row["model_roles"], dict)
    }
    observed_model_configurations = [
        json.loads(value) for value in sorted(observed_model_roles)
    ]
    released_models = None
    matches_released_models = None
    role_mismatches: dict[str, dict[str, Any]] = {}
    released_config_record = None
    if released_config is not None:
        released_config = released_config.resolve()
        released_models = load_released_models(released_config)
        released_config_record = {
            "path": str(released_config),
            "sha256": sha256(released_config),
        }
        matches_released_models = (
            len(observed_model_configurations) == 1
            and observed_model_configurations[0] == released_models
        )
        roles = sorted(
            set(released_models).union(
                *(configuration.keys() for configuration in observed_model_configurations)
            )
        )
        for role in roles:
            observed = sorted(
                {
                    configuration.get(role)
                    for configuration in observed_model_configurations
                    if configuration.get(role) is not None
                }
            )
            expected = released_models.get(role)
            if observed != ([expected] if expected is not None else []):
                role_mismatches[role] = {"released": expected, "observed": observed}
    runtime_comparison = None
    if observed_runner is not None and released_deploy is not None:
        observed_runner = observed_runner.resolve()
        released_deploy = released_deploy.resolve()
        observed_max_model_len = extract_integer_flag(
            observed_runner, "--max-model-len"
        )
        released_max_model_len = extract_integer_flag(
            released_deploy, "--max-model-len"
        )
        runtime_comparison = {
            "observed_runner": {
                "path": str(observed_runner),
                "sha256": sha256(observed_runner),
            },
            "released_deploy_script": {
                "path": str(released_deploy),
                "sha256": sha256(released_deploy),
            },
            "max_model_len": {
                "observed": observed_max_model_len,
                "released": released_max_model_len,
                "matches": observed_max_model_len == released_max_model_len,
            },
        }
    pending_paths = [
        row["paths"]["pending_evaluation"]
        for row in rows
        if row["official_judge_status"] == "pending"
    ]
    unresolved = [
        row["task_id"]
        for row in rows
        if row["generation_status"]
        not in {
            "terminal_with_artifact",
            "terminal_without_artifact",
            "generation_error_with_artifact",
            "generation_error_without_artifact",
        }
    ]
    summary = {
        "expected_cases": len(rows),
        "num_shards": num_shards,
        "frozen_denominator": len(rows),
        "terminal_trajectories": sum(row["has_interaction_history"] for row in rows),
        "pending_official_judge": len(pending_paths),
        "workspaces_with_index_html": sum(row["has_index_html"] for row in rows),
        "trajectory_and_index_html": sum(
            row["has_interaction_history"] and row["has_index_html"] for row in rows
        ),
        "generation_statuses": dict(sorted(generation_counts.items())),
        "official_judge_statuses": dict(sorted(judge_counts.items())),
        "stop_reasons": dict(sorted(stop_counts.items())),
        "observed_model_configurations": observed_model_configurations,
        "released_reference_models": released_models,
        "matches_released_reference_models": matches_released_models,
        "model_role_mismatches": role_mismatches,
        "cases_invoking_user_simulator": sum(
            row["clarification_actions"] > 0 for row in rows
        ),
        "clarification_actions": sum(row["clarification_actions"] for row in rows),
        "runtime_configuration_comparison": runtime_comparison,
        "benchmark_comparability": (
            "released_role_configuration"
            if matches_released_models is True
            else "diagnostic_nonreleased_role_configuration"
            if matches_released_models is False
            else "not_checked"
        ),
        "unclassified_or_incomplete_ids": unresolved,
        "audit_ok": len(rows) == 404 and not unresolved,
    }
    return {
        "schema": "interactweb-generation-audit-v1",
        "purpose": "provenance and denominator audit only; no benchmark score is synthesized",
        "run_root": str(run_root),
        "shard_data_dir": str(shard_data_dir),
        "model": model,
        "released_config": released_config_record,
        "summary": summary,
        "pending_official_judge_paths": pending_paths,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--shard-data-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--released-config", type=Path, default=DEFAULT_RELEASED_CONFIG
    )
    parser.add_argument(
        "--observed-runner", type=Path, default=DEFAULT_OBSERVED_RUNNER
    )
    parser.add_argument(
        "--released-deploy", type=Path, default=DEFAULT_RELEASED_DEPLOY
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    report = collect(
        arguments.run_root,
        arguments.shard_data_dir,
        arguments.model,
        arguments.released_config,
        arguments.observed_runner,
        arguments.released_deploy,
    )
    output = arguments.output or arguments.run_root / "generation_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["audit_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

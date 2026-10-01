#!/usr/bin/env python3
"""Stage sharded InteractWeb results and invoke the frozen released analyzer.

The released analyzer rewrites some trajectory statistics in place.  To keep
raw benchmark outputs immutable, this adapter copies only terminal histories
into a new, non-existing analysis directory and runs the released function on
those copies.  Score extraction below is a transparent summary of the CSV
written by that function; the CSV and captured stdout remain authoritative.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = PROJECT_ROOT / "evaluate/interactweb_bench/upstream"
SOURCE_ROOT = UPSTREAM / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from experiment import result_analyze as official  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    task_ids = [row.get("id") for row in rows]
    if not all(isinstance(task_id, str) and task_id for task_id in task_ids):
        raise RuntimeError(f"dataset contains a missing task id: {path}")
    if len(task_ids) != len(set(task_ids)):
        raise RuntimeError(f"dataset contains duplicate task ids: {path}")
    return rows


def terminal_evaluation(path: Path) -> dict[str, Any] | None:
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    trajectory = history.get("trajectory", []) if isinstance(history, dict) else []
    for item in reversed(trajectory if isinstance(trajectory, list) else []):
        info = item.get("debug_info") if isinstance(item, dict) else None
        evaluation = info.get("evaluation_detail") if isinstance(info, dict) else None
        if not isinstance(evaluation, dict) or info.get("is_final") is not True:
            continue
        status = str(evaluation.get("status", "")).upper()
        tcr = evaluation.get("tcr")
        if status in {"PASS", "FAIL", "CRASHED", "ERROR"} and isinstance(
            tcr, (int, float)
        ):
            return {"status": status, "tcr": float(tcr)}
    return None


def locate_histories(
    run_root: Path, model: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    located: list[dict[str, Any]] = []
    for row in rows:
        task_id = row["id"]
        matches = sorted(
            run_root.glob(
                f"shards/shard-*/interactweb/{model}/logs/{task_id}/interaction_history.json"
            )
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one terminal history for {task_id}, found {len(matches)}"
            )
        pending = matches[0].with_name("pending_evaluation.json")
        if pending.exists():
            raise RuntimeError(f"deferred evaluation is not an official result: {task_id}")
        evaluation = terminal_evaluation(matches[0])
        if evaluation is None:
            raise RuntimeError(f"missing valid terminal official evaluation: {task_id}")
        located.append(
            {
                "task_id": task_id,
                "source": matches[0],
                "sha256": sha256(matches[0]),
                "evaluation": evaluation,
            }
        )
    return located


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_csv(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_role: dict[str, list[float]] = defaultdict(list)
    by_difficulty: dict[str, list[float]] = defaultdict(list)
    tcr_values: list[float] = []
    clean_values: list[float] = []
    status_counts: Counter[str] = Counter()
    abnormal = 0
    for row in rows:
        tcr = float(row["tcr_score"])
        clean = float(row["tcr_score_no_hallu"])
        tcr_values.append(tcr)
        clean_values.append(clean)
        by_role[row["role"]].append(clean)
        by_difficulty[row["difficulty"]].append(clean)
        status_counts[row["final_state"]] += 1
        abnormal += str(row["is_abnormal"]).lower() == "true"
    return {
        "cases": len(rows),
        "global_tcr": mean(tcr_values),
        "global_clean_tcr": mean(clean_values),
        "clean_tcr_by_role": {
            key: mean(values) for key, values in sorted(by_role.items())
        },
        "clean_tcr_by_difficulty": {
            key: mean(values) for key, values in sorted(by_difficulty.items())
        },
        "final_states": dict(sorted(status_counts.items())),
        "abnormal_cases": abnormal,
    }


def aggregate(
    run_root: Path,
    model: str,
    data_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    data_path = data_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite analysis directory: {output_dir}")
    rows = load_rows(data_path)
    if len(rows) != 404:
        raise RuntimeError(f"expected released 404-case set, got {len(rows)}")
    located = locate_histories(run_root, model, rows)

    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True)
    for record in located:
        destination = logs_dir / record["task_id"] / "interaction_history.json"
        destination.parent.mkdir(parents=True)
        shutil.copy2(record["source"], destination)

    analyzer_stdout = output_dir / "official_result_analyze.stdout.txt"
    with analyzer_stdout.open("w", encoding="utf-8") as handle:
        with contextlib.redirect_stdout(handle):
            official.analyze_batch_trajectories(
                str(logs_dir), dataset_paths=[str(data_path)]
            )

    csv_path = output_dir / "logs_summary_with_roles.csv"
    if not csv_path.is_file():
        raise RuntimeError("released analyzer did not create its summary CSV")
    score_summary = summarize_csv(csv_path)
    if score_summary["cases"] != 404:
        raise RuntimeError(
            f"released analyzer aggregated {score_summary['cases']} rather than 404 cases"
        )

    manifest = {
        "schema": "interactweb-official-aggregate-v1",
        "run_root": str(run_root),
        "model": model,
        "dataset": {"path": str(data_path), "sha256": sha256(data_path)},
        "released_analyzer": {
            "path": str((SOURCE_ROOT / "experiment/result_analyze.py").resolve()),
            "sha256": sha256(SOURCE_ROOT / "experiment/result_analyze.py"),
            "stdout": str(analyzer_stdout),
            "csv": str(csv_path),
        },
        "score_summary_derived_from_released_csv": score_summary,
        "source_histories": [
            {
                **{key: value for key, value in record.items() if key != "source"},
                "source": str(record["source"]),
            }
            for record in located
        ],
    }
    (output_dir / "aggregate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--model", default="Qwen3.5-9B")
    parser.add_argument("--data-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    manifest = aggregate(
        arguments.run_root,
        arguments.model,
        arguments.data_path,
        arguments.output_dir,
    )
    print(
        json.dumps(
            manifest["score_summary_derived_from_released_csv"],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

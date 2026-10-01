#!/usr/bin/env python3
"""Resume-safe clean-container evaluation controller for SWE-MM dev patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_CONFIG = PROJECT_ROOT / "configs" / "swe_mm" / "official_dev.json"
EVAL_CONFIG = PROJECT_ROOT / "configs" / "swe_mm" / "clean_eval.json"
INSTANCE_IDS = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "instance_ids.txt"
IMAGE_MANIFEST = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "private_images.jsonl"
ACTIVE = {"RUNNING", "QUEUING", "PENDING", "UNKNOWN"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_clusterx(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # ClusterX talks to the internal SSP control plane.  The outbound proxy
    # intermittently leaves status queries waiting forever, so force the same
    # direct route used by interactive clusterx invocations.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "compute.pjlab.org.cn,10.140.100.1"
    env["no_proxy"] = env["NO_PROXY"]
    command = ["clusterx", *arguments]
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=env,
            timeout=20 if arguments and arguments[0] == "get-job" else 120,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return subprocess.CompletedProcess(command, 124, output + "\nclusterx command timed out\n")


def query_status(job_id: str) -> tuple[str, str]:
    result = run_clusterx(["get-job", job_id])
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout).replace("\r", "")
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1), output
    lowered = output.lower()
    if result.returncode != 0 and any(value in lowered for value in ("404", "not found", "不存在")):
        return "NOT_FOUND", output
    return "UNKNOWN", output


def job_name(model_key: str, index: int, instance_id: str, suffix: str) -> str:
    short_model = "q35" if model_key == "q35" else "q3v"
    digest = hashlib.sha1(instance_id.encode()).hexdigest()[:6]
    return f"mmc-ce-{short_model}-{index:03d}-{digest}-{suffix}"


def paths(model: dict, instance_id: str) -> tuple[Path, Path, Path]:
    run_root = PROJECT_ROOT / "runs" / "swe_mm_official" / model["run_label"]
    agent_case = run_root / "agents" / model["model"] / "swe-mm" / instance_id
    clean_case = run_root / "clean_evaluation" / model["model"] / instance_id
    return agent_case / "clusterx-summary.json", agent_case / "patch.diff", clean_case / "clean-eval-summary.json"


def write_no_patch_summary(summary: Path, model: dict, instance_id: str) -> None:
    write_json(summary, {
        "instance_id": instance_id,
        "model": model["model"],
        "transport": "not-submitted-no-patch",
        "evaluator_status": 2,
        "report_exists": False,
        "resolved": False,
        "outcome": "no_patch",
        "finished_at_utc": utc_now(),
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=("q35", "q3vl30"), default=("q35", "q3vl30"))
    parser.add_argument("--max-active-per-model", type=int, default=2)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.max_active_per_model < 1:
        parser.error("--max-active-per-model must be positive")

    run_config = read_json(RUN_CONFIG, {})
    eval_config = read_json(EVAL_CONFIG, {})
    runtime = PROJECT_ROOT / eval_config["runtime"]
    case_script = runtime / "scripts" / "swe_mm" / "run_clean_eval_case.sh"
    if not (runtime / "runtime_manifest.json").is_file() or not case_script.is_file():
        raise RuntimeError(f"Frozen clean-eval runtime is missing: {runtime}")
    state_path = PROJECT_ROOT / eval_config["state"]
    suffix = eval_config["job_suffix"]
    models = {row["key"]: row for row in run_config["models"] if row["key"] in args.models}
    instance_ids = [line.strip() for line in INSTANCE_IDS.read_text().splitlines() if line.strip()]
    images = {
        row["instance_id"]: row["target"]
        for row in (json.loads(line) for line in IMAGE_MANIFEST.read_text().splitlines() if line.strip())
    }
    missing_images = sorted(set(instance_ids) - set(images))
    if missing_images:
        raise RuntimeError(f"Missing mirrored images: {missing_images}")

    state = read_json(state_path, {"schema": 1, "created_at_utc": utc_now(), "jobs": {}})
    state.setdefault("jobs", {})
    state["run_config"] = str(RUN_CONFIG)
    state["eval_config"] = str(EVAL_CONFIG)

    while True:
        all_done = True
        for model_key, model in models.items():
            active = 0
            pending: list[tuple[int, str, dict]] = []
            for index, instance_id in enumerate(instance_ids):
                key = f"{model_key}:{instance_id}"
                generation_summary, patch, clean_summary = paths(model, instance_id)
                record = state["jobs"].setdefault(key, {
                    "model_key": model_key,
                    "model": model["model"],
                    "run_label": model["run_label"],
                    "instance_id": instance_id,
                    "image": images[instance_id],
                    "job_id": job_name(model_key, index, instance_id, suffix),
                    "status": "WAITING_GENERATION",
                    "retries": 0,
                })
                if clean_summary.is_file():
                    record["status"] = "COMPLETED"
                    record["completed_from_artifact"] = True
                    continue
                if not generation_summary.is_file():
                    record["status"] = "WAITING_GENERATION"
                    all_done = False
                    continue
                if not patch.is_file() or not patch.read_text(encoding="utf-8").strip():
                    write_no_patch_summary(clean_summary, model, instance_id)
                    record["status"] = "COMPLETED"
                    record["outcome"] = "no_patch"
                    continue

                status = record["status"]
                if status not in {"NEW", "NOT_FOUND", "WAITING_GENERATION"}:
                    status, detail = query_status(record["job_id"])
                    record["status"] = status
                    record["last_checked_at_utc"] = utc_now()
                    if status == "UNKNOWN":
                        record["last_status_output"] = detail[-2000:]
                status = record["status"]
                if status in ACTIVE:
                    active += 1
                    all_done = False
                elif status in {"FAILED", "STOPPED", "SUCCEEDED", "INFRA_FAILED"}:
                    # The clean summary is the commit marker.  Even a job that
                    # ClusterX calls SUCCEEDED must be retried when that marker
                    # is absent (for example, a shared-filesystem write was
                    # interrupted after process exit).
                    retries = int(record.get("retries", 0))
                    if retries < args.max_retries:
                        retries += 1
                        record["retries"] = retries
                        record["previous_terminal_status"] = status
                        record["job_id"] = f"{job_name(model_key, index, instance_id, suffix)}r{retries}"
                        record["status"] = "NOT_FOUND"
                        pending.append((index, instance_id, record))
                    else:
                        record["status"] = "INFRA_FAILED"
                    all_done = False
                else:
                    record["status"] = "NEW" if status == "WAITING_GENERATION" else status
                    pending.append((index, instance_id, record))
                    all_done = False

            slots = max(0, args.max_active_per_model - active)
            for index, instance_id, record in pending[:slots]:
                existing, detail = query_status(record["job_id"])
                if existing != "NOT_FOUND":
                    record["status"] = existing
                    record["last_checked_at_utc"] = utc_now()
                    if existing == "UNKNOWN":
                        record["last_status_output"] = detail[-2000:]
                    continue
                command = [
                    "run",
                    "--job-name", record["job_id"],
                    "--num-nodes", "1",
                    "--gpus-per-task", "0",
                    "--cpus-per-task", "8",
                    "--memory-per-task", "32",
                    "--shm-size-gib", "8",
                    "--no-env",
                    "--image", record["image"],
                    "bash", str(case_script), instance_id, model["model"], model["run_label"], str(runtime),
                ]
                print(f"[submit-clean-eval] {model_key}:{instance_id} -> {record['job_id']}", flush=True)
                submitted = run_clusterx(command)
                record["submit_returncode"] = submitted.returncode
                record["submit_output"] = submitted.stdout[-4000:]
                record["submitted_at_utc"] = utc_now()
                if submitted.returncode != 0:
                    record["status"] = "SUBMIT_FAILED"
                    write_json(state_path, state)
                    raise RuntimeError(
                        f"ClusterX clean-eval submission failed for {model_key}:{instance_id}:\n{submitted.stdout}"
                    )
                record["status"] = "QUEUING"
                write_json(state_path, state)

        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        counts = {}
        for record in state["jobs"].values():
            if record["model_key"] in models:
                counts[record["status"]] = counts.get(record["status"], 0) + 1
        print(f"[clean-eval-state] {json.dumps(counts, sort_keys=True)}", flush=True)
        if args.once or all_done:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Rolling, resume-safe ClusterX submission controller for SWE-MM dev."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "configs" / "swe_mm" / "official_dev.json"
INSTANCE_IDS = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "instance_ids.txt"
IMAGE_MANIFEST = PROJECT_ROOT / "data" / "swe_mm" / "dev" / "private_images.jsonl"
STATE_PATH = PROJECT_ROOT / "runs" / "swe_mm_official" / "clusterx_state.json"
SERVER_ROOT = PROJECT_ROOT / "runs" / "agent_smoke" / "servers"
TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED"}
ACTIVE = {"RUNNING", "QUEUING", "PENDING"}


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
    # The SSP control plane is an internal endpoint.  Routing it through the
    # outbound HTTP proxy can hang indefinitely, which previously froze the
    # resume controller while it was polling an old job.
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


def server_ready(model_spec: dict) -> tuple[bool, str]:
    ready_path = SERVER_ROOT / model_spec["server_run"] / "ready.json"
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, f"{ready_path}: {exc}"
    if ready.get("model") != model_spec["model"]:
        return False, f"model mismatch in {ready_path}"
    request = urllib.request.Request(ready["endpoint"].rstrip("/") + "/models")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            if response.status != 200:
                return False, f"HTTP {response.status} from {ready['endpoint']}"
    except Exception as exc:
        return False, f"{ready['endpoint']}: {exc}"
    return True, ready["endpoint"]


def job_name(model_key: str, index: int, instance_id: str, suffix: str = "") -> str:
    digest = hashlib.sha1(instance_id.encode()).hexdigest()[:6]
    suffix_part = f"-{suffix}" if suffix else ""
    return f"mmc-swm-{model_key}-{index:03d}-{digest}{suffix_part}"


def case_complete(run_label: str, model: str, instance_id: str) -> bool:
    return (
        PROJECT_ROOT
        / "runs"
        / "swe_mm_official"
        / run_label
        / "agents"
        / model
        / "swe-mm"
        / instance_id
        / "clusterx-summary.json"
    ).is_file()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=("q35", "q3vl30"), default=("q35", "q3vl30"))
    parser.add_argument("--limit", type=int, default=0, help="first N cases per model; 0 means all 102")
    parser.add_argument("--max-active-per-model", type=int, default=4)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true", help="refresh and fill available slots once, then exit")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args()
    if args.max_active_per_model < 1:
        parser.error("--max-active-per-model must be positive")
    if args.max_retries < 0:
        parser.error("--max-retries must be non-negative")

    config = read_json(CONFIG_PATH, {})
    state_path = args.state or PROJECT_ROOT / config["runtime"].get(
        "controller_state", str(STATE_PATH.relative_to(PROJECT_ROOT))
    )
    job_suffix = config["runtime"].get("job_suffix", "")
    runtime_root = PROJECT_ROOT / config["runtime"]["snapshot"]
    case_script = runtime_root / "scripts" / "swe_mm" / "run_official_case.sh"
    if not (runtime_root / "runtime_manifest.json").is_file() or not case_script.is_file():
        raise RuntimeError(
            f"Frozen runtime is missing: {runtime_root}. Run freeze_official_runtime.py first."
        )
    models = {row["key"]: row for row in config["models"] if row["key"] in args.models}
    instance_ids = [line.strip() for line in INSTANCE_IDS.read_text().splitlines() if line.strip()]
    if args.limit:
        instance_ids = instance_ids[: args.limit]
    images = {
        row["instance_id"]: row["target"]
        for row in (json.loads(line) for line in IMAGE_MANIFEST.read_text().splitlines() if line.strip())
    }
    missing_images = sorted(set(instance_ids) - set(images))
    if missing_images:
        raise RuntimeError(f"Missing mirrored images: {missing_images}")

    state = read_json(state_path, {"schema": 1, "created_at_utc": utc_now(), "jobs": {}})
    state.setdefault("jobs", {})
    state["config"] = str(CONFIG_PATH)
    state["updated_at_utc"] = utc_now()

    while True:
        all_terminal = True
        for model_key, model_spec in models.items():
            model_server_ready, server_detail = server_ready(model_spec)
            if not model_server_ready:
                print(f"[wait-server] {model_key}: {server_detail}", flush=True)
            active = 0
            pending: list[tuple[int, str, str, dict]] = []
            for index, instance_id in enumerate(instance_ids):
                key = f"{model_key}:{instance_id}"
                record = state["jobs"].setdefault(
                    key,
                    {
                        "model_key": model_key,
                        "model": model_spec["model"],
                        "run_label": model_spec["run_label"],
                        "instance_id": instance_id,
                        "image": images[instance_id],
                        "job_id": job_name(model_key, index, instance_id, job_suffix),
                        "status": "NEW",
                    },
                )
                if case_complete(model_spec["run_label"], model_spec["model"], instance_id):
                    record["status"] = "SUCCEEDED"
                    record["completed_from_artifact"] = True
                    # The durable artifact is authoritative.  Do not pass this
                    # record into terminal-without-artifact retry handling.
                    continue
                elif record["status"] not in {"NEW", "NOT_FOUND"}:
                    status, detail = query_status(record["job_id"])
                    record["status"] = status
                    record["last_checked_at_utc"] = utc_now()
                    if status == "UNKNOWN":
                        record["last_status_output"] = detail[-2000:]
                status = record["status"]
                if status in ACTIVE or status == "UNKNOWN":
                    active += 1
                    all_terminal = False
                elif status in TERMINAL:
                    # A ClusterX success is not a completed benchmark case
                    # unless the durable artifact reached shared storage.  A
                    # controller restart must therefore be able to retry
                    # SUCCEEDED-without-artifact as well as FAILED/STOPPED.
                    retries = int(record.get("retries", 0))
                    if args.retry_failed and retries < args.max_retries:
                        retries += 1
                        record["retries"] = retries
                        record["previous_terminal_status"] = status
                        record["status"] = "NOT_FOUND"
                        record["job_id"] = (
                            f"{job_name(model_key, index, instance_id, job_suffix)}-r{retries}"
                        )
                        pending.append((index, instance_id, key, record))
                        all_terminal = False
                    else:
                        record["retry_exhausted"] = bool(args.retry_failed)
                else:
                    pending.append((index, instance_id, key, record))
                    all_terminal = False

            slots = max(0, args.max_active_per_model - active) if model_server_ready else 0
            for index, instance_id, key, record in pending[:slots]:
                # A deterministic name makes restart-before-state-write safe.
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
                    "bash", str(case_script), instance_id, model_spec["model"],
                    model_spec["server_run"], model_spec["run_label"],
                ]
                print(f"[submit] {key} -> {record['job_id']}", flush=True)
                submitted = run_clusterx(command)
                record["submit_returncode"] = submitted.returncode
                record["submit_output"] = submitted.stdout[-4000:]
                record["submitted_at_utc"] = utc_now()
                if submitted.returncode != 0:
                    record["status"] = "SUBMIT_FAILED"
                    write_json(state_path, state)
                    raise RuntimeError(
                        f"ClusterX submission failed for {key}:\n{submitted.stdout}"
                    )
                record["status"] = "QUEUING"
                write_json(state_path, state)

        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        counts: dict[str, int] = {}
        for record in state["jobs"].values():
            if record["model_key"] in models and record["instance_id"] in instance_ids:
                counts[record["status"]] = counts.get(record["status"], 0) + 1
        print(f"[state] {json.dumps(counts, sort_keys=True)}", flush=True)
        if args.once or all_terminal:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

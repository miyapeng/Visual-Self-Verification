#!/usr/bin/env python3
"""Rolling, resume-safe ClusterX controller for Vision2Web generation."""

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
CONFIG_PATH = PROJECT_ROOT / "configs" / "vision2web" / "qwen35_generation.json"
INSTANCE_IDS = PROJECT_ROOT / "data" / "vision2web" / "instance_ids.txt"
SERVER_ROOT = PROJECT_ROOT / "runs" / "agent_smoke" / "servers"
ACTIVE = {"RUNNING", "QUEUING", "PENDING", "UNKNOWN"}
TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED"}


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
            timeout=30 if arguments and arguments[0] == "get-job" else 120,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return subprocess.CompletedProcess(command, 124, output + "\nclusterx command timed out\n")


def query_status(job_id: str) -> tuple[str, str]:
    result = run_clusterx(["get-job", job_id, "--no-verbose"])
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout).replace("\r", "")
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1), output
    lowered = output.lower()
    if result.returncode != 0 and any(value in lowered for value in ("404", "not found", "不存在")):
        return "NOT_FOUND", output
    return "UNKNOWN", output


def server_ready(config: dict) -> tuple[bool, str]:
    ready_path = SERVER_ROOT / config["model"]["server_run"] / "ready.json"
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, f"{ready_path}: {exc}"
    if ready.get("model") != config["model"]["name"]:
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


def safe_case(case_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", case_id).strip("._")


def job_name(index: int, case_id: str, retry: int = 0) -> str:
    digest = hashlib.sha1(case_id.encode()).hexdigest()[:6]
    suffix = f"-r{retry}" if retry else ""
    return f"mmc-v2w-q35-{index:03d}-{digest}-n2{suffix}"


def run_dir(config: dict, case_id: str) -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "vision2web_generation"
        / config["run_label"]
        / "agents"
        / f"litellm_proxy__{config['model']['name']}"
        / "vision2web"
        / "official"
        / safe_case(case_id)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-active", type=int, default=2)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-infra-retries", type=int, default=2)
    parser.add_argument(
        "--retry-infra-forever",
        action="store_true",
        help="Keep retrying pre-container ClusterX failures instead of abandoning cases.",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.max_active < 1:
        parser.error("--max-active must be positive")

    config = read_json(CONFIG_PATH, {})
    runtime = PROJECT_ROOT / config["runtime"]
    case_script = runtime / "scripts" / "vision2web" / "run_generation_case.sh"
    manifest_path = runtime / "runtime_manifest.json"
    if not manifest_path.is_file() or not case_script.is_file():
        raise RuntimeError(f"frozen runtime is missing: {runtime}")
    state_path = PROJECT_ROOT / config["state"]
    instance_ids = [line.strip() for line in INSTANCE_IDS.read_text().splitlines() if line.strip()]
    if len(instance_ids) != config["dataset"]["instances"]:
        raise RuntimeError(f"expected 193 cases, found {len(instance_ids)}")

    state = read_json(state_path, {"schema": 1, "created_at_utc": utc_now(), "jobs": {}})
    state["config"] = str(CONFIG_PATH)
    state["runtime_manifest"] = read_json(manifest_path, {})

    while True:
        ready, server_detail = server_ready(config)
        if not ready:
            print(f"[wait-server] {server_detail}", flush=True)
        active = 0
        pending: list[tuple[int, str, dict]] = []
        all_terminal = True
        for index, case_id in enumerate(instance_ids):
            record = state["jobs"].setdefault(
                case_id,
                {
                    "case_id": case_id,
                    "job_id": job_name(index, case_id),
                    "status": "NEW",
                    "retries": 0,
                },
            )
            case_root = run_dir(config, case_id)
            artifact = case_root / "generation-summary.json"
            if artifact.is_file():
                result = read_json(case_root / "result.json", {})
                record["status"] = "COMPLETED"
                record["result_status"] = result.get("status", "unknown")
                record["result"] = str(artifact)
                continue
            if record["status"] not in {"NEW", "NOT_FOUND"}:
                status, detail = query_status(record["job_id"])
                record["status"] = status
                record["last_checked_at_utc"] = utc_now()
                if status == "UNKNOWN":
                    record["last_status_output"] = detail[-2000:]
            status = record["status"]
            if status in ACTIVE:
                active += 1
                all_terminal = False
            elif status in TERMINAL:
                retries = int(record.get("retries", 0))
                if retries < args.max_infra_retries or args.retry_infra_forever:
                    retries += 1
                    record["retries"] = retries
                    record["previous_terminal_status"] = status
                    record["job_id"] = job_name(index, case_id, retries)
                    record["status"] = "NOT_FOUND"
                    pending.append((index, case_id, record))
                    all_terminal = False
                else:
                    record["status"] = "INFRA_FAILED"
            elif status == "INFRA_FAILED":
                if args.retry_infra_forever:
                    retries = int(record.get("retries", 0)) + 1
                    record["retries"] = retries
                    record["previous_terminal_status"] = status
                    record["job_id"] = job_name(index, case_id, retries)
                    record["status"] = "NOT_FOUND"
                    pending.append((index, case_id, record))
                    all_terminal = False
                else:
                    continue
            else:
                pending.append((index, case_id, record))
                all_terminal = False

        slots = max(0, args.max_active - active) if ready else 0
        for index, case_id, record in pending[:slots]:
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
                "--cpus-per-task", "16",
                "--memory-per-task", "64",
                "--shm-size-gib", "16",
                "--no-env",
                "--image", config["image"],
                "bash", str(case_script), case_id, config["model"]["name"],
                config["model"]["server_run"], config["run_label"],
            ]
            print(f"[submit] {case_id} -> {record['job_id']}", flush=True)
            submitted = run_clusterx(command)
            record["submit_returncode"] = submitted.returncode
            record["submit_output"] = submitted.stdout[-4000:]
            record["submitted_at_utc"] = utc_now()
            if submitted.returncode != 0:
                record["status"] = "SUBMIT_FAILED"
                write_json(state_path, state)
                raise RuntimeError(f"ClusterX submission failed for {case_id}:\n{submitted.stdout}")
            record["status"] = "QUEUING"

        state["updated_at_utc"] = utc_now()
        counts: dict[str, int] = {}
        result_counts: dict[str, int] = {}
        for record in state["jobs"].values():
            counts[record["status"]] = counts.get(record["status"], 0) + 1
            if record["status"] == "COMPLETED":
                outcome = record.get("result_status", "unknown")
                result_counts[outcome] = result_counts.get(outcome, 0) + 1
        state["counts"] = counts
        state["result_counts"] = result_counts
        write_json(state_path, state)
        print(f"[state] {json.dumps(counts, sort_keys=True)} results={json.dumps(result_counts, sort_keys=True)}", flush=True)
        if args.once or all_terminal:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

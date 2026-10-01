#!/usr/bin/env python3
"""Resume-safe CPU-only official evaluation for the completed Vision2Web run.

This is submission glue only.  Each scoreable workspace is forwarded to
``evaluate/vision2web/run_clusterx.py``, which in turn invokes the frozen,
unmodified Vision2Web evaluator.  Credentials are read from the process
environment and are never written to controller state.
"""

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
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = (
    PROJECT_ROOT
    / "runs/vision2web_generation/qwen35-9b-openhands-official-v2"
    / "agents/litellm_proxy__Qwen3.5-9B/vision2web/official"
)
OUTPUT_ROOT = (
    PROJECT_ROOT
    / "runs/vision2web_official_eval/qwen35-9b-openhands-official-v2-judge-gemini31-v2"
)
RUNNER = PROJECT_ROOT / "evaluate/vision2web/run_clusterx.py"
IMAGE = "registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939"
IMAGE_DIGEST = "sha256:3d2fa9b0999a1fbb10c9d4ccc653c7d5560a713668d6c18c80d0d06e36e43056"
MODEL_LABEL = "Qwen3.5-9B"
FRAMEWORK_LABEL = "openhands"
FUNCTIONAL_MODEL = "claude-sonnet-4-5-20250929"
VISUAL_MODEL = "gemini-3.1-pro-preview"
FUNCTIONAL_BASE_URL = "http://35.220.164.252:3888"
VISUAL_BASE_URL = "http://35.220.164.252:3888/v1"
ACTIVE = {"RUNNING", "QUEUING", "PENDING", "UNKNOWN"}
TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")


def discover() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary_path in sorted(SOURCE_ROOT.glob("*/generation-summary.json")):
        summary = read_json(summary_path, {})
        case_id = str(summary.get("case_id") or "")
        if "/" not in case_id:
            raise RuntimeError(f"invalid case id in {summary_path}: {case_id!r}")
        workspace = summary_path.parent / "workspace"
        has_result = (summary_path.parent / "result.json").is_file()
        has_start = (workspace / "start.sh").is_file()
        has_prototypes = (workspace / "prototypes").is_dir()
        scoreable = has_result and has_start and has_prototypes
        reasons = []
        if not has_result:
            reasons.append("missing result.json")
        if not has_start:
            reasons.append("missing workspace/start.sh")
        if not has_prototypes:
            reasons.append("missing workspace/prototypes")
        rows.append(
            {
                "case_id": case_id,
                "generation_status": summary.get("result_status", "missing"),
                "workspace": str(workspace),
                "scoreable": scoreable,
                "unscoreable_reason": "; ".join(reasons) if reasons else None,
            }
        )
    if len(rows) != 193:
        raise RuntimeError(f"expected 193 completed generation cases, found {len(rows)}")
    return sorted(rows, key=lambda row: row["case_id"])


def evaluation_summary(case_id: str) -> Path:
    task, project = case_id.split("/", 1)
    return OUTPUT_ROOT / MODEL_LABEL / task / safe(project) / "clusterx_evaluation.json"


def job_name(index: int, case_id: str, retry: int = 0) -> str:
    digest = hashlib.sha1(case_id.encode()).hexdigest()[:7]
    suffix = f"-r{retry}" if retry else ""
    return f"mmc-v2w-gm2-{index:03d}-{digest}{suffix}"


def clusterx(arguments: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        environment.pop(key, None)
    environment["NO_PROXY"] = "compute.pjlab.org.cn,10.140.100.1"
    environment["no_proxy"] = environment["NO_PROXY"]
    try:
        return subprocess.run(
            ["clusterx", *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return subprocess.CompletedProcess(arguments, 124, output + "\nclusterx timeout\n")


def query(job_id: str) -> tuple[str, str]:
    result = clusterx(["get-job", job_id, "--no-verbose"], timeout=30)
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout).replace("\r", "")
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1), output
    if result.returncode != 0 and any(
        token in output.lower() for token in ("404", "not found", "不存在")
    ):
        return "NOT_FOUND", output
    return "UNKNOWN", output


def required_environment() -> tuple[list[str], list[str]]:
    api_key = os.environ.get("LLM_RELAY_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_RELAY_API_KEY is required")
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not http_proxy or not https_proxy:
        raise RuntimeError("run proxy_on before starting the evaluation controller")
    values = {
        "VISION2WEB_FUNCTIONAL_MODEL": FUNCTIONAL_MODEL,
        "VISION2WEB_FUNCTIONAL_API_KEY": api_key,
        "VISION2WEB_FUNCTIONAL_BASE_URL": FUNCTIONAL_BASE_URL,
        "VISION2WEB_VISUAL_MODEL": VISUAL_MODEL,
        "VISION2WEB_VISUAL_API_KEY": api_key,
        "VISION2WEB_VISUAL_BASE_URL": VISUAL_BASE_URL,
        "VISION2WEB_RUNTIME_IMAGE_DIGEST": IMAGE_DIGEST,
        "HTTP_PROXY": http_proxy,
        "HTTPS_PROXY": https_proxy,
        "http_proxy": http_proxy,
        "https_proxy": https_proxy,
        "NO_PROXY": "35.220.164.252,localhost,127.0.0.1,10.0.0.0/8",
        "no_proxy": "35.220.164.252,localhost,127.0.0.1,10.0.0.0/8",
    }
    arguments: list[str] = []
    for key, value in values.items():
        arguments.extend(["-e", f"{key}={value}"])
    return arguments, [api_key, http_proxy, https_proxy]


def redact(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "<redacted>")
    return value


def submission_command(
    *, case_id: str, workspace: str, job_id: str, environment_args: list[str]
) -> list[str]:
    return [
        "run", "--job-name", job_id,
        "--num-nodes", "1",
        "--gpus-per-task", "0",
        "--cpus-per-task", "4",
        "--memory-per-task", "16",
        "--shm-size-gib", "8",
        "--no-env",
        *environment_args,
        "--image", IMAGE,
        "python3.12", str(RUNNER),
        "--case", case_id,
        "--workspace", workspace,
        "--output-root", str(OUTPUT_ROOT),
        "--model-label", MODEL_LABEL,
        "--framework-label", FRAMEWORK_LABEL,
        "--runtime-image-digest", IMAGE_DIGEST,
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-active", type=int, default=4)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-infra-retries", type=int, default=2)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.max_active < 1:
        parser.error("--max-active must be positive")

    inventory = discover()
    requested = set(args.cases or [])
    known = {row["case_id"] for row in inventory}
    unknown = sorted(requested - known)
    if unknown:
        parser.error(f"unknown case(s): {', '.join(unknown)}")
    selected = [row for row in inventory if not requested or row["case_id"] in requested]
    environment_args, secrets = required_environment()
    state_path = OUTPUT_ROOT / "controller.json"
    state = read_json(
        state_path,
        {
            "schema": "multimodalcode-vision2web-official-full-eval-1",
            "created_at_utc": utc_now(),
            "source_root": str(SOURCE_ROOT),
            "output_root": str(OUTPUT_ROOT),
            "dataset_size": 193,
            "judge": {
                "functional_model": FUNCTIONAL_MODEL,
                "visual_model": VISUAL_MODEL,
                "functional_base_url": FUNCTIONAL_BASE_URL,
                "visual_base_url": VISUAL_BASE_URL,
                "frozen_evaluator_commit": "577f9397b3db8fc6d828adde254a830caa65d515",
                "leaderboard_equivalent": False,
                "note": (
                    "The frozen default gemini-3-pro-preview was unavailable; "
                    "gemini-3.1-pro-preview is explicitly recorded."
                ),
            },
            "jobs": {},
        },
    )
    # Refresh non-secret run metadata on every resume so controller state
    # reflects the exact endpoints/models used by newly submitted workers.
    state["judge"] = {
        "functional_model": FUNCTIONAL_MODEL,
        "visual_model": VISUAL_MODEL,
        "functional_base_url": FUNCTIONAL_BASE_URL,
        "visual_base_url": VISUAL_BASE_URL,
        "frozen_evaluator_commit": "577f9397b3db8fc6d828adde254a830caa65d515",
        "leaderboard_equivalent": False,
        "note": (
            "The frozen default gemini-3-pro-preview was unavailable; "
            "gemini-3.1-pro-preview is explicitly recorded."
        ),
    }

    while True:
        active = 0
        pending: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        all_terminal = True
        for index, row in enumerate(inventory, start=1):
            case_id = row["case_id"]
            record = state["jobs"].setdefault(
                case_id,
                {
                    **row,
                    "job_id": job_name(index, case_id),
                    "status": "NEW" if row["scoreable"] else "GENERATION_FAILURE_ZERO",
                    "retries": 0,
                },
            )
            if not row["scoreable"]:
                record["status"] = "GENERATION_FAILURE_ZERO"
                continue
            artifact = evaluation_summary(case_id)
            if artifact.is_file():
                result = read_json(artifact, {})
                record.update(
                    {
                        "status": "EVALUATED",
                        "artifact": str(artifact),
                        "evaluation_status": result.get("status"),
                        "official_cli_returncode": result.get("official_cli_returncode"),
                    }
                )
                continue
            if case_id not in {item["case_id"] for item in selected}:
                record["status"] = "NOT_SELECTED"
                continue
            if record["status"] not in {"NEW", "NOT_FOUND"}:
                status, detail = query(record["job_id"])
                record["status"] = status
                record["last_checked_at_utc"] = utc_now()
                if status == "UNKNOWN":
                    record["last_status_output"] = redact(detail[-2000:], secrets)
            status = record["status"]
            if status in ACTIVE:
                active += 1
                all_terminal = False
            elif status in TERMINAL:
                retries = int(record.get("retries", 0))
                if retries < args.max_infra_retries:
                    retries += 1
                    record["retries"] = retries
                    record["job_id"] = job_name(index, case_id, retries)
                    record["status"] = "NOT_FOUND"
                    pending.append((index, row, record))
                    all_terminal = False
                else:
                    record["status"] = "INFRA_FAILED"
            elif status != "INFRA_FAILED":
                pending.append((index, row, record))
                all_terminal = False

        slots = max(0, args.max_active - active)
        for _, row, record in pending[:slots]:
            existing, detail = query(record["job_id"])
            if existing != "NOT_FOUND":
                record["status"] = existing
                if existing == "UNKNOWN":
                    record["last_status_output"] = redact(detail[-2000:], secrets)
                continue
            command = submission_command(
                case_id=row["case_id"],
                workspace=row["workspace"],
                job_id=record["job_id"],
                environment_args=environment_args,
            )
            print(f"[submit] {row['case_id']} -> {record['job_id']}", flush=True)
            submitted = clusterx(command)
            record["submit_returncode"] = submitted.returncode
            record["submitted_at_utc"] = utc_now()
            if submitted.returncode != 0:
                record["status"] = "SUBMIT_FAILED"
                record["submit_error"] = redact(submitted.stdout[-2000:], secrets)
                write_json(state_path, state)
                raise RuntimeError(record["submit_error"])
            record["status"] = "QUEUING"

        counts: dict[str, int] = {}
        for record in state["jobs"].values():
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        state["counts"] = counts
        state["selected_cases"] = [row["case_id"] for row in selected]
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        print(json.dumps(counts, sort_keys=True), flush=True)
        if args.once or all_terminal:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

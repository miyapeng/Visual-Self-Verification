#!/usr/bin/env python3
"""Resume-safe CPU controller for the Vision2Web paper evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
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
    / "runs/vision2web_official_eval/qwen35-9b-openhands-official-v2-paper-glm46v-functional-v1"
)
DATASETS = PROJECT_ROOT / "data/vision2web/extracted"
RUNNER = PROJECT_ROOT / "evaluate/vision2web/run_paper_clusterx.py"
ANALYZER = PROJECT_ROOT / "scripts/vision2web/analyze_paper_official_eval.py"
IMAGE = "registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939"
IMAGE_DIGEST = "sha256:3d2fa9b0999a1fbb10c9d4ccc653c7d5560a713668d6c18c80d0d06e36e43056"
MODEL_LABEL = "Qwen3.5-9B"
FRAMEWORK_LABEL = "openhands"
GUI_AGENT_MODEL = "glm-4.6v"
BASE_URL = "http://35.220.164.252:3888/v1"
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
        deployable = (
            (summary_path.parent / "result.json").is_file()
            and (workspace / "start.sh").is_file()
            and (workspace / "prototypes").is_dir()
        )
        functional_scope = case_id.startswith(("frontend/", "website/"))
        scoreable = deployable and functional_scope
        if not functional_scope:
            skip_status = "VISUAL_ONLY_NOT_EVALUATED"
            unscoreable_reason = "Level 1 has no functional score"
        elif not deployable:
            skip_status = "GENERATION_FAILURE_ZERO"
            unscoreable_reason = "generation output is not deployable"
        else:
            skip_status = None
            unscoreable_reason = None
        rows.append(
            {
                "case_id": case_id,
                "generation_status": summary.get("result_status", "missing"),
                "workspace": str(workspace),
                "scoreable": scoreable,
                "skip_status": skip_status,
                "unscoreable_reason": unscoreable_reason,
            }
        )
    if len(rows) != 193:
        raise RuntimeError(f"expected 193 generation cases, found {len(rows)}")
    return sorted(rows, key=lambda row: row["case_id"])


def ensure_analysis_inventory(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        task, project = row["case_id"].split("/", 1)
        (
            OUTPUT_ROOT
            / "paper_results"
            / task
            / FRAMEWORK_LABEL
            / MODEL_LABEL
            / project
        ).mkdir(parents=True, exist_ok=True)


def evaluation_summary(case_id: str) -> Path:
    task, project = case_id.split("/", 1)
    return (
        OUTPUT_ROOT
        / "cases"
        / MODEL_LABEL
        / task
        / safe(project)
        / "clusterx_paper_evaluation.json"
    )


def published_result(case_id: str) -> Path:
    task, project = case_id.split("/", 1)
    return (
        OUTPUT_ROOT
        / "paper_results"
        / task
        / FRAMEWORK_LABEL
        / MODEL_LABEL
        / project
        / "evaluation_result.json"
    )


def job_name(index: int, case_id: str, retry: int = 0) -> str:
    digest = hashlib.sha1(case_id.encode()).hexdigest()[:7]
    suffix = f"-r{retry}" if retry else ""
    return f"mmc-v2w-pg1-{index:03d}-{digest}{suffix}"


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
        raise RuntimeError("HTTP_PROXY and HTTPS_PROXY are required for task dependency access")
    values = {
        "VISION2WEB_PAPER_API_KEY": api_key,
        "VISION2WEB_PAPER_BASE_URL": BASE_URL,
        "VISION2WEB_PAPER_GUI_MODEL": GUI_AGENT_MODEL,
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
        "--cpus-per-task", "8",
        "--memory-per-task", "32",
        "--shm-size-gib", "16",
        "--no-env",
        *environment_args,
        "--image", IMAGE,
        "python3.12", str(RUNNER),
        "--case", case_id,
        "--workspace", workspace,
        "--execution-workspace", "/workspace",
        "--output-root", str(OUTPUT_ROOT),
        "--model-label", MODEL_LABEL,
        "--framework-label", FRAMEWORK_LABEL,
        "--attempt-label", job_id,
        "--runtime-image-digest", IMAGE_DIGEST,
        "--functional-only",
    ]


def run_analysis() -> tuple[int, str]:
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            "--output-root", str(OUTPUT_ROOT),
            "--datasets-dir", str(DATASETS),
            "--framework", FRAMEWORK_LABEL,
            "--model", MODEL_LABEL,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.returncode, completed.stdout


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
    ensure_analysis_inventory(inventory)
    requested = set(args.cases or [])
    known = {row["case_id"] for row in inventory}
    unknown = sorted(requested - known)
    if unknown:
        parser.error(f"unknown cases: {', '.join(unknown)}")
    selected = [row for row in inventory if not requested or row["case_id"] in requested]
    selected_ids = {row["case_id"] for row in selected}
    environment_args, secrets = required_environment()
    state_path = OUTPUT_ROOT / "controller.json"
    state = read_json(
        state_path,
        {
            "schema": "multimodalcode-vision2web-paper-full-eval-1",
            "created_at_utc": utc_now(),
            "source_root": str(SOURCE_ROOT),
            "output_root": str(OUTPUT_ROOT),
            "dataset_size": 193,
            "jobs": {},
        },
    )
    state["evaluator"] = {
        "source_commit": "3111cc312a2ff86d764d93477cb7c17205153e50",
        "functional_protocol": "WebVoyager-style GUIAgentTester",
        "functional_model": GUI_AGENT_MODEL,
        "functional_paper_exact": True,
        "visual_model": None,
        "paper_visual_model": "gemini-3-pro-preview",
        "visual_paper_exact": False,
        "base_url": BASE_URL,
        "clusterx_image": IMAGE,
        "clusterx_image_digest": IMAGE_DIGEST,
        "note": "Functional-only run by user decision. No visual-judge API calls are made and no visual or overall score is reported.",
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
                record["status"] = row["skip_status"]
                continue
            if published_result(case_id).is_file():
                summary = read_json(evaluation_summary(case_id), {})
                record.update(
                    {
                        "status": "EVALUATED",
                        "artifact": str(evaluation_summary(case_id)),
                        "evaluation_status": summary.get("status"),
                    }
                )
                continue
            if case_id not in selected_ids:
                record["status"] = "NOT_SELECTED"
                continue
            if record["status"] == "NOT_SELECTED":
                # This state means no job was submitted during a prior subset run.
                record["status"] = "NOT_FOUND"
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
                    record["status"] = "EVALUATION_FAILED_ZERO"
            elif status != "EVALUATION_FAILED_ZERO":
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
        state["selected_cases"] = sorted(selected_ids)
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        print(json.dumps(counts, sort_keys=True), flush=True)
        if args.once:
            return 0
        if all_terminal:
            returncode, output = run_analysis()
            state["analysis_returncode"] = returncode
            state["analysis_output_tail"] = output[-5000:]
            state["completed_at_utc"] = utc_now()
            write_json(state_path, state)
            print(output, flush=True)
            return returncode
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

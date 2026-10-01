#!/usr/bin/env python3
"""Submit the six one-case scaffold/model comparison sessions safely.

This utility is intentionally a thin, resume-safe launcher.  It never changes
an agent prompt or introduces a development loop.  API credentials are passed
only as ClusterX environment variables and are redacted from launcher output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939"
CASE_ID = "frontend/smartrecruiters"
WORKER = PROJECT_ROOT / "scripts/vision2web/run_scaffold_comparison_case.sh"

CONDITION_DEFAULTS = {
    "guided_vsv": {
        "run_label": "smartrecruiters-guided-vsv-sixway-v1",
        "server_run": "qwen38-27b-v2w-comparison",
        "job_prefix": "mmc-v2wgv",
    },
    "browser_enabled": {
        "run_label": "smartrecruiters-browser-enabled-sixway-v1",
        "server_run": "qwen38-27b-v2w-official-prompt",
        "job_prefix": "mmc-v2wbr",
    },
    "official": {
        "run_label": "smartrecruiters-official-sixway-v1",
        "server_run": "qwen38-27b-v2w-official",
        "job_prefix": "mmc-v2woff",
    },
}


@dataclass(frozen=True)
class Run:
    job_suffix: str
    framework: str
    model: str
    backend: str


RUNS = (
    Run("q38-oh", "openhands", "Qwen3.8-27B", "local"),
    Run("q38-cc", "claude_code", "Qwen3.8-27B", "local"),
    Run("opus-oh", "openhands", "claude-opus-4-8", "relay"),
    Run("opus-cc", "claude_code", "claude-opus-4-8", "relay"),
    Run("glm-oh", "openhands", "glm-5.2", "relay"),
    Run("glm-cc", "claude_code", "glm-5.2", "relay"),
)


def clean_control_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(name, None)
    env["NO_PROXY"] = "compute.pjlab.org.cn,10.140.100.1"
    env["no_proxy"] = env["NO_PROXY"]
    return env


def invoke(arguments: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["clusterx", *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
        env=clean_control_environment(),
    )


def redact(value: str) -> str:
    secrets = [
        os.environ.get("LLM_RELAY_API_KEY", ""),
        os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", ""),
        os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", ""),
    ]
    for secret in secrets:
        if secret:
            value = value.replace(secret, "<redacted>")
    return value


def status(job_id: str) -> tuple[str, str]:
    completed = invoke(["get-job", job_id, "--no-verbose"], timeout=30)
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", completed.stdout)
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1), output
    if completed.returncode != 0 and any(
        marker in output.casefold() for marker in ("404", "not found", "不存在")
    ):
        return "NOT_FOUND", output
    return "UNKNOWN", output


def artifact(run: Run, *, run_label: str, mode: str) -> Path:
    model_dir = run.model if run.framework == "claude_code" else f"litellm_proxy__{run.model}"
    root = (
        PROJECT_ROOT
        / "runs/vision2web_scaffold_comparison"
        / run_label
        / "agents"
        / model_dir
        / "vision2web"
    )
    if run.framework == "claude_code":
        root = root / "claude_code"
    return root / f"{mode}/frontend__smartrecruiters/comparison-summary.json"


def result_artifact(run: Run, *, run_label: str, mode: str) -> Path:
    return artifact(run, run_label=run_label, mode=mode).with_name("result.json")


def worker_environment(run: Run, *, wall_time: int) -> list[str]:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not http_proxy or not https_proxy:
        raise RuntimeError("HTTP_PROXY/HTTPS_PROXY are missing; run proxy_on first")
    values = {
        "HTTP_PROXY": http_proxy,
        "HTTPS_PROXY": https_proxy,
        "http_proxy": http_proxy,
        "https_proxy": https_proxy,
        "NO_PROXY": "localhost,127.0.0.1,35.220.164.252",
        "no_proxy": "localhost,127.0.0.1,35.220.164.252",
        "MMCODE_AGENT_WALL_TIME": str(wall_time),
    }
    if run.backend == "relay":
        key = os.environ.get("LLM_RELAY_API_KEY")
        if not key:
            raise RuntimeError("LLM_RELAY_API_KEY is missing")
        values["LLM_RELAY_API_KEY"] = key
    arguments: list[str] = []
    for name, value in values.items():
        arguments.extend(["-e", f"{name}={value}"])
    return arguments


def submit(
    run: Run,
    *,
    job_id: str,
    run_label: str,
    server_run: str,
    mode: str,
    wall_time: int,
) -> None:
    selected_server = server_run if run.backend == "local" else ""
    arguments = [
        "run",
        "--job-name",
        job_id,
        "--num-nodes",
        "1",
        "--gpus-per-task",
        "0",
        "--cpus-per-task",
        "16",
        "--memory-per-task",
        "64",
        "--shm-size-gib",
        "16",
        "--no-env",
        *worker_environment(run, wall_time=wall_time),
        "--image",
        IMAGE,
        "bash",
        str(WORKER),
        CASE_ID,
        run.framework,
        run.model,
        run.backend,
        run_label,
        mode,
        selected_server,
    ]
    completed = invoke(arguments)
    if completed.returncode != 0:
        raise RuntimeError(redact(completed.stdout))
    print(f"[submitted] {job_id} {run.framework} {run.model} mode={mode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("all", "local", "relay"), default="all")
    parser.add_argument(
        "--framework",
        choices=("all", "openhands", "claude_code"),
        default="all",
    )
    parser.add_argument(
        "--mode", choices=tuple(CONDITION_DEFAULTS), default="guided_vsv"
    )
    parser.add_argument("--run-label")
    parser.add_argument("--server-run")
    parser.add_argument("--job-prefix")
    parser.add_argument("--wall-time", type=int, default=7200)
    parser.add_argument("--query-only", action="store_true")
    args = parser.parse_args()
    if args.wall_time <= 0:
        parser.error("--wall-time must be positive")
    defaults = CONDITION_DEFAULTS[args.mode]
    run_label = args.run_label or defaults["run_label"]
    server_run = args.server_run or defaults["server_run"]
    job_prefix = args.job_prefix or defaults["job_prefix"]
    selected = [
        run
        for run in RUNS
        if (args.group == "all" or run.backend == args.group)
        and (args.framework == "all" or run.framework == args.framework)
    ]
    if args.mode == "browser_enabled":
        selected = [run for run in selected if run.framework == "openhands"]
    records = []
    for run in selected:
        job_id = f"{job_prefix}-{run.job_suffix}"
        summary_path = artifact(run, run_label=run_label, mode=args.mode)
        result_path = result_artifact(run, run_label=run_label, mode=args.mode)
        if summary_path.is_file():
            current = "ARTIFACT_READY"
        elif result_path.is_file():
            # A historical worker could finish the native agent run and write
            # result.json before failing in optional shell post-processing.
            # Treat that as terminal evidence rather than resubmitting an
            # expensive model trajectory merely to recreate a summary file.
            current = "RESULT_READY"
        else:
            current, _ = status(job_id)
        if not args.query_only and current == "NOT_FOUND":
            submit(
                run,
                job_id=job_id,
                run_label=run_label,
                server_run=server_run,
                mode=args.mode,
                wall_time=args.wall_time,
            )
            current = "QUEUING"
        records.append(
            {
                "job_id": job_id,
                "framework": run.framework,
                "model": run.model,
                "backend": run.backend,
                "mode": args.mode,
                "run_label": run_label,
                "wall_time": args.wall_time,
                "status": current,
                "artifact": str(summary_path),
                "result_artifact": str(result_path),
            }
        )
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

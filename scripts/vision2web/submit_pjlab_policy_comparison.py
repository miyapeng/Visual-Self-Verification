#!/usr/bin/env python3
"""Submit the PJLab-model × 5-condition Vision2Web policy comparison."""

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
RUN_LABEL = "smartrecruiters-pjlab-policy-v1"
WORKER = PROJECT_ROOT / "scripts/vision2web/run_scaffold_comparison_case.sh"


@dataclass(frozen=True)
class Run:
    job_id: str
    model: str
    framework: str
    mode: str


RUNS = tuple(
    Run(f"mmc-pj-{short}-oh-{suffix}", model, "openhands", mode)
    for short, model in (
        ("g53", "glm-5.3-flash"),
        ("kk3", "kimi-k3"),
        ("kk26", "kimi-k2.6"),
    )
    for suffix, mode in (("off", "official"), ("brw", "browser_enabled"), ("vsv", "guided_vsv"))
) + tuple(
    Run(f"mmc-pj-{short}-cc-{suffix}", model, "claude_code", mode)
    for short, model in (
        ("g53", "glm-5.3-flash"),
        ("kk3", "kimi-k3"),
        ("kk26", "kimi-k2.6"),
    )
    for suffix, mode in (("off", "official"), ("vsv", "guided_vsv"))
)


def clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    env["NO_PROXY"] = "compute.pjlab.org.cn,token.pjlab.org.cn"
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
        env=clean_environment(),
    )


def status(job_id: str) -> str:
    completed = invoke(["get-job", job_id, "--no-verbose"], timeout=30)
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", completed.stdout)
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1)
    if completed.returncode != 0 and any(value in output.casefold() for value in ("404", "not found", "不存在")):
        return "NOT_FOUND"
    return "UNKNOWN"


def artifact(run: Run) -> Path:
    model_dir = run.model if run.framework == "claude_code" else f"litellm_proxy__{run.model}"
    root = PROJECT_ROOT / "runs/vision2web_scaffold_comparison" / RUN_LABEL / "agents" / model_dir / "vision2web"
    if run.framework == "claude_code":
        root = root / "claude_code"
    return root / run.mode / "frontend__smartrecruiters" / "result.json"


def submit(run: Run, *, key: str, wall_time: int) -> None:
    environment = {
        "PJLAB_TOKEN_API_KEY": key,
        "NO_PROXY": "localhost,127.0.0.1,token.pjlab.org.cn",
        "no_proxy": "localhost,127.0.0.1,token.pjlab.org.cn",
        "MMCODE_AGENT_WALL_TIME": str(wall_time),
    }
    env_args: list[str] = []
    for name, value in environment.items():
        env_args.extend(["-e", f"{name}={value}"])
    arguments = [
        "run",
        "--job-name", run.job_id,
        "--num-nodes", "1",
        "--gpus-per-task", "0",
        "--cpus-per-task", "16",
        "--memory-per-task", "64",
        "--shm-size-gib", "16",
        "--no-env",
        *env_args,
        "--image", IMAGE,
        "bash", str(WORKER), CASE_ID, run.framework, run.model,
        "pjlab", RUN_LABEL, run.mode,
    ]
    completed = invoke(arguments)
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.replace(key, "<redacted>"))
    print(f"[submitted] {run.job_id} {run.model} {run.framework} {run.mode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-only", action="store_true")
    parser.add_argument(
        "--model",
        choices=("all", "glm-5.3-flash", "kimi-k3", "kimi-k2.6"),
        default="all",
    )
    parser.add_argument("--framework", choices=("all", "openhands", "claude_code"), default="all")
    parser.add_argument("--wall-time", type=int, default=7200)
    args = parser.parse_args()
    if args.wall_time <= 0:
        parser.error("--wall-time must be positive")
    selected = [
        run for run in RUNS
        if (args.model == "all" or run.model == args.model)
        and (args.framework == "all" or run.framework == args.framework)
    ]
    key = os.environ.get("PJLAB_TOKEN_API_KEY", "")
    if not args.query_only and not key:
        raise RuntimeError("PJLAB_TOKEN_API_KEY is missing")
    records = []
    for run in selected:
        result_path = artifact(run)
        current = "ARTIFACT_READY" if result_path.is_file() else status(run.job_id)
        if not args.query_only and current == "NOT_FOUND":
            submit(run, key=key, wall_time=args.wall_time)
            current = "QUEUING"
        records.append({
            "job_id": run.job_id,
            "model": run.model,
            "framework": run.framework,
            "mode": run.mode,
            "status": current,
            "result": str(result_path),
        })
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume-safe ClusterX controller for canonical Vision2Web VSV conditions."""

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
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATHS = {
    "openhands": PROJECT_ROOT / "configs/vision2web/self_verify_openhands.json",
    "claude_code": PROJECT_ROOT / "configs/vision2web/self_verify_claude_code.json",
}
PILOT20_CONFIG_PATH = (
    PROJECT_ROOT / "configs/vision2web/visual_self_verification_pilot_20.json"
)
INSTANCE_IDS_PATH = PROJECT_ROOT / "data/vision2web/instance_ids.txt"
CLAUDE_RESUME_PROBE_PATH = (
    PROJECT_ROOT
    / "runs/vision2web_self_verify/probes/claude_resume_capability.json"
)
DEFAULT_RUN_LABEL = "qwen35-9b-openhands-guided-vsv-v3"
DEFAULT_SERVER_RUN = "qwen35-9b-v2w-guided-vsv"
WORKER_GPUS = 0
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


def clusterx(arguments: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "compute.pjlab.org.cn,10.140.100.1"
    env["no_proxy"] = env["NO_PROXY"]
    try:
        return subprocess.run(
            ["clusterx", *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return subprocess.CompletedProcess(arguments, 124, output + "\nclusterx timeout\n")


def worker_proxy_arguments() -> list[str]:
    """Inject the platform outbound proxy into the case container explicitly.

    ``--no-env`` is retained so unrelated controller state cannot leak into a
    controlled run.  Vision2Web Level 2/3 implementations commonly need npm,
    so silently launching without the configured platform proxy would change
    the task environment and prevent deployment.
    """

    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not http_proxy or not https_proxy:
        raise RuntimeError(
            "Vision2Web workers require HTTP_PROXY and HTTPS_PROXY; run proxy_on "
            "before starting the supervisor"
        )
    values = {
        "HTTP_PROXY": http_proxy,
        "HTTPS_PROXY": https_proxy,
        "http_proxy": http_proxy,
        "https_proxy": https_proxy,
    }
    arguments: list[str] = []
    for key, value in values.items():
        arguments.extend(["-e", f"{key}={value}"])
    return arguments


def query(job_id: str) -> tuple[str, str]:
    result = clusterx(["get-job", job_id, "--no-verbose"], timeout=30)
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout).replace("\r", "")
    match = re.search(r"JobStatus\.([A-Z]+)", output)
    if match:
        return match.group(1), output
    if result.returncode != 0 and any(token in output.lower() for token in ("404", "not found", "不存在")):
        return "NOT_FOUND", output
    return "UNKNOWN", output


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")


def job_name(
    mode: str,
    case_id: str,
    run_label: str,
    retry: int = 0,
    framework: str = "openhands",
) -> str:
    digest = hashlib.sha1(case_id.encode()).hexdigest()[:7]
    run_digest = hashlib.sha1(run_label.encode()).hexdigest()[:4]
    short_mode = {
        "official": "off",
        "browser_enabled": "br",
        "guided_vsv": "gv",
    }[mode]
    suffix = f"-r{retry}" if retry else ""
    scaffold = "cc" if framework == "claude_code" else "oh"
    return f"mmc-v2w-{scaffold}-{short_mode}-{run_digest}-{digest}{suffix}"


def selected_cases(config: dict[str, Any], phase: str) -> list[str]:
    if phase == "full":
        selected = [
            line.strip()
            for line in INSTANCE_IDS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(selected) != 193 or len(set(selected)) != 193:
            raise RuntimeError(
                f"Invalid frozen full set in {INSTANCE_IDS_PATH}: "
                f"found {len(selected)} unique={len(set(selected))}"
            )
        return selected
    if phase == "pilot20":
        pilot = read_json(PILOT20_CONFIG_PATH, {})
        cases = pilot.get("cases", {})
        selected = [
            case_id
            for level in ("Level 1", "Level 2", "Level 3")
            for case_id in cases.get(level, [])
        ]
        expected = int(pilot.get("selection_policy", {}).get("total", 0))
        if len(selected) != expected or len(set(selected)) != expected:
            raise RuntimeError(
                f"Invalid frozen pilot selection in {PILOT20_CONFIG_PATH}: "
                f"expected {expected}, found {len(selected)} unique={len(set(selected))}"
            )
        return selected
    selection = config["selection"]
    if phase == "smoke":
        return list(selection["smoke"])
    fixed = selection["fixed_experiment"]
    return [case_id for level in ("Level 1", "Level 2", "Level 3") for case_id in fixed[level]]


def server_ready(server_run: str, expected_model: str) -> tuple[bool, str]:
    path = PROJECT_ROOT / "runs/agent_smoke/servers" / server_run / "ready.json"
    record = read_json(path, {})
    if record.get("model") != expected_model or not record.get("endpoint"):
        return False, f"missing or mismatched {path}"
    request = urllib.request.Request(record["endpoint"].rstrip("/") + "/models")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            return response.status == 200, record["endpoint"]
    except Exception as exc:
        return False, f"{record['endpoint']}: {exc}"


def claude_resume_ready() -> tuple[bool, str]:
    record = read_json(CLAUDE_RESUME_PROBE_PATH, {})
    ready = bool(
        record.get("claude_resume_supported")
        and record.get("claude", {}).get("available")
    )
    return ready, (
        str(CLAUDE_RESUME_PROBE_PATH)
        if ready
        else f"waiting for a successful pinned-image probe at {CLAUDE_RESUME_PROBE_PATH}"
    )


def artifact_path(
    run_label: str, model: str, mode: str, case_id: str, framework: str = "openhands"
) -> Path:
    root = (
        PROJECT_ROOT
        / "runs/vision2web_generation"
        / run_label
        / "agents"
        / (model if framework == "claude_code" else f"litellm_proxy__{model}")
        / "vision2web"
    )
    if framework == "claude_code":
        root = root / "claude_code"
    return root / mode / safe(case_id) / "generation-summary.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("smoke", "fixed", "pilot20", "full"), default="smoke"
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("official", "browser_enabled", "guided_vsv"),
        default=None,
    )
    parser.add_argument("--max-active", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-infra-retries", type=int, default=4)
    parser.add_argument(
        "--wall-time", type=int, default=7200,
        help="Per-task seconds; 0 disables the Claude Code task deadline (diagnostic setting).",
    )
    parser.add_argument("--run-label", default=DEFAULT_RUN_LABEL)
    parser.add_argument("--server-run", default=DEFAULT_SERVER_RUN)
    parser.add_argument(
        "--model",
        help="Override the model recorded in the scaffold config.",
    )
    parser.add_argument(
        "--case-ids",
        nargs="+",
        help="Optional ordered subset of the selected phase (used for audited reruns).",
    )
    parser.add_argument(
        "--framework",
        choices=("openhands", "claude_code"),
        default="openhands",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.modes is None:
        args.modes = (
            ["official", "guided_vsv"]
            if args.framework == "claude_code"
            else ["official", "browser_enabled", "guided_vsv"]
        )
    if args.framework == "claude_code" and "browser_enabled" in args.modes:
        parser.error(
            "Claude Code official already exposes playwright-cli; "
            "browser_enabled would be a duplicate condition"
        )
    if args.max_active < 1:
        parser.error("--max-active must be positive")
    if args.wall_time < 0 or (args.wall_time == 0 and args.framework != "claude_code"):
        parser.error("--wall-time must be positive, or 0 for Claude Code")
    if args.framework == "claude_code" and args.run_label == DEFAULT_RUN_LABEL:
        args.run_label = "qwen35-9b-claude-code-guided-vsv-v2"
    if args.phase == "pilot20" and (
        args.framework != "claude_code" or args.modes != ["official"]
    ):
        parser.error(
            "pilot20 is the Claude Code Natural + same-context protocol; use "
            "--framework claude_code --modes official"
        )
    if args.phase == "full" and (
        args.framework != "claude_code" or args.modes != ["official"]
    ):
        parser.error(
            "full is the canonical Claude Code official run; use "
            "--framework claude_code --modes official"
        )

    config_path = CONFIG_PATHS[args.framework]
    config = read_json(config_path, {})
    interface_revision = config["browser_interface_revision"]
    model = args.model or config["model"]["name"]
    image = config["runtime_image"]["reference"]
    cases = selected_cases(config, args.phase)
    if args.case_ids:
        unknown = [case_id for case_id in args.case_ids if case_id not in cases]
        if unknown:
            parser.error(f"case IDs are outside the frozen {args.phase} selection: {unknown}")
        if len(set(args.case_ids)) != len(args.case_ids):
            parser.error("--case-ids contains duplicates")
        cases = list(args.case_ids)
    pairs = [(mode, case_id) for case_id in cases for mode in args.modes]
    state_path = (
        PROJECT_ROOT
        / "runs/vision2web_generation"
        / args.run_label
        / (
            f"controller-{args.phase}.json"
            if args.framework == "openhands"
            else f"controller-claude-code-{args.phase}.json"
        )
    )
    state = read_json(
        state_path,
        {
            "schema": "multimodalcode-vision2web-self-verify-controller-2",
            "created_at_utc": utc_now(),
            "config": str(config_path),
            "browser_interface_revision": interface_revision,
            "framework": args.framework,
            "model": model,
            "phase": args.phase,
            "protocol": "pilot20" if args.phase == "pilot20" else "standard",
            "jobs": {},
        },
    )
    state.setdefault(
        "protocol", "pilot20" if args.phase == "pilot20" else "standard"
    )
    if state.get("browser_interface_revision") != interface_revision:
        raise RuntimeError(
            "Controller state/browser interface mismatch: "
            f"{state.get('browser_interface_revision')!r} != {interface_revision!r}"
        )
    if state.get("framework", args.framework) != args.framework:
        raise RuntimeError(
            f"Controller state belongs to {state.get('framework')}, not {args.framework}"
        )
    if state.get("model", model) != model:
        raise RuntimeError(
            f"Controller state belongs to {state.get('model')}, not {model}"
        )
    state.setdefault("model", model)
    if state.get("wall_time_seconds", 7200) != args.wall_time and state.get("jobs"):
        parser.error("Task time budget changed; use a new --run-label")
    state["wall_time_seconds"] = args.wall_time
    case_script = PROJECT_ROOT / "scripts/vision2web/run_generation_case.sh"
    proxy_arguments = worker_proxy_arguments()

    while True:
        ready, server_detail = server_ready(args.server_run, model)
        if not ready:
            print(f"[wait-server] {server_detail}", flush=True)
        if args.phase == "pilot20":
            resume_ready, resume_detail = claude_resume_ready()
            if not resume_ready:
                print(f"[wait-resume-probe] {resume_detail}", flush=True)
            ready = ready and resume_ready
        active = 0
        pending: list[tuple[str, str, dict[str, Any]]] = []
        all_terminal = True
        for mode, case_id in pairs:
            key = f"{mode}:{case_id}"
            record = state["jobs"].setdefault(
                key,
                {
                    "mode": mode,
                    "case_id": case_id,
                    "job_id": job_name(
                        mode, case_id, args.run_label, framework=args.framework
                    ),
                    "status": "NEW",
                    "retries": 0,
                },
            )
            artifact = artifact_path(
                args.run_label, model, mode, case_id, args.framework
            )
            if artifact.is_file():
                summary = read_json(artifact, {})
                record.update(
                    {
                        "status": "COMPLETED",
                        "result_status": summary.get("result_status", "unknown"),
                        "artifact": str(artifact),
                    }
                )
                continue
            if record["status"] not in {"NEW", "NOT_FOUND"}:
                status, detail = query(record["job_id"])
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
                if retries < args.max_infra_retries:
                    retries += 1
                    record["retries"] = retries
                    record["job_id"] = job_name(
                        mode,
                        case_id,
                        args.run_label,
                        retries,
                        framework=args.framework,
                    )
                    record["status"] = "NOT_FOUND"
                    pending.append((mode, case_id, record))
                    all_terminal = False
                else:
                    record["status"] = "INFRA_FAILED"
            elif status != "INFRA_FAILED":
                pending.append((mode, case_id, record))
                all_terminal = False

        slots = max(0, args.max_active - active) if ready else 0
        for mode, case_id, record in pending[:slots]:
            existing, detail = query(record["job_id"])
            if existing != "NOT_FOUND":
                record["status"] = existing
                if existing == "UNKNOWN":
                    record["last_status_output"] = detail[-2000:]
                continue
            command = [
                "run",
                "--job-name",
                record["job_id"],
                "--num-nodes",
                "1",
                "--gpus-per-task",
                # Coding/browser workers call the shared vLLM endpoint and do
                # not perform local inference. Never reserve a scheduler GPU.
                str(WORKER_GPUS),
                "--cpus-per-task",
                "8",
                "--memory-per-task",
                "32",
                "--shm-size-gib",
                "8",
                "--no-env",
                *proxy_arguments,
                "--image",
                image,
                "bash",
                str(case_script),
                case_id,
                model,
                args.server_run,
                args.run_label,
                mode,
                args.framework,
                "pilot20" if args.phase == "pilot20" else "standard",
                str(args.wall_time),
            ]
            print(f"[submit] {mode} {case_id} -> {record['job_id']}", flush=True)
            submitted = clusterx(command)
            record["submit_returncode"] = submitted.returncode
            record["submit_output"] = submitted.stdout[-4000:]
            record["submitted_at_utc"] = utc_now()
            if submitted.returncode != 0:
                # ClusterX creation is not transactionally visible: a request
                # can create the job but return HTTP 409 on an internal retry.
                # Recover by the deterministic job ID instead of submitting a
                # duplicate or aborting the full controller.
                existing_after, detail = query(record["job_id"])
                conflict = "409" in submitted.stdout and "Conflict" in submitted.stdout
                if existing_after not in {"NOT_FOUND", "UNKNOWN"} or conflict:
                    record["status"] = (
                        existing_after
                        if existing_after not in {"NOT_FOUND", "UNKNOWN"}
                        else "QUEUING"
                    )
                    record["submit_recovered"] = True
                    record["submit_recovery_output"] = detail[-2000:]
                else:
                    record["status"] = "SUBMIT_FAILED"
                    write_json(state_path, state)
                    raise RuntimeError(submitted.stdout)
            else:
                record["status"] = "QUEUING"

        counts: dict[str, int] = {}
        for record in state["jobs"].values():
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        state["counts"] = counts
        state["updated_at_utc"] = utc_now()
        state["server"] = server_detail
        write_json(state_path, state)
        print(f"[state] {json.dumps(counts, sort_keys=True)}", flush=True)
        if args.once or all_terminal:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

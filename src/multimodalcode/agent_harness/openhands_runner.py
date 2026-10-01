"""Direct-container adapter for the repository-vendored OpenHands runtime."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cases import AgentCase
from .vision2web_experiment import (
    ACCEPTED_VISION2WEB_MODES,
    BROWSER_INTERFACE_REVISION,
    normalize_vision2web_mode,
    prompt_for_mode,
    prompt_record,
)
from .vision2web_trace import (
    NativeOpenHandsBrowserRecorder,
    Vision2WebRunRecorder,
    build_development_timeline,
)


OPENHANDS_PROFILES = ("official", "research")
_RESEARCH_ONLY_ENV = (
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "MULTIMODALCODE_FORCE_VISION",
    "MULTIMODALCODE_MAX_INPUT_TOKENS",
    "MULTIMODALCODE_MAX_OUTPUT_TOKENS",
    "OPENHANDS_SUPPRESS_BANNER",
)


def inspect_openhands_vision_support(
    *,
    env: dict[str, str],
    python_executable: str,
) -> dict[str, Any]:
    """Ask the frozen OpenHands SDK how it classifies the configured model."""
    python = shutil.which(python_executable)
    if not python:
        raise RuntimeError(f"OpenHands Python executable not found: {python_executable}")
    probe = subprocess.run(
        [
            python,
            "-c",
            (
                "import json, os; from openhands.sdk import LLM; "
                "llm=LLM(model=os.environ['LLM_MODEL'], api_key=os.environ['LLM_API_KEY'], "
                "base_url=os.environ.get('LLM_BASE_URL'), usage_id='vision-preflight'); "
                "print('MMCODE_VISION_PROBE='+json.dumps({"
                "'supports_vision':llm.vision_is_active(),"
                "'max_input_tokens':llm.max_input_tokens,"
                "'max_output_tokens':llm.max_output_tokens,"
                "'model_info':llm.model_info}))"
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else "unknown error"
        raise RuntimeError(f"OpenHands vision preflight failed: {detail}")
    marker = "MMCODE_VISION_PROBE="
    line = next((line for line in reversed(probe.stdout.splitlines()) if line.startswith(marker)), None)
    if line is None:
        raise RuntimeError("OpenHands vision preflight returned no capability record")
    return json.loads(line.removeprefix(marker))


def has_finish_action(raw_trajectory: Path) -> bool:
    """Return whether the canonical OpenHands stream contains a real finish call."""
    if not raw_trajectory.is_file():
        return False
    for line in raw_trajectory.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = event.get("action") or {}
        if event.get("kind") == "ActionEvent" and (
            event.get("tool_name") == "finish" or action.get("kind") == "FinishAction"
        ):
            return True
    return False


def build_openhands_command(
    prompt: str,
    *,
    runtime_root: Path,
    profile: str = "official",
    vision2web_mode: str = "official",
    python_executable: str = "python3.12",
    installed_executable: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build the official or explicitly experimental OpenHands command.

    The frozen Vision2Web image supplies Python 3.12 and all transitive wheels.
    The official profile executes the repository-vendored upstream CLI entry
    point without monkey patches.  The research profile is a separate path for
    local-model capability overrides and must never be reported as the official
    Vision2Web/OpenHands baseline.
    """
    if profile not in OPENHANDS_PROFILES:
        raise ValueError(f"Unknown OpenHands profile: {profile}")
    if vision2web_mode not in ACCEPTED_VISION2WEB_MODES:
        raise ValueError(f"Unknown Vision2Web mode: {vision2web_mode}")
    vision2web_mode = normalize_vision2web_mode(
        vision2web_mode, scaffold="openhands"
    )
    if vision2web_mode != "official" and profile != "official":
        raise ValueError(
            "Vision2Web browser_enabled/guided_vsv conditions require the official OpenHands "
            "model/runtime profile so tool availability is the only scaffold change"
        )
    env = os.environ.copy()
    if installed_executable:
        if profile != "official" or vision2web_mode != "official":
            raise ValueError(
                "Research OpenHands profiles and Vision2Web browser modes require "
                "the vendored runtime"
            )
        binary = shutil.which(installed_executable)
        if not binary:
            raise RuntimeError(f"OpenHands executable not found: {installed_executable}")
        return [binary, "--headless", "--json", "--override-with-envs", "-t", prompt], env

    source = runtime_root.resolve() / "src"
    entrypoint = source / "openhands_cli" / "entrypoint.py"
    sdk = source / "openhands" / "sdk"
    if not entrypoint.is_file() or not sdk.is_dir():
        raise RuntimeError(f"vendored OpenHands runtime is incomplete: {source}")
    python = shutil.which(python_executable)
    if not python:
        raise RuntimeError(
            f"{python_executable} is absent. Run Vision2Web inside its pinned ClusterX image; "
            "that image supplies Python 3.12 and the frozen OpenHands dependencies."
        )
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(source) if not previous else os.pathsep.join((str(source), previous))
    module = "openhands_cli.entrypoint"
    if profile == "research":
        project_source = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join((str(project_source), env["PYTHONPATH"]))
        module = "multimodalcode.agent_harness.openhands_entrypoint"
    elif vision2web_mode != "official":
        project_source = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join((str(project_source), env["PYTHONPATH"]))
        module = (
            "multimodalcode.agent_harness.vision2web_openhands_browser_entrypoint"
        )
    return [
        python,
        "-m",
        module,
        "--headless",
        "--json",
        "--override-with-envs",
        "-t",
        prompt,
    ], env


def run_openhands(
    case: AgentCase,
    workspace: Path,
    raw_trajectory: Path,
    stderr_path: Path,
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    runtime_root: Path,
    profile: str = "official",
    vision2web_mode: str = "official",
    max_retries: int = 2,
    require_vision: bool = True,
    python_executable: str = "python3.12",
    installed_executable: str | None = None,
) -> dict[str, Any]:
    if max_retries < 0:
        raise ValueError("OpenHands max_retries must be non-negative")
    requested_mode = vision2web_mode
    vision2web_mode = normalize_vision2web_mode(
        requested_mode, scaffold="openhands"
    )
    effective_prompt = prompt_for_mode(case.prompt, vision2web_mode)
    command, env = build_openhands_command(
        effective_prompt,
        runtime_root=runtime_root,
        profile=profile,
        vision2web_mode=vision2web_mode,
        python_executable=python_executable,
        installed_executable=installed_executable,
    )
    # The released Vision2Web adapter passes exactly these three LLM variables
    # to `openhands --override-with-envs`.  Strip our research-only variables so
    # an outer ClusterX environment cannot silently change the official run.
    for name in _RESEARCH_ONLY_ENV:
        env.pop(name, None)
    env.update(
        {
            "LLM_MODEL": model,
            "LLM_API_KEY": api_key,
            "LLM_BASE_URL": base_url.rstrip("/"),
        }
    )
    started = datetime.now(timezone.utc)
    run_token = started.strftime("%Y%m%dT%H%M%S.%fZ")
    trace_root = raw_trajectory.parent / "development" / run_token
    trace_root.mkdir(parents=True, exist_ok=False)
    prompt_path = trace_root / "prompt.json"
    prompt_path.write_text(
        json.dumps(
            prompt_record(
                case.prompt,
                requested_mode,
                scaffold="openhands",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for name in (
        "MULTIMODALCODE_VISION2WEB_MODE",
        "MULTIMODALCODE_BROWSER_TRACE_ROOT",
        "MULTIMODALCODE_WORKSPACE",
    ):
        env.pop(name, None)
    if vision2web_mode != "official":
        env["MULTIMODALCODE_VISION2WEB_MODE"] = vision2web_mode
    if profile == "research":
        env.update(
            {
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "MULTIMODALCODE_FORCE_VISION": "1",
                "MULTIMODALCODE_MAX_INPUT_TOKENS": "32768",
                "MULTIMODALCODE_MAX_OUTPUT_TOKENS": "8192",
                "OPENHANDS_SUPPRESS_BANNER": "1",
            }
        )

    vision_probe = None
    if profile == "official" and require_vision:
        vision_probe = inspect_openhands_vision_support(
            env=env,
            python_executable=python_executable,
        )
        if not vision_probe.get("supports_vision"):
            raise RuntimeError(
                "The frozen official OpenHands/LiteLLM runtime classifies "
                f"{model!r} as text-only, so its file_editor will not expose prototype "
                "pixels. Route the model through the officially supported LiteLLM proxy, "
                "publish supports_vision=true in /v1/model/info, and use a "
                "litellm_proxy/<model> identifier; or select --openhands-profile research "
                "and report it as a non-official compatibility run."
            )

    status = "failed"
    error = None
    returncode = None
    attempts: list[dict[str, Any]] = []
    final_raw: Path | None = None
    final_stderr: Path | None = None
    max_attempts = 1 + max_retries
    recorder = Vision2WebRunRecorder(workspace, trace_root)
    recorder.start()
    try:
        for attempt in range(1, max_attempts + 1):
            attempt_raw = raw_trajectory.with_name(
                f"{raw_trajectory.stem}.attempt-{attempt}{raw_trajectory.suffix}"
            )
            attempt_stderr = stderr_path.with_name(
                f"{stderr_path.stem}.attempt-{attempt}{stderr_path.suffix}"
            )
            attempt_started = datetime.now(timezone.utc)
            with attempt_raw.open("w", encoding="utf-8") as stdout, attempt_stderr.open(
                "w", encoding="utf-8"
            ) as stderr:
                browser_recorder = None
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    env=env,
                    text=True,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                try:
                    if vision2web_mode != "official":
                        browser_recorder = NativeOpenHandsBrowserRecorder(
                            attempt_raw,
                            workspace,
                            trace_root,
                        )
                        browser_recorder.start()
                    returncode = process.wait(timeout=timeout if timeout > 0 else None)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    returncode = process.returncode
                    status = "timeout"
                    error = f"OpenHands exceeded {timeout} seconds"
                else:
                    if returncode != 0:
                        status = "failed"
                        error = f"OpenHands exited with code {returncode}"
                    elif (case.metadata.get("protocol") != "verification_handoff"
                          and not (workspace / "start.sh").is_file()):
                        status = "failed"
                        error = "OpenHands completed without generating /workspace/start.sh"
                    elif profile == "research" and not has_finish_action(attempt_raw):
                        status = "incomplete"
                        error = "Research profile requires an OpenHands FinishAction"
                    else:
                        status = "success"
                        error = None
                finally:
                    if browser_recorder is not None:
                        browser_recorder.finish()

            attempt_ended = datetime.now(timezone.utc)
            final_raw = attempt_raw
            final_stderr = attempt_stderr
            attempts.append(
                {
                    "attempt": attempt,
                    "status": status,
                    "error": error,
                    "returncode": returncode,
                    "finish_action": has_finish_action(attempt_raw),
                    "duration_seconds": (attempt_ended - attempt_started).total_seconds(),
                    "raw_trajectory": str(attempt_raw),
                    "stderr": str(attempt_stderr),
                }
            )
            # Match the released engine: success terminates the loop; timeout is
            # terminal; only ordinary failures are retried in the same workspace.
            if status in {"success", "timeout"} or attempt == max_attempts:
                break

        assert final_raw is not None and final_stderr is not None
        shutil.copy2(final_raw, raw_trajectory)
        shutil.copy2(final_stderr, stderr_path)
    finally:
        # Preserve P_final and stop the passive observer even if process launch,
        # retry bookkeeping, or trajectory copying raises unexpectedly.
        versions = recorder.finish()
    timeline = build_development_timeline(
        raw_trajectory,
        trace_root,
        attempt_trajectories=[Path(row["raw_trajectory"]) for row in attempts],
    )
    ended = datetime.now(timezone.utc)
    return {
        "status": status,
        "error": error,
        "returncode": returncode,
        "command": command,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "workspace": str(workspace),
        "openhands_profile": profile,
        "vision2web_mode": vision2web_mode,
        "requested_vision2web_mode": requested_mode,
        "browser_interface_revision": (
            BROWSER_INTERFACE_REVISION if vision2web_mode != "official" else None
        ),
        "effective_prompt": str(prompt_path),
        "development_trace": str(trace_root),
        "versions": versions,
        "timeline": timeline,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "finish_action": has_finish_action(raw_trajectory),
        "vision_probe": vision_probe,
    }

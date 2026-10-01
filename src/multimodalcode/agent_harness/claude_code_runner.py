"""Direct-container adapter for Vision2Web's official Claude Code scaffold.

The released adapter enters a Docker sandbox with ``docker exec`` and then
runs Claude Code in ``/workspace``.  A ClusterX case worker already *is* that
sandbox, so this module removes only the outer Docker transport.  Prompt,
environment, command-line flags, retry semantics, and the ``start.sh`` success
condition remain aligned with the frozen upstream implementation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

from .cases import AgentCase
from .vision2web_experiment import (
    ACCEPTED_VISION2WEB_MODES,
    CLAUDE_BROWSER_INTERFACE_REVISION,
    normalize_vision2web_mode,
    prompt_for_mode,
    prompt_record,
)
from .vision2web_trace import (
    Vision2WebRunRecorder,
    append_trace_event,
    build_claude_development_timeline,
    capture_observation_binding,
    capture_first_application_observation,
    is_deployed_application_url,
)


_PLAYWRIGHT_COMMAND = re.compile(
    r"(?:^|[;&|]\s*|\n\s*)(?:[^\s;&|]*/)?playwright-cli\s+"
    r"(?P<verb>open|goto|snapshot|screenshot|click|fill|type|press|hover|select|"
    r"check|uncheck|reload|go-back|close)\b(?P<arguments>[^\n;&|]*)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?[^\s'\"]*", re.I)


def _message_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        content = event.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _image_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [
        item
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "image"
        and isinstance(item.get("source"), dict)
    ]


class ClaudeBrowserEvidenceObserver:
    """Audit browser evidence already present in Claude Code stream-json.

    The observer does not add a tool or instruct Claude. It records the real
    Bash/Read tool exchange and creates ``P_first`` only when a deployed-app
    observation is present in the model-facing tool result.
    """

    def __init__(self, workspace: Path, trace_root: Path):
        self.workspace = workspace.resolve()
        self.trace_root = trace_root.resolve()
        self.tools: dict[str, tuple[str, dict[str, Any]]] = {}
        self.current_url = ""
        self.playwright_command_count = 0
        self.image_read_count = 0
        self.model_context_image_confirmed = False

    def start_attempt(self) -> None:
        """Reset session-local correlation without discarding aggregate evidence."""

        self.tools = {}
        self.current_url = ""

    def consume(
        self,
        line: str,
        *,
        timestamp: str,
        monotonic_ns: int,
        sequence: int,
    ) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        for block in _message_blocks(event):
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "")
                tool = str(block.get("name") or "")
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = {}
                if tool_id:
                    self.tools[tool_id] = (tool, tool_input)
                self._record_tool_use(
                    tool_id,
                    tool,
                    tool_input,
                    timestamp=timestamp,
                    monotonic_ns=monotonic_ns,
                    sequence=sequence,
                )
            elif block.get("type") == "tool_result":
                self._record_tool_result(
                    block,
                    timestamp=timestamp,
                    monotonic_ns=monotonic_ns,
                    sequence=sequence,
                )

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        append_trace_event(
            self.trace_root,
            event_type,
            payload,
            filename="browser.events.jsonl",
        )

    def _record_tool_use(
        self,
        tool_id: str,
        tool: str,
        tool_input: dict[str, Any],
        *,
        timestamp: str,
        monotonic_ns: int,
        sequence: int,
    ) -> None:
        if tool.casefold() != "bash":
            return
        command = str(tool_input.get("command") or "")
        matches = list(_PLAYWRIGHT_COMMAND.finditer(command))
        if not matches:
            return
        commands = []
        for match in matches:
            verb = match.group("verb").casefold()
            arguments = match.group("arguments").strip()
            url_match = _URL.search(arguments)
            if verb in {"open", "goto"} and url_match:
                self.current_url = url_match.group(0)
            commands.append({"verb": verb, "arguments": arguments})
        self.playwright_command_count += len(commands)
        self._event(
            "claude_browser_command_requested",
            {
                "tool_use_id": tool_id,
                "commands": commands,
                "current_url": self.current_url,
                "raw_stream_timestamp": timestamp,
                "raw_stream_monotonic_ns": monotonic_ns,
                "raw_stream_sequence": sequence,
            },
        )

    def _record_tool_result(
        self,
        block: dict[str, Any],
        *,
        timestamp: str,
        monotonic_ns: int,
        sequence: int,
    ) -> None:
        tool_id = str(block.get("tool_use_id") or "")
        tool, tool_input = self.tools.get(tool_id, ("", {}))
        content = block.get("content")
        is_error = bool(block.get("is_error"))
        if tool.casefold() == "bash":
            command = str(tool_input.get("command") or "")
            matches = list(_PLAYWRIGHT_COMMAND.finditer(command))
            if matches:
                text = _result_text(content)
                observation_binding = capture_observation_binding(
                    self.workspace,
                    self.trace_root,
                    url=self.current_url,
                    observation_succeeded=not is_error,
                )
                self._event(
                    "claude_browser_command_result",
                    {
                        "tool_use_id": tool_id,
                        "commands": [match.group("verb").casefold() for match in matches],
                        "current_url": self.current_url,
                        "is_error": is_error,
                        "result_chars": len(text),
                        "result_preview": text[:1000],
                        "raw_stream_timestamp": timestamp,
                        "raw_stream_monotonic_ns": monotonic_ns,
                        "raw_stream_sequence": sequence,
                        "observation_binding": observation_binding,
                    },
                )
                # ``open`` and ``goto`` return a model-facing accessibility
                # snapshot in the pinned playwright-cli, just like an explicit
                # ``snapshot`` command. Capture P_first at that first real
                # deployed-app observation; waiting for a later PNG Read would
                # place the checkpoint after repairs based on initial runtime
                # evidence.
                if (
                    not is_error
                    and any(
                        match.group("verb").casefold() in {"open", "goto", "snapshot"}
                        for match in matches
                    )
                    and is_deployed_application_url(self.current_url)
                ):
                    capture_first_application_observation(
                        self.workspace,
                        self.trace_root,
                        framework="claude_code",
                        tool="playwright-cli state",
                        url=self.current_url,
                        evidence_path=None,
                    )
            return

        if tool.casefold() != "read":
            return
        file_value = tool_input.get("file_path") or tool_input.get("path") or ""
        path = Path(str(file_value))
        if not path.is_absolute():
            path = self.workspace / path
        images = _image_blocks(content)
        if not images:
            return
        image_records = []
        for image in images:
            source = image["source"]
            data = source.get("data")
            digest = None
            if source.get("type") == "base64" and isinstance(data, str):
                try:
                    digest = hashlib.sha256(base64.b64decode(data)).hexdigest()
                except (ValueError, TypeError):
                    digest = None
            image_records.append(
                {
                    "media_type": source.get("media_type"),
                    "source_type": source.get("type"),
                    "sha256": digest,
                }
            )
        self.image_read_count += 1
        self.model_context_image_confirmed = True
        first_hash = image_records[0].get("sha256")
        observation_binding = capture_observation_binding(
            self.workspace,
            self.trace_root,
            url=self.current_url,
            observation_succeeded=True,
        )
        self._event(
            "claude_png_entered_model_context",
            {
                "tool_use_id": tool_id,
                "path": str(path),
                "current_url": self.current_url,
                "stream_json_contains_image_block": True,
                "images": image_records,
                "raw_stream_timestamp": timestamp,
                "raw_stream_monotonic_ns": monotonic_ns,
                "raw_stream_sequence": sequence,
                "observation_binding": observation_binding,
            },
        )
        capture_first_application_observation(
            self.workspace,
            self.trace_root,
            framework="claude_code",
            tool="Read(PNG)",
            url=self.current_url,
            screenshot_sha256=str(first_hash or "") or None,
            evidence_path=str(path),
        )

    def summary(self) -> dict[str, Any]:
        result = {
            "schema": "multimodalcode-claude-browser-context-evidence-1",
            "playwright_command_count": self.playwright_command_count,
            "png_read_tool_result_count": self.image_read_count,
            "model_context_image_confirmed": self.model_context_image_confirmed,
            "evidence": str(self.trace_root / "browser.events.jsonl"),
        }
        (self.trace_root / "claude_browser_context.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result


def build_claude_code_environment(
    *, base_url: str | None, api_key: str | None, model: str
) -> dict[str, str]:
    """Return the exact environment mapping used by frozen Vision2Web.

    Keep this small function independently testable against
    ``vision2web.core.utils.build_claude_code_env``.  It intentionally returns
    only the adapter-owned variables rather than a copy of the process
    environment.
    """

    values = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "IS_SANDBOX": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "CLAUDE_CODE_EFFORT_LEVEL": "high",
    }
    return {key: value for key, value in values.items() if value is not None}


def build_claude_code_command(
    prompt: str,
    *,
    executable: str = "claude",
    resume_session_id: str | None = None,
) -> list[str]:
    """Build the in-container portion of the released Claude Code command."""

    binary = shutil.which(executable)
    if not binary:
        raise RuntimeError(
            f"Claude Code executable not found: {executable}. Run this adapter "
            "inside the pinned Vision2Web ClusterX image."
        )
    command = [
        binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
    ]
    if resume_session_id:
        command.extend(["--resume", resume_session_id])
    command.extend(["-p", prompt])
    return command


def read_claude_session_id(raw_trajectory: Path) -> str:
    """Return the session persisted by one completed Claude Code invocation."""

    with raw_trajectory.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = event.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                return session_id.strip()
    raise RuntimeError(f"Claude Code trajectory has no session_id: {raw_trajectory}")


def _capture_stream(
    source: TextIO,
    destination: TextIO,
    capture: TextIO,
    *,
    stream_name: str,
    capture_lock: threading.Lock,
    line_observer: Callable[..., None] | None = None,
) -> None:
    """Copy a child stream byte-for-byte while recording receive times."""

    for sequence, line in enumerate(iter(source.readline, "")):
        destination.write(line)
        destination.flush()
        timestamp = datetime.now(timezone.utc).isoformat()
        monotonic_ns = time.monotonic_ns()
        if line_observer is not None:
            try:
                line_observer(
                    line,
                    timestamp=timestamp,
                    monotonic_ns=monotonic_ns,
                    sequence=sequence,
                )
            except Exception as exc:
                # Evidence auditing must never stop draining Claude's stdout.
                with capture_lock:
                    capture.write(
                        json.dumps(
                            {
                                "timestamp": timestamp,
                                "monotonic_ns": monotonic_ns,
                                "stream": "observer",
                                "sequence": sequence,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        + "\n"
                    )
                    capture.flush()
        with capture_lock:
            capture.write(
                json.dumps(
                    {
                        "timestamp": timestamp,
                        "monotonic_ns": monotonic_ns,
                        "stream": stream_name,
                        "sequence": sequence,
                    }
                )
                + "\n"
            )
            capture.flush()


def _run_attempt(
    command: list[str],
    *,
    workspace: Path,
    env: dict[str, str],
    raw_path: Path,
    stderr_path: Path,
    capture_path: Path,
    timeout: int,
    line_observer: Callable[..., None] | None = None,
) -> tuple[int, bool]:
    """Run one Claude invocation and preserve live stdout/stderr chronology."""

    with raw_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file, capture_path.open("w", encoding="utf-8") as capture_file:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        capture_lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_capture_stream,
                args=(process.stdout, stdout_file, capture_file),
                kwargs={
                    "stream_name": "stdout",
                    "capture_lock": capture_lock,
                    "line_observer": line_observer,
                },
                daemon=True,
            ),
            threading.Thread(
                target=_capture_stream,
                args=(process.stderr, stderr_file, capture_file),
                kwargs={
                    "stream_name": "stderr",
                    "capture_lock": capture_lock,
                },
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout if timeout > 0 else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            returncode = process.wait()
        finally:
            for thread in threads:
                thread.join(timeout=10)
            process.stdout.close()
            process.stderr.close()
    return returncode, timed_out


def _attempt_artifact_path(
    base: Path, *, attempt: int, run_token: str
) -> Path:
    """Allocate a retry artifact without overwriting an earlier job's trace."""

    candidate = base.with_name(f"{base.stem}.attempt-{attempt}{base.suffix}")
    if not candidate.exists():
        return candidate
    return base.with_name(
        f"{base.stem}.{run_token}.attempt-{attempt}{base.suffix}"
    )


def run_claude_code(
    case: AgentCase,
    workspace: Path,
    raw_trajectory: Path,
    stderr_path: Path,
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    vision2web_mode: str = "official",
    max_retries: int = 2,
    executable: str = "claude",
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    """Run the official Claude Code policy directly in a case container."""

    if case.benchmark != "vision2web":
        raise ValueError("Claude Code direct-container adapter is Vision2Web-only")
    if vision2web_mode not in ACCEPTED_VISION2WEB_MODES:
        raise ValueError(f"Unknown Vision2Web mode: {vision2web_mode}")
    requested_mode = vision2web_mode
    vision2web_mode = normalize_vision2web_mode(
        requested_mode, scaffold="claude_code"
    )
    if max_retries < 0:
        raise ValueError("Claude Code max_retries must be non-negative")

    effective_prompt = prompt_for_mode(case.prompt, vision2web_mode)
    command = build_claude_code_command(
        effective_prompt,
        executable=executable,
        resume_session_id=resume_session_id,
    )
    env = os.environ.copy()
    claude_environment = build_claude_code_environment(
        base_url=base_url.rstrip("/"), api_key=api_key, model=model
    )
    effort_override = os.environ.get("MMCODE_CLAUDE_CODE_EFFORT_LEVEL")
    if effort_override:
        if effort_override not in {"xhigh", "medium", "low"}:
            raise ValueError(
                "MMCODE_CLAUDE_CODE_EFFORT_LEVEL must be xhigh, medium, or low"
            )
        claude_environment["CLAUDE_CODE_EFFORT_LEVEL"] = effort_override
    env.update(claude_environment)

    started = datetime.now(timezone.utc)
    run_token = started.strftime("%Y%m%dT%H%M%S.%fZ")
    trace_root = raw_trajectory.parent / "development" / run_token
    trace_root.mkdir(parents=True, exist_ok=False)
    record = prompt_record(
        case.prompt,
        requested_mode,
        scaffold="claude_code",
    )
    # The released image installs playwright-cli's Claude skill before either
    # condition runs. Do not fabricate a browser-only Claude condition: the
    # active comparison is official versus guided_vsv with identical tools.
    record.update(
        {
            "scaffold": "claude-code-cli",
            "browser_interfaces": True,
            "browser_interface_revision": CLAUDE_BROWSER_INTERFACE_REVISION,
            "browser_affordance_is_official": True,
            "resume_session_id": resume_session_id,
            "claude_code_effort_level": claude_environment[
                "CLAUDE_CODE_EFFORT_LEVEL"
            ],
        }
    )
    prompt_path = trace_root / "prompt.json"
    prompt_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    status = "failed"
    error: str | None = None
    returncode: int | None = None
    attempts: list[dict[str, Any]] = []
    final_raw: Path | None = None
    final_stderr: Path | None = None
    capture_paths: list[Path] = []
    recorder = Vision2WebRunRecorder(workspace, trace_root)
    browser_observer = ClaudeBrowserEvidenceObserver(workspace, trace_root)
    recorder.start()
    try:
        for attempt in range(1, max_retries + 2):
            browser_observer.start_attempt()
            attempt_raw = _attempt_artifact_path(
                raw_trajectory, attempt=attempt, run_token=run_token
            )
            attempt_stderr = _attempt_artifact_path(
                stderr_path, attempt=attempt, run_token=run_token
            )
            capture_path = _attempt_artifact_path(
                raw_trajectory.with_name("claude.capture.jsonl"),
                attempt=attempt,
                run_token=run_token,
            )
            attempt_started = datetime.now(timezone.utc)
            returncode, timed_out = _run_attempt(
                command,
                workspace=workspace,
                env=env,
                raw_path=attempt_raw,
                stderr_path=attempt_stderr,
                capture_path=capture_path,
                timeout=timeout,
                line_observer=browser_observer.consume,
            )
            if timed_out:
                status = "timeout"
                error = f"Claude Code exceeded {timeout} seconds"
            elif returncode != 0:
                status = "failed"
                error = f"Claude Code exited with code {returncode}"
            elif (case.metadata.get("protocol") != "verification_handoff"
                  and not (workspace / "start.sh").is_file()):
                status = "failed"
                error = "Claude Code completed without generating /workspace/start.sh"
            else:
                status = "success"
                error = None

            attempt_ended = datetime.now(timezone.utc)
            final_raw = attempt_raw
            final_stderr = attempt_stderr
            capture_paths.append(capture_path)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": status,
                    "error": error,
                    "returncode": returncode,
                    "duration_seconds": (
                        attempt_ended - attempt_started
                    ).total_seconds(),
                    "raw_trajectory": str(attempt_raw),
                    "stderr": str(attempt_stderr),
                    "capture": str(capture_path),
                }
            )
            # Match the released inference engine: keep the workspace across
            # ordinary failures, terminate on success or timeout.
            if status in {"success", "timeout"} or attempt == max_retries + 1:
                break

        assert final_raw is not None and final_stderr is not None
        shutil.copy2(final_raw, raw_trajectory)
        shutil.copy2(final_stderr, stderr_path)
    finally:
        versions = recorder.finish()
    browser_context = browser_observer.summary()

    timeline = build_claude_development_timeline(
        raw_trajectory,
        trace_root,
        attempt_trajectories=[Path(row["raw_trajectory"]) for row in attempts],
        capture_paths=capture_paths,
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
        "framework": "claude_code",
        "vision2web_mode": vision2web_mode,
        "requested_vision2web_mode": requested_mode,
        "browser_interface_revision": CLAUDE_BROWSER_INTERFACE_REVISION,
        "native_browser_interface": "playwright-cli Claude skill",
        "browser_affordance_is_official": True,
        "resumed_session": resume_session_id is not None,
        "resume_session_id": resume_session_id,
        "claude_code_effort_level": claude_environment[
            "CLAUDE_CODE_EFFORT_LEVEL"
        ],
        "effective_prompt": str(prompt_path),
        "development_trace": str(trace_root),
        "versions": versions,
        "timeline": timeline,
        "browser_context": browser_context,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }

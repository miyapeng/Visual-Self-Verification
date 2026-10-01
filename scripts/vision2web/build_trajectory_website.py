#!/usr/bin/env python3
"""Build a static Vision2Web action/observation trajectory explorer.

The explorer is post-trajectory analysis only.  It reads public task inputs and
agent traces, never ``workflow.json`` or an evaluator.  Every displayed action,
observation, thought, screenshot, and workspace event is backed by a preserved
artifact.  Pending Claude Code runs are shown as pending rather than synthesized.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.registry import PROJECT_ROOT


OPENHANDS_ROOT = (
    PROJECT_ROOT
    / "runs/vision2web_generation/qwen35-9b-openhands-selfverify-v5-local-bypass"
    / "agents/litellm_proxy__Qwen3.5-9B/vision2web"
)
CLAUDE_ROOT = (
    PROJECT_ROOT
    / "runs/vision2web_generation/qwen35-9b-claude-code-smoke-cpu-v2-sandboxfixed"
)


@dataclass(frozen=True)
class Sample:
    framework: str
    mode: str
    case_id: str
    reason: str

    @property
    def level(self) -> str:
        return {
            "webpage": "Level 1",
            "frontend": "Level 2",
            "website": "Level 3",
        }[self.case_id.split("/", 1)[0]]

    @property
    def slug(self) -> str:
        return safe(f"{self.framework}-{self.mode}-{self.case_id}")


CASES = (
    "webpage/classic-clashes",
    "frontend/forum_vectorworks",
    "website/permanent",
)
# This fixed explorer points at preserved pre-renaming runs.  New experiments use
# official/browser_enabled/guided_vsv, but adding those names to this archived
# Cartesian product would misleadingly render them as unfinished historical jobs.
ARCHIVED_MODES = ("official", "tools", "self_verify")
SAMPLES = tuple(
    Sample(
        framework,
        mode,
        case_id,
        (
            f"{framework} · {mode} 的真实 smoke 轨迹；"
            "用于区分工具是否存在、是否成功执行以及模型是否主动调用。"
        ),
    )
    for framework in ("openhands", "claude_code")
    for case_id in CASES
    for mode in ARCHIVED_MODES
)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("_")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def iso_key(value: Any) -> str:
    return str(value or "9999")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def text_blocks(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "thinking"}:
            parts.append(str(block.get("text") or block.get("thinking") or ""))
    return "\n".join(part for part in parts if part)


def compact_payload(value: Any) -> Any:
    """Remove binary image payloads while preserving commands and textual output."""

    if isinstance(value, list):
        return [compact_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "screenshot_data":
            if isinstance(item, str) and item:
                result[key] = f"<extracted {len(item)} base64 characters>"
            continue
        if key == "data" and isinstance(item, str) and len(item) > 4096:
            result[key] = f"<binary payload omitted: {len(item)} characters>"
            continue
        result[key] = compact_payload(item)
    return result


def copy_file(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination.as_posix()


def relative_copy(source: Path, output: Path, relative: Path) -> str:
    target = output / relative
    copy_file(source, target)
    return target.relative_to(output).as_posix()


def save_image_payload(
    encoded: str,
    *,
    output: Path,
    relative_dir: Path,
    stem: str,
    media_type: str | None = None,
) -> dict[str, Any] | None:
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        media_type = header[5:].split(";", 1)[0] or media_type
    try:
        data = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        return None
    if not data:
        return None
    extension = ".png"
    if data[:3] == b"\xff\xd8\xff" or media_type == "image/jpeg":
        extension = ".jpg"
    digest = sha256_bytes(data)
    target = output / relative_dir / f"{safe(stem)}-{digest[:12]}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return {
        "path": target.relative_to(output).as_posix(),
        "sha256": digest,
        "bytes": len(data),
        "media_type": media_type or ("image/jpeg" if extension == ".jpg" else "image/png"),
    }


def copy_public_inputs(sample: Sample, output: Path) -> tuple[str, list[dict[str, Any]]]:
    case = load_case("vision2web", sample.case_id)
    images = []
    for index, source_value in enumerate(case.image_paths, start=1):
        source = Path(source_value)
        relative = Path("assets/prototypes") / sample.slug / f"{index:02d}-{source.name}"
        images.append(
            {
                "label": source.name,
                "path": relative_copy(source, output, relative),
                "sha256": sha256_bytes(source.read_bytes()),
            }
        )
    return case.prompt, images


def openhands_dir(sample: Sample) -> Path:
    return OPENHANDS_ROOT / sample.mode / sample.case_id.replace("/", "__")


def claude_dir(sample: Sample) -> Path:
    return (
        CLAUDE_ROOT
        / "agents/Qwen3.5-9B/vision2web/claude_code"
        / sample.mode
        / sample.case_id.replace("/", "__")
    )


def attempt_number(path: Path) -> int:
    match = re.search(r"attempt-(\d+)", path.name)
    return int(match.group(1)) if match else 1


def raw_json_events(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield line_number, event


def observation_images(
    observation: dict[str, Any],
    *,
    output: Path,
    sample: Sample,
    attempt: int,
    step_index: int,
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    screenshot = observation.get("screenshot_data")
    if isinstance(screenshot, str) and screenshot:
        record = save_image_payload(
            screenshot,
            output=output,
            relative_dir=Path("assets/screenshots") / sample.slug,
            stem=f"attempt-{attempt}-step-{step_index}-screenshot",
        )
        if record:
            images.append(record)
    for image_index, block in enumerate(observation.get("content") or [], start=1):
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else block
        encoded = source.get("data")
        if not isinstance(encoded, str) or not encoded:
            continue
        record = save_image_payload(
            encoded,
            output=output,
            relative_dir=Path("assets/screenshots") / sample.slug,
            stem=f"attempt-{attempt}-step-{step_index}-content-{image_index}",
            media_type=source.get("media_type"),
        )
        if record:
            images.append(record)
    return images


def thought_text(event: dict[str, Any]) -> str:
    parts = [text_blocks(event.get("thought"))]
    reasoning = event.get("reasoning_content")
    if isinstance(reasoning, str):
        parts.append(reasoning)
    return "\n".join(part for part in parts if part)


def observation_text(observation: dict[str, Any], event: dict[str, Any]) -> str:
    content = text_blocks(observation.get("content"))
    if content:
        return content
    for key in ("message", "error", "detail"):
        if event.get(key):
            return str(event[key])
    return ""


def parse_workspace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates = sorted((run_dir / "development").glob("*/workspace.events.jsonl"))
    if not candidates:
        return []
    result = []
    for _, event in raw_json_events(candidates[-1]):
        kind = str(event.get("type") or "workspace_event")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind in {"first_application_observation_version", "final_submission_version"}:
            payload = {
                "path": payload.get("path"),
                "program_sha256": payload.get("program_sha256"),
                "file_count": payload.get("file_count"),
            }
        result.append(
            {
                "timestamp": event.get("timestamp"),
                "type": kind,
                "payload": compact_payload(payload),
            }
        )
    return result


def attach_workspace_events(attempts: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    steps = [step for attempt in attempts for step in attempt["steps"]]
    steps.sort(key=lambda row: iso_key(row.get("timestamp")))
    for step in steps:
        step["workspace_events"] = []
    unattached = []
    for event in events:
        stamp = iso_key(event.get("timestamp"))
        owner = None
        for index, step in enumerate(steps):
            start = iso_key(step.get("timestamp"))
            end = iso_key(steps[index + 1].get("timestamp")) if index + 1 < len(steps) else "9999"
            if start <= stamp < end:
                owner = step
                break
        if owner is None:
            unattached.append(event)
        else:
            owner["workspace_events"].append(event)
    if attempts:
        attempts[0]["unattached_workspace_events"] = unattached


def classify_step(step: dict[str, Any]) -> list[str]:
    tool = str(step.get("tool") or "").casefold()
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    command = str(action.get("command") or "")
    phases: list[str] = []
    if tool in {"file_editor", "edit", "write", "multiedit", "notebookedit"}:
        phases.append("edit")
    if any(event.get("type") == "workspace_change" for event in step.get("workspace_events", [])):
        phases.append("edit-effect")
    if tool.startswith("browser_") or "playwright-cli" in command:
        phases.append("browser")
    if tool in {"view_image", "read"} and re.search(r"\.(?:png|jpe?g|webp)\b", str(action), re.I):
        phases.append("visual-input")
    if re.search(r"start\.sh|npm\s+run\s+(?:dev|start)|\bvite\b|http\.server\s+3000", command):
        phases.append("deploy")
    if re.search(r"pytest|npm\s+test|curl\b|playwright", command):
        phases.append("test")
    if tool in {"finish", "submit"} or str(action.get("kind", "")).casefold().startswith("finish"):
        phases.append("submit")
    if not phases:
        phases.append("inspect" if tool in {"terminal", "glob", "grep", "read"} else "other")
    return list(dict.fromkeys(phases))


def derive_loop_stages(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    deployed = False
    deployed_visual_seen = False
    post_visual_edit = False
    rechecked = False
    current_url = ""
    counts: dict[str, int] = {}
    authored_interaction = False
    normal_submit = False
    for attempt in attempts:
        for step in attempt["steps"]:
            phases = classify_step(step)
            step["phases"] = phases
            for phase in phases:
                counts[phase] = counts.get(phase, 0) + 1
            tool = str(step.get("tool") or "")
            action = step.get("action") if isinstance(step.get("action"), dict) else {}
            if tool in {"browser_navigate", "browser_open"}:
                current_url = str(action.get("url") or current_url)
            command = str(action.get("command") or "")
            match = re.search(
                r"(?:[^\s;&|]*/)?playwright-cli\s+(?:open|goto)\s+([^\s;&|]+)",
                command,
            )
            if match:
                current_url = match.group(1).strip("'\"")
            step["browser_url_context"] = current_url or None
            if "deploy" in phases or any(
                event.get("type") == "deployment_ready" for event in step.get("workspace_events", [])
            ):
                deployed = True
            has_image = any(obs.get("images") for obs in step.get("observations", []))
            deployed_app = bool(re.match(r"https?://(?:localhost|127\.0\.0\.1):3000(?:/|$)", current_url))
            if "browser" in phases and deployed_app and has_image:
                if post_visual_edit:
                    rechecked = True
                deployed_visual_seen = True
            if deployed_visual_seen and ("edit" in phases or "edit-effect" in phases):
                post_visual_edit = True
            if tool in {"browser_execute_plan", "browser_click", "browser_fill", "browser_press"}:
                authored_interaction = True
            if "submit" in phases:
                normal_submit = True
            if "submit" in phases:
                stage = "submit"
            elif post_visual_edit and "browser" in phases:
                stage = "recheck"
            elif deployed_visual_seen and ("edit" in phases or "edit-effect" in phases):
                stage = "repair"
            elif "browser" in phases and deployed:
                stage = "observe"
            elif "deploy" in phases:
                stage = "deploy"
            elif not deployed:
                stage = "implement"
            else:
                stage = "develop"
            step["inferred_stage"] = stage
    return {
        "phase_counts": counts,
        "service_start_requested": counts.get("deploy", 0) > 0,
        "deployed_app_visual_observed": deployed_visual_seen,
        "task_interaction_authored": authored_interaction,
        "post_visual_edit": post_visual_edit,
        "post_edit_recheck": rechecked,
        "normal_submit_action": normal_submit,
        "stage_labels_are_offline_heuristics": True,
    }


def parse_openhands(sample: Sample, run_dir: Path, output: Path) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    raw_links = []
    paths = sorted(run_dir.glob("openhands.events.attempt-*.jsonl"), key=attempt_number)
    if not paths and (run_dir / "openhands.events.jsonl").is_file():
        paths = [run_dir / "openhands.events.jsonl"]
    for raw_path in paths:
        attempt = attempt_number(raw_path)
        raw_links.append(
            relative_copy(raw_path, output, Path("raw") / sample.slug / raw_path.name)
        )
        steps: list[dict[str, Any]] = []
        by_action: dict[str, dict[str, Any]] = {}
        messages = []
        unmatched = []
        for line_number, event in raw_json_events(raw_path):
            kind = str(event.get("kind") or "")
            if kind == "MessageEvent":
                messages.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "source": event.get("source"),
                        "text": text_blocks((event.get("llm_message") or {}).get("content")),
                        "raw_line": line_number,
                    }
                )
                continue
            if kind == "ActionEvent":
                action = event.get("action") if isinstance(event.get("action"), dict) else {}
                if not action and isinstance(event.get("tool_call"), dict):
                    arguments = event["tool_call"].get("arguments")
                    try:
                        action = json.loads(arguments) if isinstance(arguments, str) else {}
                    except json.JSONDecodeError:
                        action = {"arguments": arguments}
                step = {
                    "index": len(steps) + 1,
                    "attempt": attempt,
                    "event_id": event.get("id"),
                    "tool_call_id": event.get("tool_call_id"),
                    "timestamp": event.get("timestamp"),
                    "tool": event.get("tool_name") or (event.get("tool_call") or {}).get("name"),
                    "summary": event.get("summary"),
                    "security_risk": event.get("security_risk"),
                    "reasoning": thought_text(event),
                    "action": compact_payload(action),
                    "observations": [],
                    "raw_line": line_number,
                }
                steps.append(step)
                if event.get("id"):
                    by_action[str(event["id"])] = step
                continue
            if kind in {"ObservationEvent", "AgentErrorEvent"}:
                owner = by_action.get(str(event.get("action_id") or ""))
                if owner is None and steps:
                    owner = steps[-1]
                observation = event.get("observation") if isinstance(event.get("observation"), dict) else {}
                record = {
                    "event_id": event.get("id"),
                    "timestamp": event.get("timestamp"),
                    "kind": kind,
                    "tool": event.get("tool_name"),
                    "is_error": bool(observation.get("is_error") or kind == "AgentErrorEvent"),
                    "error_code": event.get("error_code"),
                    "text": observation_text(observation, event),
                    "details": compact_payload(
                        {
                            key: value
                            for key, value in observation.items()
                            if key not in {"content", "screenshot_data"}
                        }
                    ),
                    "images": observation_images(
                        observation,
                        output=output,
                        sample=sample,
                        attempt=attempt,
                        step_index=owner["index"] if owner else len(steps) + 1,
                    ),
                    "raw_line": line_number,
                }
                if owner is None:
                    unmatched.append(record)
                else:
                    owner["observations"].append(record)
                continue
            if event.get("source") or kind:
                unmatched.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "kind": kind or event.get("type"),
                        "source": event.get("source"),
                        "details": compact_payload(event),
                        "raw_line": line_number,
                    }
                )
        attempts.append(
            {
                "attempt": attempt,
                "source": raw_links[-1],
                "messages": messages,
                "steps": steps,
                "unmatched_events": unmatched,
            }
        )
    workspace_events = parse_workspace_events(run_dir)
    attach_workspace_events(attempts, workspace_events)
    behavior = derive_loop_stages(attempts)
    return {
        "attempts": attempts,
        "behavior": behavior,
        "raw_links": raw_links,
        "workspace_event_count": len(workspace_events),
    }


def claude_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else event.get("content")
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def parse_claude(sample: Sample, run_dir: Path, output: Path) -> dict[str, Any]:
    attempts = []
    raw_links = []
    for raw_path in sorted(run_dir.glob("claude.events.attempt-*.jsonl"), key=attempt_number):
        attempt = attempt_number(raw_path)
        raw_links.append(relative_copy(raw_path, output, Path("raw") / sample.slug / raw_path.name))
        steps: list[dict[str, Any]] = []
        by_tool_id: dict[str, dict[str, Any]] = {}
        messages = []
        unmatched = []
        for line_number, event in raw_json_events(raw_path):
            blocks = claude_blocks(event)
            text = "\n".join(
                str(block.get("text") or "")
                for block in blocks
                if block.get("type") == "text" and block.get("text")
            )
            thinking = "\n".join(
                str(block.get("thinking") or "")
                for block in blocks
                if block.get("type") == "thinking" and block.get("thinking")
            )
            tool_uses = [block for block in blocks if block.get("type") == "tool_use"]
            for block in tool_uses:
                tool_id = str(block.get("id") or "")
                step = {
                    "index": len(steps) + 1,
                    "attempt": attempt,
                    "event_id": tool_id,
                    "tool_call_id": tool_id,
                    "timestamp": event.get("timestamp"),
                    "tool": block.get("name"),
                    "summary": text,
                    "security_risk": None,
                    "reasoning": thinking,
                    "action": compact_payload(block.get("input") or {}),
                    "observations": [],
                    "raw_line": line_number,
                }
                steps.append(step)
                if tool_id:
                    by_tool_id[tool_id] = step
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id") or "")
                owner = by_tool_id.get(tool_id)
                content = block.get("content")
                pseudo = {"content": content if isinstance(content, list) else []}
                record = {
                    "event_id": None,
                    "timestamp": event.get("timestamp"),
                    "kind": "tool_result",
                    "tool": owner.get("tool") if owner else None,
                    "is_error": bool(block.get("is_error")),
                    "error_code": None,
                    "text": text_blocks(content) if isinstance(content, list) else str(content or ""),
                    "details": {"tool_use_id": tool_id},
                    "images": observation_images(
                        pseudo,
                        output=output,
                        sample=sample,
                        attempt=attempt,
                        step_index=owner["index"] if owner else len(steps) + 1,
                    ),
                    "raw_line": line_number,
                }
                (owner["observations"] if owner else unmatched).append(record)
            if not tool_uses and text:
                messages.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "source": event.get("type"),
                        "text": text,
                        "raw_line": line_number,
                    }
                )
            if event.get("type") == "result":
                step = {
                    "index": len(steps) + 1,
                    "attempt": attempt,
                    "event_id": None,
                    "tool_call_id": None,
                    "timestamp": event.get("timestamp"),
                    "tool": "submit",
                    "summary": "Claude process returned its final result.",
                    "security_risk": None,
                    "reasoning": "",
                    "action": compact_payload(event),
                    "observations": [],
                    "raw_line": line_number,
                }
                steps.append(step)
        attempts.append(
            {
                "attempt": attempt,
                "source": raw_links[-1],
                "messages": messages,
                "steps": steps,
                "unmatched_events": unmatched,
            }
        )
    workspace_events = parse_workspace_events(run_dir)
    attach_workspace_events(attempts, workspace_events)
    return {
        "attempts": attempts,
        "behavior": derive_loop_stages(attempts),
        "raw_links": raw_links,
        "workspace_event_count": len(workspace_events),
    }


def result_metadata(run_dir: Path) -> dict[str, Any]:
    result = read_json(run_dir / "result.json", {}) or {}
    audit = read_json(run_dir / "trajectory_audit.json", {}) or {}
    return {
        "status": result.get("status") or "unknown",
        "error": result.get("error"),
        "started_at": result.get("started_at"),
        "ended_at": result.get("ended_at"),
        "duration_seconds": result.get("duration_seconds"),
        "attempt_count": result.get("attempt_count"),
        "development_trace": result.get("development_trace"),
        "versions_schema": (result.get("versions") or {}).get("schema"),
        "trajectory_audit": {
            key: audit.get(key)
            for key in (
                "event_count",
                "assistant_action_count",
                "observation_event_count",
                "categories",
                "risk_indicators",
                "experiment_contaminated",
                "eligible_for_controlled_experiment",
            )
            if key in audit
        },
    }


def copy_supporting_raw(run_dir: Path, sample: Sample, output: Path) -> list[str]:
    links = []
    candidates = [
        run_dir / "case.json",
        run_dir / "result.json",
        run_dir / "trajectory.json",
        run_dir / "trajectory_audit.json",
        run_dir / "generation-summary.json",
    ]
    candidates.extend(sorted((run_dir / "development").glob("*/development_timeline.jsonl")))
    candidates.extend(sorted((run_dir / "development").glob("*/browser.events.jsonl")))
    for source in candidates:
        if source.is_file():
            name = source.name
            if any(link.endswith("/" + name) for link in links):
                name = f"{source.parent.name}-{name}"
            links.append(relative_copy(source, output, Path("raw") / sample.slug / name))
    return links


def controller_status(sample: Sample) -> dict[str, Any]:
    if sample.framework == "claude_code":
        controller_path = CLAUDE_ROOT / "controller-claude-code-smoke.json"
        cancellation = None
    else:
        controller_path = OPENHANDS_ROOT.parents[2] / "controller-smoke.json"
        cancellation = read_json(OPENHANDS_ROOT.parents[2] / "cancelled.json", None)
    controller = read_json(controller_path, {}) or {}
    row = (controller.get("jobs") or {}).get(f"{sample.mode}:{sample.case_id}", {})
    return {
        "status": (
            "USER_CANCELLED"
            if cancellation
            else str(row.get("status") or "NOT_SUBMITTED")
        ),
        "job_id": row.get("job_id"),
        "retries": row.get("retries", 0),
        "last_checked_at_utc": row.get("last_checked_at_utc"),
        "note": (
            cancellation.get("reason")
            if cancellation
            else (
                "No Claude trajectory is displayed until a real raw event artifact exists."
                if sample.framework == "claude_code"
                else "No OpenHands trajectory is displayed until a real raw event artifact exists."
            )
        ),
    }


def build_run(sample: Sample, output: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    official_prompt, prototypes = copy_public_inputs(sample, output)
    run_dir = openhands_dir(sample) if sample.framework == "openhands" else claude_dir(sample)
    if sample.framework == "openhands":
        available = bool(list(run_dir.glob("openhands.events.attempt-*.jsonl")))
    else:
        available = bool(list(run_dir.glob("claude.events.attempt-*.jsonl")))
    manifest = {
        "id": sample.slug,
        "framework": sample.framework,
        "model": "Qwen3.5-9B",
        "mode": sample.mode,
        "case_id": sample.case_id,
        "level": sample.level,
        "reason": sample.reason,
        "availability": "available" if available else "pending",
        "status": result_metadata(run_dir).get("status") if available else (
            controller_status(sample).get("status")
        ),
        "data_path": f"data/{sample.slug}.json" if available else None,
        "pending": controller_status(sample) if not available else None,
    }
    if not available:
        return manifest, None
    parsed = (
        parse_openhands(sample, run_dir, output)
        if sample.framework == "openhands"
        else parse_claude(sample, run_dir, output)
    )
    parsed["raw_links"].extend(copy_supporting_raw(run_dir, sample, output))
    parsed["raw_links"] = sorted(set(parsed["raw_links"]))
    prompt_path = sorted((run_dir / "development").glob("*/prompt.json"))
    prompt_record = read_json(prompt_path[-1], {}) if prompt_path else {}
    data = {
        "schema": "multimodalcode-vision2web-trajectory-explorer-run-1",
        "id": sample.slug,
        "framework": sample.framework,
        "model": "Qwen3.5-9B",
        "mode": sample.mode,
        "case_id": sample.case_id,
        "level": sample.level,
        "sample_reason": sample.reason,
        "source_run_dir": str(run_dir),
        "task": {
            "official_prompt": official_prompt,
            "effective_prompt": prompt_record.get("prompt") or official_prompt,
            "prompt_sha256": prompt_record.get("sha256"),
            "official_prompt_sha256": prompt_record.get("official_prompt_sha256"),
            "prototypes": prototypes,
            "private_workflow_read": False,
        },
        "result": result_metadata(run_dir),
        **parsed,
    }
    manifest["summary"] = {
        "attempts": len(data["attempts"]),
        "actions": sum(len(attempt["steps"]) for attempt in data["attempts"]),
        "observations": sum(
            len(step["observations"])
            for attempt in data["attempts"]
            for step in attempt["steps"]
        ),
        **data["behavior"],
    }
    return manifest, data


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Vision2Web OpenHands 与 Claude Code 真实 action-observation 轨迹浏览器">
<title>Vision2Web · Agent Trajectory Explorer</title><link rel="stylesheet" href="style.css"></head>
<body><header class="topbar"><div><span class="kicker">MULTIMODALCODE / POST-TRAJECTORY AUDIT</span><h1>Vision2Web Agent 行为轨迹</h1><p>逐步查看模型 reasoning、真实 action、环境 observation、截图与 workspace 变化。阶段标签来自离线可视化推断，不是外部控制循环。</p></div><div id="global-stats" class="global-stats"></div></header>
<div class="layout"><aside><div class="filters"><input id="search" placeholder="搜索 case、tool、命令…"><div class="segmented" id="framework-filter"><button class="active" data-value="">全部</button><button data-value="openhands">OpenHands</button><button data-value="claude_code">Claude Code</button></div><select id="mode-filter"><option value="">全部模式</option><option value="official">official</option><option value="browser_enabled">browser_enabled</option><option value="guided_vsv">guided_vsv</option><option value="tools">tools (archived)</option><option value="self_verify">self_verify (archived)</option></select><select id="level-filter"><option value="">全部层级</option><option>Level 1</option><option>Level 2</option><option>Level 3</option></select></div><div id="run-list" class="run-list"></div></aside><main id="main"><section class="empty"><h2>选择左侧轨迹</h2><p>Available 表示已有真实 artifact；Queued/New 只显示调度状态，不生成模拟轨迹。</p></section></main></div>
<script src="app.js"></script></body></html>"""


STYLE_CSS = r"""
:root{--ink:#17202a;--muted:#667085;--line:#dde3ea;--paper:#f6f7f9;--card:#fff;--nav:#111827;--blue:#3157d5;--cyan:#0e7490;--green:#087a55;--amber:#b15c00;--red:#b42318;--violet:#7c3aed;--shadow:0 12px 34px rgba(17,24,39,.08)}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--paper);font:14px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.topbar{min-height:205px;padding:35px clamp(24px,5vw,76px);color:#fff;background:radial-gradient(circle at 85% 15%,#3857a8 0,transparent 34%),linear-gradient(128deg,#111827,#172554 56%,#0f3c4c);display:flex;justify-content:space-between;gap:34px;align-items:flex-end}.topbar h1{margin:6px 0 8px;font-size:clamp(30px,4vw,52px);letter-spacing:-.04em}.topbar p{max-width:850px;color:#dbe4ff;margin:0}.kicker{font:700 11px/1.2 ui-monospace,monospace;letter-spacing:.15em;color:#93c5fd}.global-stats{display:grid;grid-template-columns:repeat(2,minmax(88px,1fr));gap:8px}.stat{padding:11px 13px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);border-radius:10px}.stat b{display:block;font-size:22px}.stat span{font-size:11px;color:#cbd5e1}.layout{display:grid;grid-template-columns:355px minmax(0,1fr);max-width:1760px;margin:0 auto}aside{border-right:1px solid var(--line);min-height:calc(100vh - 205px);background:#eef1f5;padding:18px;position:relative}.filters{position:sticky;top:0;z-index:5;padding-bottom:14px;background:#eef1f5}.filters input,.filters select{width:100%;height:41px;border:1px solid #cfd6df;border-radius:9px;background:white;padding:0 11px;margin-bottom:8px}.segmented{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-bottom:8px}.segmented button{border:1px solid #cfd6df;background:#fff;padding:8px 4px;font-size:11px;border-radius:7px;cursor:pointer}.segmented button.active{background:var(--nav);color:#fff;border-color:var(--nav)}.run-list{display:flex;flex-direction:column;gap:9px}.run-item{width:100%;text-align:left;border:1px solid var(--line);border-radius:12px;background:#fff;padding:13px;cursor:pointer;box-shadow:0 2px 9px rgba(15,23,42,.035)}.run-item:hover,.run-item.active{border-color:#7691e8;box-shadow:0 5px 18px rgba(49,87,213,.12)}.run-item .row{display:flex;justify-content:space-between;gap:8px}.run-item h3{margin:4px 0;font-size:14px}.run-item p{margin:0;color:var(--muted);font-size:12px}.badge{display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:700;letter-spacing:.02em;background:#eef2f6;color:#475467}.badge.available,.badge.success{background:#e7f8f0;color:var(--green)}.badge.queued,.badge.queuing{background:#fff3df;color:var(--amber)}.badge.self_verify{background:#ede9fe;color:var(--violet)}.badge.official{background:#e8eefc;color:var(--blue)}main{padding:24px clamp(20px,3vw,48px) 70px;min-width:0}.empty,.panel{background:#fff;border:1px solid var(--line);border-radius:15px;box-shadow:var(--shadow)}.empty{padding:55px;text-align:center;color:var(--muted)}.run-head{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:18px}.run-head h2{font-size:30px;margin:5px 0 3px;letter-spacing:-.025em}.run-head p{margin:0;color:var(--muted)}.run-head .badges{display:flex;gap:6px;flex-wrap:wrap}.panel{padding:20px;margin:14px 0}.panel h3{margin:0 0 12px;font-size:18px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:8px}.metric{border:1px solid var(--line);background:#fafbfc;border-radius:10px;padding:11px}.metric b{display:block;font-size:19px}.metric span{font-size:11px;color:var(--muted)}.truth-note{padding:12px 14px;border-left:4px solid var(--amber);background:#fffbeb;color:#764900;border-radius:8px}.behavior{display:flex;gap:7px;flex-wrap:wrap}.behavior .yes{background:#e7f8f0;color:var(--green)}.behavior .no{background:#f2f4f7;color:#667085}.prototype-grid,.shot-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.prototype-grid figure,.shot-grid figure{margin:0;border:1px solid var(--line);border-radius:10px;background:#f8fafc;overflow:hidden}.prototype-grid img{width:100%;height:210px;object-fit:contain;background:#fff}.shot-grid img{width:100%;max-height:430px;object-fit:contain;background:#111827}.prototype-grid figcaption,.shot-grid figcaption{padding:7px 9px;color:var(--muted);font:11px ui-monospace,monospace}.prompt summary,.attempt summary,.reasoning summary,.observation summary,.workspace summary{cursor:pointer;font-weight:700}.prompt pre,.code,.observation pre,.reasoning pre,.workspace pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5edf7;border-radius:9px;padding:13px;max-height:560px;overflow:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}.loop-map{display:flex;gap:5px;flex-wrap:wrap}.loop-chip{border:0;border-radius:6px;padding:7px 9px;font:700 10px ui-monospace,monospace;cursor:pointer;background:#eef2f6;color:#344054}.loop-chip.implement{background:#e8eefc;color:var(--blue)}.loop-chip.deploy{background:#e0f2fe;color:#0369a1}.loop-chip.observe{background:#cffafe;color:var(--cyan)}.loop-chip.repair{background:#fee2e2;color:var(--red)}.loop-chip.recheck{background:#ede9fe;color:var(--violet)}.loop-chip.submit{background:#dcfce7;color:var(--green)}.trajectory-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}.trajectory-tools button,.trajectory-tools select{border:1px solid #cfd6df;background:#fff;border-radius:8px;padding:8px 10px;cursor:pointer}.attempt{border:1px solid var(--line);border-radius:12px;margin:12px 0;background:#fff}.attempt>summary{padding:12px 14px;background:#f8fafc;border-radius:12px}.attempt-body{padding:13px}.step{border:1px solid var(--line);border-left:5px solid #98a2b3;border-radius:10px;margin:10px 0;overflow:hidden;background:#fff}.step[data-stage=implement]{border-left-color:var(--blue)}.step[data-stage=deploy]{border-left-color:#0284c7}.step[data-stage=observe]{border-left-color:var(--cyan)}.step[data-stage=repair]{border-left-color:var(--red)}.step[data-stage=recheck]{border-left-color:var(--violet)}.step[data-stage=submit]{border-left-color:var(--green)}.step-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 12px;background:#fafbfc;border-bottom:1px solid var(--line)}.step-head .num{font:700 11px ui-monospace,monospace;color:var(--muted)}.step-head .tool{font:700 12px ui-monospace,monospace;color:var(--blue)}.step-head time{margin-left:auto;color:var(--muted);font-size:11px}.step-body{padding:12px}.action-title{display:flex;justify-content:space-between;gap:10px;margin-bottom:7px}.action-title strong{font-size:13px}.observation{border-top:1px dashed #cfd6df;padding-top:10px;margin-top:10px}.observation.error{border-left:3px solid var(--red);padding-left:10px}.workspace{margin-top:10px;background:#f8fafc;border-radius:8px;padding:9px}.raw-links{display:flex;gap:7px;flex-wrap:wrap}.raw-links a{color:var(--blue);font:11px ui-monospace,monospace}.pending{padding:35px}.pending h2{margin-top:0}.pending-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}.pending-grid div{padding:12px;border:1px solid var(--line);border-radius:9px;background:#fafbfc}.muted{color:var(--muted)}body.loading{cursor:progress}@media(max-width:950px){.topbar{display:block}.global-stats{margin-top:18px}.layout{grid-template-columns:1fr}aside{min-height:0;border-right:0;border-bottom:1px solid var(--line)}.filters{position:static}.run-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr))}.run-head{display:block}}@media(max-width:560px){.topbar{padding:24px 18px}aside,main{padding:14px}.prototype-grid{grid-template-columns:1fr}.step-head time{width:100%;margin-left:0}}
"""


APP_JS = r"""
const state={manifest:null,selected:null,framework:'',mode:'',level:'',search:'',tool:''};
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>n==null?'—':Number(n).toLocaleString();
const boolBadge=(label,value)=>`<span class="badge ${value?'yes':'no'}">${esc(label)} · ${value?'YES':'NO'}</span>`;
function renderStats(){const m=state.manifest;$('#global-stats').innerHTML=`<div class="stat"><b>${m.available_count}</b><span>真实轨迹</span></div><div class="stat"><b>${m.pending_count}</b><span>等待任务</span></div><div class="stat"><b>${m.openhands_count}</b><span>OpenHands 样本</span></div><div class="stat"><b>${m.claude_available_count}</b><span>Claude 实跑</span></div>`}
function filtered(){return state.manifest.runs.filter(r=>(!state.framework||r.framework===state.framework)&&(!state.mode||r.mode===state.mode)&&(!state.level||r.level===state.level)&&(!state.search||`${r.framework} ${r.mode} ${r.case_id} ${r.reason} ${r.status}`.toLowerCase().includes(state.search)))}
function renderList(){const rows=filtered();$('#run-list').innerHTML=rows.map(r=>`<button class="run-item ${state.selected===r.id?'active':''}" data-id="${esc(r.id)}"><div class="row"><span class="badge">${esc(r.framework==='openhands'?'OpenHands':'Claude Code')}</span><span class="badge ${esc(String(r.status).toLowerCase())}">${esc(r.status)}</span></div><h3>${esc(r.case_id)}</h3><p>${esc(r.level)} · <span class="badge ${esc(r.mode)}">${esc(r.mode)}</span></p><p>${esc(r.reason)}</p></button>`).join('')||'<p class="muted">无匹配轨迹</p>';document.querySelectorAll('.run-item').forEach(x=>x.onclick=()=>selectRun(x.dataset.id))}
function pendingPage(r){return `<section class="panel pending"><span class="badge ${esc(String(r.status).toLowerCase())}">${esc(r.status)}</span><h2>${esc(r.framework==='claude_code'?'Claude Code':'OpenHands')} · ${esc(r.case_id)}</h2><p>${esc(r.reason)}</p><div class="truth-note">这里没有模型轨迹：当前只存在调度状态，网站不会生成或模拟 action。</div><div class="pending-grid"><div><b>mode</b><br>${esc(r.mode)}</div><div><b>job</b><br>${esc(r.pending?.job_id||'尚未提交')}</div><div><b>retries</b><br>${esc(r.pending?.retries??0)}</div><div><b>last check</b><br>${esc(r.pending?.last_checked_at_utc||'—')}</div></div></section>`}
function metrics(d){const s=state.manifest.runs.find(r=>r.id===d.id).summary||{};return `<div class="metric-grid"><div class="metric"><b>${fmt(s.attempts)}</b><span>attempts</span></div><div class="metric"><b>${fmt(s.actions)}</b><span>agent actions</span></div><div class="metric"><b>${fmt(s.observations)}</b><span>observations</span></div><div class="metric"><b>${fmt(d.workspace_event_count)}</b><span>workspace events</span></div><div class="metric"><b>${fmt(Math.round(d.result.duration_seconds||0))}s</b><span>wall time</span></div></div>`}
function behavior(d){const b=d.behavior;return `<div class="behavior">${boolBadge('启动服务请求',b.service_start_requested)}${boolBadge('观察已部署页面',b.deployed_app_visual_observed)}${boolBadge('生成交互动作',b.task_interaction_authored)}${boolBadge('视觉证据后修改',b.post_visual_edit)}${boolBadge('修改后复查',b.post_edit_recheck)}${boolBadge('显式提交 action',b.normal_submit_action)}</div>`}
function prototypes(d){return d.task.prototypes.map(x=>`<figure><a href="${esc(x.path)}" target="_blank"><img loading="lazy" src="${esc(x.path)}"></a><figcaption>${esc(x.label)} · ${esc(x.sha256.slice(0,12))}</figcaption></figure>`).join('')}
function rawLinks(d){return d.raw_links.map(x=>`<a href="${esc(x)}" target="_blank">${esc(x.split('/').pop())}</a>`).join('')}
function loopMap(d){let n=0;return d.attempts.flatMap(a=>a.steps.map(s=>{n++;return `<button class="loop-chip ${esc(s.inferred_stage)}" data-target="${esc(d.id)}-${a.attempt}-${s.index}">${n} · ${esc(s.inferred_stage)}</button>`})).join('')}
function observation(o){const shots=(o.images||[]).map(x=>`<figure><a href="${esc(x.path)}" target="_blank"><img loading="lazy" src="${esc(x.path)}"></a><figcaption>${esc(x.sha256.slice(0,16))} · ${fmt(x.bytes)} bytes</figcaption></figure>`).join('');return `<details class="observation ${o.is_error?'error':''}" open><summary>Observation · ${esc(o.kind)} ${o.is_error?'· ERROR':''}</summary>${o.text?`<pre>${esc(o.text)}</pre>`:''}${shots?`<div class="shot-grid">${shots}</div>`:''}<details><summary>结构化 metadata</summary><pre>${esc(JSON.stringify(o.details||{},null,2))}</pre></details></details>`}
function workspace(events){if(!events?.length)return '';return `<details class="workspace"><summary>被动记录的 workspace / deploy 变化 · ${events.length}</summary><pre>${esc(JSON.stringify(events,null,2))}</pre></details>`}
function step(d,a,s){const reasoning=s.reasoning?`<details class="reasoning" open><summary>Model reasoning</summary><pre>${esc(s.reasoning)}</pre></details>`:'';const obs=(s.observations||[]).map(observation).join('')||'<p class="muted">没有与该 action 配对的 observation。</p>';return `<article class="step" id="${esc(d.id)}-${a.attempt}-${s.index}" data-tool="${esc(String(s.tool||'').toLowerCase())}" data-stage="${esc(s.inferred_stage)}"><div class="step-head"><span class="num">A${a.attempt} · #${s.index}</span><span class="tool">${esc(s.tool||'unknown')}</span>${(s.phases||[]).map(p=>`<span class="badge">${esc(p)}</span>`).join('')}<time>${esc(s.timestamp||'')}</time></div><div class="step-body">${reasoning}<div class="action-title"><strong>Action</strong><span class="muted">${esc(s.summary||'')}</span></div><pre class="code">${esc(JSON.stringify(s.action||{},null,2))}</pre>${obs}${workspace(s.workspace_events)}</div></article>`}
function attempt(d,a){const messages=a.messages?.length?`<details><summary>Attempt messages · ${a.messages.length}</summary><pre class="code">${esc(a.messages.map(x=>`[${x.source}] ${x.text}`).join('\n\n'))}</pre></details>`:'';return `<details class="attempt" open><summary>Attempt ${a.attempt} · ${a.steps.length} actions · source ${esc(a.source.split('/').pop())}</summary><div class="attempt-body">${messages}${a.steps.map(s=>step(d,a,s)).join('')}${a.unmatched_events?.length?`<details><summary>Unmatched/system events · ${a.unmatched_events.length}</summary><pre class="code">${esc(JSON.stringify(a.unmatched_events,null,2))}</pre></details>`:''}</div></details>`}
function applyToolFilter(){document.querySelectorAll('.step').forEach(x=>x.hidden=!!state.tool&&!x.dataset.tool.includes(state.tool))}
function page(d){return `<div class="run-head"><div><div class="badges"><span class="badge available">REAL ARTIFACT</span><span class="badge">${esc(d.framework==='openhands'?'OpenHands':'Claude Code')}</span><span class="badge ${esc(d.mode)}">${esc(d.mode)}</span><span class="badge">${esc(d.level)}</span></div><h2>${esc(d.case_id)}</h2><p>${esc(d.sample_reason)}</p></div></div><section class="panel"><h3>运行概览</h3>${metrics(d)}<br>${behavior(d)}<p class="truth-note">工具存在、工具执行成功、模型主动调用是三个不同结论。以下 action/observation 来自真实轨迹；彩色阶段仅为离线导航标签。</p></section><section class="panel"><h3>任务输入</h3><div class="prototype-grid">${prototypes(d)}</div><details class="prompt"><summary>完整 effective prompt</summary><pre>${esc(d.task.effective_prompt)}</pre></details></section><section class="panel"><h3>Loop map</h3><div class="loop-map">${loopMap(d)}</div></section><section class="panel"><h3>完整 action → observation 过程</h3><div class="trajectory-tools"><button id="expand-all">全部展开</button><button id="collapse-all">收起 attempt</button><select id="tool-filter"><option value="">全部 tools</option>${[...new Set(d.attempts.flatMap(a=>a.steps.map(s=>String(s.tool||'unknown'))))].sort().map(t=>`<option value="${esc(t.toLowerCase())}">${esc(t)}</option>`).join('')}</select></div>${d.attempts.map(a=>attempt(d,a)).join('')}</section><section class="panel"><h3>原始证据</h3><div class="raw-links">${rawLinks(d)}</div><p class="muted">原始 JSONL 是权威记录；网页 JSON 只移除内联 base64，并将截图解码为独立文件。</p></section>`}
async function selectRun(id){state.selected=id;renderList();const r=state.manifest.runs.find(x=>x.id===id);if(!r)return;history.replaceState(null,'',`#${encodeURIComponent(id)}`);if(r.availability!=='available'){ $('#main').innerHTML=pendingPage(r);return }document.body.classList.add('loading');try{const d=await fetch(r.data_path).then(x=>{if(!x.ok)throw Error(`${x.status} ${x.statusText}`);return x.json()});$('#main').innerHTML=page(d);document.querySelectorAll('.loop-chip').forEach(x=>x.onclick=()=>document.getElementById(x.dataset.target)?.scrollIntoView({behavior:'smooth',block:'center'}));$('#expand-all').onclick=()=>document.querySelectorAll('.attempt,.reasoning,.observation,.workspace').forEach(x=>x.open=true);$('#collapse-all').onclick=()=>document.querySelectorAll('.attempt').forEach(x=>x.open=false);$('#tool-filter').oninput=e=>{state.tool=e.target.value;applyToolFilter()}}catch(e){$('#main').innerHTML=`<section class="panel"><h2>加载失败</h2><pre>${esc(e.stack||e)}</pre></section>`}finally{document.body.classList.remove('loading')}}
async function init(){state.manifest=await fetch('data/manifest.json').then(r=>r.json());renderStats();renderList();$('#search').oninput=e=>{state.search=e.target.value.toLowerCase();renderList()};$('#mode-filter').oninput=e=>{state.mode=e.target.value;renderList()};$('#level-filter').oninput=e=>{state.level=e.target.value;renderList()};document.querySelectorAll('#framework-filter button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#framework-filter button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.framework=b.dataset.value;renderList()});const requested=decodeURIComponent(location.hash.slice(1));const first=state.manifest.runs.find(r=>r.id===requested)||state.manifest.runs.find(r=>r.availability==='available')||state.manifest.runs[0];if(first)selectRun(first.id)}
init().catch(e=>{$('#main').innerHTML=`<section class="panel"><h2>Manifest 加载失败</h2><pre>${esc(e.stack||e)}</pre></section>`});
"""


def main() -> int:
    global OPENHANDS_ROOT, CLAUDE_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports/vision2web_trajectory_explorer",
    )
    parser.add_argument(
        "--openhands-root",
        type=Path,
        default=OPENHANDS_ROOT,
        help=(
            "Directory containing canonical official/browser_enabled/guided_vsv "
            "runs or archived tools/self_verify runs."
        ),
    )
    parser.add_argument(
        "--claude-run-root",
        type=Path,
        default=CLAUDE_ROOT,
        help="Claude Code run root containing agents/ and its controller file.",
    )
    args = parser.parse_args()
    OPENHANDS_ROOT = args.openhands_root.resolve()
    CLAUDE_ROOT = args.claude_run_root.resolve()
    output = args.output.resolve()
    (output / "data").mkdir(parents=True, exist_ok=True)
    manifests = []
    for sample in SAMPLES:
        manifest, data = build_run(sample, output)
        manifests.append(manifest)
        if data is not None:
            (output / "data" / f"{sample.slug}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    available = [row for row in manifests if row["availability"] == "available"]
    document = {
        "schema": "multimodalcode-vision2web-trajectory-explorer-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "post_trajectory_only": True,
        "workflow_json_read": False,
        "stage_labels_are_offline_heuristics": True,
        "available_count": len(available),
        "pending_count": len(manifests) - len(available),
        "openhands_count": sum(row["framework"] == "openhands" and row["availability"] == "available" for row in manifests),
        "claude_available_count": sum(row["framework"] == "claude_code" and row["availability"] == "available" for row in manifests),
        "runs": manifests,
    }
    (output / "data/manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (output / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (output / "app.js").write_text(APP_JS, encoding="utf-8")
    (output / "README.md").write_text(
        "# Vision2Web trajectory explorer\n\n"
        "Generated from preserved post-trajectory artifacts. It never reads workflow.json or an evaluator.\n\n"
        "```bash\n"
        "PYTHONPATH=src python scripts/vision2web/build_trajectory_website.py\n"
        "python -m http.server 8095 --directory reports/vision2web_trajectory_explorer\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps({"index": str(output / "index.html"), **document}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

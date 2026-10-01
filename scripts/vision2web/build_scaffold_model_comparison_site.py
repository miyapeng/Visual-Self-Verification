#!/usr/bin/env python3
"""Build a focused six-way Vision2Web trajectory comparison website.

The primary timeline is reconstructed only from the native OpenHands JSONL or
Claude Code stream-json.  Images are rendered only under the exact observation
or tool result whose model-facing content contained the image payload.
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = (
    PROJECT_ROOT
    / "runs/vision2web_scaffold_comparison/smartrecruiters-scaffold-model-sixway-v1"
)
RUN_ROOT = DEFAULT_RUN_ROOT
CASE_ID = "frontend/smartrecruiters"
MODE = "guided_vsv"


@dataclass(frozen=True)
class RunSpec:
    framework: str
    model: str
    backend: str
    endpoint_vision: bool

    @property
    def slug(self) -> str:
        return safe(f"{self.model}-{self.framework}")

    @property
    def run_dir(self) -> Path:
        model_dir = self.model if self.framework == "claude_code" else f"litellm_proxy__{self.model}"
        root = RUN_ROOT / "agents" / model_dir / "vision2web"
        if self.framework == "claude_code":
            root /= "claude_code"
        return root / MODE / safe(CASE_ID)


RUNS = (
    RunSpec("openhands", "Qwen3.8-27B", "local vLLM", True),
    RunSpec("claude_code", "Qwen3.8-27B", "local vLLM", True),
    RunSpec("openhands", "claude-opus-4-8", "relay API", True),
    RunSpec("claude_code", "claude-opus-4-8", "relay API", True),
    RunSpec("openhands", "glm-5.2", "relay API", False),
    RunSpec("claude_code", "glm-5.2", "relay API", False),
)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def json_lines(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        return
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield line_number, value


def blocks_text(value: Any, kinds: set[str] | None = None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = value.get("content")
    if not isinstance(value, list):
        return ""
    result = []
    for block in value:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kinds is not None and kind not in kinds:
            continue
        text = block.get("text") if kind != "thinking" else block.get("thinking")
        if isinstance(text, str) and text:
            result.append(text)
    return "\n".join(result)


def compact(value: Any) -> Any:
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"screenshot_data", "data"} and isinstance(item, str) and len(item) > 1024:
            result[key] = f"<model-context binary omitted: {len(item)} base64 chars>"
        elif key == "signature":
            result[key] = "<signature omitted>"
        else:
            result[key] = compact(item)
    return result


def save_image(encoded: str, media_type: str | None, output: Path) -> dict[str, Any] | None:
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        media_type = header[5:].split(";", 1)[0] or media_type
    try:
        payload = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        return None
    if not payload:
        return None
    digest = hashlib.sha256(payload).hexdigest()
    extension = ".jpg" if payload[:3] == b"\xff\xd8\xff" or media_type == "image/jpeg" else ".png"
    target = output / "assets/model_context" / f"{digest}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)
    return {
        "path": target.relative_to(output).as_posix(),
        "sha256": digest,
        "bytes": len(payload),
        "media_type": media_type or ("image/jpeg" if extension == ".jpg" else "image/png"),
    }


def extract_images(value: Any, output: Path) -> list[dict[str, Any]]:
    images = []
    if isinstance(value, dict):
        screenshot = value.get("screenshot_data")
        if isinstance(screenshot, str) and screenshot:
            image = save_image(screenshot, "image/png", output)
            if image:
                images.append(image)
        value = value.get("content")
    if not isinstance(value, list):
        return images
    for block in value:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else block
        encoded_values: list[str] = []
        encoded = source.get("data") or source.get("source") or source.get("image_url")
        if isinstance(encoded, str):
            encoded_values.append(encoded)
        if isinstance(source.get("image_urls"), list):
            encoded_values.extend(
                item for item in source["image_urls"] if isinstance(item, str)
            )
        for encoded_value in encoded_values:
            image = save_image(encoded_value, source.get("media_type"), output)
            if image:
                images.append(image)
    return images


def classify(tool: str, payload: Any) -> str:
    name = tool.casefold()
    command = ""
    if isinstance(payload, dict):
        command = str(payload.get("command") or payload.get("cmd") or "")
    if name in {"edit", "write", "multiedit", "notebookedit", "file_editor"}:
        if name != "file_editor" or str(payload.get("command", "")).casefold() != "view":
            return "edit"
    if re.search(r"(?:^|\s)(?:bash\s+)?(?:\./)?start\.sh\b|npm\s+run\s+(?:dev|start)", command):
        return "deploy"
    if name.startswith("browser_") or re.search(
        r"(?:^|[;&|]\s*|\n\s*)(?:[^\s;&|]*/)?playwright-cli(?:\s|$)",
        command,
        re.I,
    ):
        return "browser"
    if name in {"finish", "submit"}:
        return "submit"
    if name in {"read", "view_image"} and re.search(r"\.(?:png|jpe?g|webp)\b", str(payload), re.I):
        return "view-image"
    return "inspect"


def copy_raw(path: Path, spec: RunSpec, output: Path) -> str:
    target = output / "raw" / spec.slug / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target.relative_to(output).as_posix()


def parse_openhands(spec: RunSpec, output: Path) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = sorted(spec.run_dir.glob("openhands.events.attempt-*.jsonl"))
    if not candidates and (spec.run_dir / "openhands.events.jsonl").is_file():
        candidates = [spec.run_dir / "openhands.events.jsonl"]
    timeline: list[dict[str, Any]] = []
    raw_links = []
    for attempt, path in enumerate(candidates, start=1):
        raw_links.append(copy_raw(path, spec, output))
        current_url = ""
        for line_number, event in json_lines(path):
            kind = str(event.get("kind") or "")
            base = {
                "attempt": attempt,
                "raw_line": line_number,
                "timestamp": event.get("timestamp"),
                "ordinal": len(timeline),
            }
            if kind == "MessageEvent":
                text = blocks_text((event.get("llm_message") or {}).get("content"))
                if text:
                    timeline.append(
                        {
                            **base,
                            "kind": "model_text" if event.get("source") == "agent" else "message",
                            "role": event.get("source"),
                            "text": text,
                        }
                    )
            elif kind == "ActionEvent":
                action = event.get("action") if isinstance(event.get("action"), dict) else {}
                reasoning = blocks_text(event.get("thought"))
                if isinstance(event.get("reasoning_content"), str):
                    reasoning = "\n".join(x for x in (reasoning, event["reasoning_content"]) if x)
                tool = str(event.get("tool_name") or (event.get("tool_call") or {}).get("name") or "unknown")
                if tool in {"browser_navigate", "browser_open"} and action.get("url"):
                    current_url = str(action["url"])
                timeline.append(
                    {
                        **base,
                        "kind": "action",
                        "tool": tool,
                        "category": classify(tool, action),
                        "reasoning": reasoning,
                        "text": str(event.get("summary") or ""),
                        "payload": compact(action),
                        "tool_call_id": event.get("tool_call_id"),
                        "browser_url_context": current_url or None,
                    }
                )
            elif kind in {"ObservationEvent", "AgentErrorEvent"}:
                observation = event.get("observation") if isinstance(event.get("observation"), dict) else {}
                tool = str(event.get("tool_name") or "")
                timeline.append(
                    {
                        **base,
                        "kind": "observation",
                        "tool": tool or None,
                        "category": classify(tool, observation),
                        "is_error": bool(observation.get("is_error") or kind == "AgentErrorEvent"),
                        "text": blocks_text(observation.get("content")) or str(event.get("message") or ""),
                        "payload": compact({k: v for k, v in observation.items() if k not in {"content", "screenshot_data"}}),
                        "images": extract_images(observation, output),
                        "image_provenance": "OpenHands model-facing ObservationEvent",
                        "action_id": event.get("action_id"),
                        "browser_url_context": current_url or None,
                    }
                )
    return timeline, raw_links


def claude_capture_times(run_dir: Path, attempt: int) -> dict[int, str]:
    candidates = sorted(run_dir.glob(f"claude.capture*attempt-{attempt}.jsonl"))
    if not candidates:
        return {}
    result = {}
    for _, event in json_lines(candidates[-1]):
        if event.get("stream") == "stdout" and isinstance(event.get("sequence"), int):
            result[event["sequence"] + 1] = str(event.get("timestamp") or "")
    return result


def claude_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else event.get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def parse_claude(spec: RunSpec, output: Path) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = sorted(spec.run_dir.glob("claude.events.attempt-*.jsonl"))
    if not candidates and (spec.run_dir / "claude.events.jsonl").is_file():
        candidates = [spec.run_dir / "claude.events.jsonl"]
    timeline: list[dict[str, Any]] = []
    raw_links = []
    for attempt, path in enumerate(candidates, start=1):
        raw_links.append(copy_raw(path, spec, output))
        capture_times = claude_capture_times(spec.run_dir, attempt)
        tools: dict[str, str] = {}
        tool_inputs: dict[str, dict[str, Any]] = {}
        current_url = ""
        for line_number, event in json_lines(path):
            timestamp = str(event.get("timestamp") or capture_times.get(line_number) or "")
            for block_index, block in enumerate(claude_blocks(event)):
                block_type = str(block.get("type") or "")
                base = {
                    "attempt": attempt,
                    "raw_line": line_number,
                    "block_index": block_index,
                    "timestamp": timestamp,
                    "ordinal": len(timeline),
                    "scope": (
                        "nested_subagent"
                        if event.get("parent_tool_use_id")
                        else "main_session"
                    ),
                    "parent_tool_use_id": event.get("parent_tool_use_id"),
                }
                if block_type == "thinking":
                    text = str(block.get("thinking") or "")
                    if text:
                        timeline.append({**base, "kind": "reasoning", "text": text})
                elif block_type == "text":
                    text = str(block.get("text") or "")
                    if text:
                        timeline.append({**base, "kind": "model_text", "text": text})
                elif block_type == "tool_use":
                    tool_id = str(block.get("id") or "")
                    tool = str(block.get("name") or "unknown")
                    payload = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if tool_id:
                        tools[tool_id] = tool
                        tool_inputs[tool_id] = payload
                    command = str(payload.get("command") or "")
                    match = re.search(
                        r"(?:^|\s)playwright-cli\s+(?:open|goto)\s+([^\s;&|]+)",
                        command,
                        re.I,
                    )
                    if match:
                        current_url = match.group(1).strip("'\"")
                    timeline.append(
                        {
                            **base,
                            "kind": "action",
                            "tool": tool,
                            "category": classify(tool, payload),
                            "payload": compact(payload),
                            "tool_call_id": tool_id,
                            "browser_url_context": current_url or None,
                        }
                    )
                elif block_type == "tool_result":
                    tool_id = str(block.get("tool_use_id") or "")
                    content = block.get("content")
                    source_input = tool_inputs.get(tool_id, {})
                    tool = tools.get(tool_id)
                    timeline.append(
                        {
                            **base,
                            "kind": "observation",
                            "tool": tool,
                            "category": classify(str(tool or ""), source_input),
                            "is_error": bool(block.get("is_error")),
                            "text": blocks_text(content) if isinstance(content, list) else str(content or ""),
                            "payload": {"tool_use_id": tool_id},
                            "source_action_payload": compact(source_input),
                            "images": extract_images(content, output),
                            "image_provenance": (
                                "Claude Code model-facing tool_result"
                                + (
                                    " (nested subagent context)"
                                    if event.get("parent_tool_use_id")
                                    else " (main session)"
                                )
                            ),
                            "browser_url_context": current_url or None,
                        }
                    )
            if event.get("type") == "result":
                timeline.append(
                    {
                        "attempt": attempt,
                        "raw_line": line_number,
                        "timestamp": timestamp,
                        "ordinal": len(timeline),
                        "kind": "session_result",
                        "text": str(event.get("result") or ""),
                        "payload": compact({k: v for k, v in event.items() if k not in {"result"}}),
                    }
                )
    return timeline, raw_links


def development_record(spec: RunSpec) -> dict[str, Any]:
    roots = sorted((spec.run_dir / "development").glob("*"))
    if not roots:
        return {}
    root = roots[-1]
    events = []
    for _, event in json_lines(root / "workspace.events.jsonl"):
        kind = event.get("type")
        if kind in {
            "workspace_change",
            "deployment_ready",
            "first_application_observation_version",
            "final_submission_version",
        }:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            events.append(
                {
                    "timestamp": event.get("timestamp"),
                    "type": kind,
                    "payload": compact(payload),
                }
            )
    prompt = read_json(root / "prompt.json", {})
    browser_context = read_json(root / "claude_browser_context.json", {})
    return {
        "root": str(root),
        "events": events,
        "prompt": prompt,
        "claude_browser_context": browser_context,
    }


def summarize(timeline: list[dict[str, Any]], endpoint_vision: bool) -> dict[str, Any]:
    actions = [event for event in timeline if event["kind"] == "action"]
    image_events = [event for event in timeline if event["kind"] == "observation" and event.get("images")]
    def is_app_url(event: dict[str, Any]) -> bool:
        return bool(
            re.match(
                r"https?://(?:localhost|127\.0\.0\.1):3000(?:/|$)",
                str(event.get("browser_url_context") or ""),
                re.I,
            )
        )

    def is_app_image(event: dict[str, Any]) -> bool:
        if not is_app_url(event):
            return False
        source = event.get("source_action_payload")
        if isinstance(source, dict):
            path = str(source.get("file_path") or source.get("path") or "")
            if re.search(r"(?:^|/)(?:prototypes?|proto_split|crops?)(?:/|_|$)", path, re.I):
                return False
        return True

    app_image_events = [event for event in image_events if is_app_image(event)]
    browser_indices = [
        i
        for i, event in enumerate(timeline)
        if event.get("kind") == "action" and event.get("category") == "browser"
    ]
    app_browser_indices = [
        i
        for i in browser_indices
        if is_app_url(timeline[i])
    ]
    edit_indices = [i for i, event in enumerate(timeline) if event.get("category") == "edit"]
    post_browser_edits = [i for i in edit_indices if app_browser_indices and i > app_browser_indices[0]]
    rechecks = [i for i in app_browser_indices if post_browser_edits and i > post_browser_edits[0]]
    return {
        "events": len(timeline),
        "actions": len(actions),
        "main_session_actions": sum(
            1 for event in actions if event.get("scope", "main_session") == "main_session"
        ),
        "nested_subagent_actions": sum(
            1 for event in actions if event.get("scope") == "nested_subagent"
        ),
        "browser_actions": len(browser_indices),
        "deployed_app_browser_actions": len(app_browser_indices),
        "model_context_image_events": len(image_events),
        "deployed_app_image_events": len(app_image_events),
        "endpoint_vision_probe_passed": endpoint_vision,
        "edit_after_deployed_app_browser": bool(post_browser_edits),
        "deployed_app_recheck_after_edit": bool(rechecks),
        "tool_counts": {
            tool: sum(1 for action in actions if action.get("tool") == tool)
            for tool in sorted({str(action.get("tool")) for action in actions})
        },
    }


def checkpoint_audit(
    timeline: list[dict[str, Any]], development: dict[str, Any]
) -> dict[str, Any]:
    """Compare the recorded P_first boundary with native model-facing evidence."""

    first_observation = next(
        (
            event
            for event in timeline
            if event.get("kind") == "observation"
            and event.get("category") == "browser"
            and re.match(
                r"https?://(?:localhost|127\.0\.0\.1):3000(?:/|$)",
                str(event.get("browser_url_context") or ""),
                re.I,
            )
            and not event.get("is_error")
        ),
        None,
    )
    checkpoint = next(
        (
            event
            for event in development.get("events", [])
            if event.get("type") == "first_application_observation_version"
        ),
        None,
    )
    first_timestamp = str((first_observation or {}).get("timestamp") or "")
    checkpoint_timestamp = str((checkpoint or {}).get("timestamp") or "")
    edits_between = [
        event
        for event in timeline
        if event.get("kind") == "action"
        and event.get("category") == "edit"
        and first_timestamp
        and checkpoint_timestamp
        and first_timestamp < str(event.get("timestamp") or "") < checkpoint_timestamp
    ]
    return {
        "first_native_app_observation_timestamp": first_timestamp or None,
        "recorded_p_first_timestamp": checkpoint_timestamp or None,
        "recorded_p_first_is_late": bool(edits_between),
        "edits_between_native_observation_and_p_first": len(edits_between),
    }


def build_one(spec: RunSpec, output: Path) -> dict[str, Any]:
    parser = parse_openhands if spec.framework == "openhands" else parse_claude
    timeline, raw_links = parser(spec, output)
    result = read_json(spec.run_dir / "result.json", {})
    comparison = read_json(spec.run_dir / "comparison-summary.json", {})
    development = development_record(spec)
    development_root = Path(str(development.get("root") or ""))
    if development_root.is_dir():
        for filename in (
            "development_timeline.jsonl",
            "workspace.events.jsonl",
            "browser.events.jsonl",
        ):
            path = development_root / filename
            if path.is_file():
                raw_links.append(copy_raw(path, spec, output))
    status = comparison.get("result_status") or result.get("status")
    if not timeline:
        status = status or "pending"
    else:
        status = status or "running"
    data = {
        "schema": "vision2web-native-trajectory-comparison-1",
        "case_id": CASE_ID,
        "framework": spec.framework,
        "model": spec.model,
        "backend": spec.backend,
        "mode": MODE,
        "status": status,
        "endpoint_vision_probe_passed": spec.endpoint_vision,
        "timeline": timeline,
        "summary": summarize(timeline, spec.endpoint_vision),
        "checkpoint_audit": checkpoint_audit(timeline, development),
        "development": development,
        "result": compact(result),
        "raw_links": raw_links,
        "source_dir": str(spec.run_dir),
    }
    path = output / "data/runs" / f"{spec.slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "id": spec.slug,
        "framework": spec.framework,
        "model": spec.model,
        "backend": spec.backend,
        "status": status,
        "endpoint_vision_probe_passed": spec.endpoint_vision,
        "events": len(timeline),
        "data": path.relative_to(output).as_posix(),
    }


INDEX = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vision2Web · Native Agent Trajectories</title><link rel="stylesheet" href="style.css"></head><body><header><p class="eyebrow">__CASE_ID__ · __MODE__ · one case</p><h1>真实 OpenHands / Claude Code 轨迹对照</h1><p>3 个模型 × 2 个框架。时间轴只显示模型原始输出、真实工具调用及返回；图片仅出现在它进入模型上下文的 observation 位置。<a href="REPORT.md">实验摘要</a></p></header><nav id="runs"></nav><main id="main"><p>Loading…</p></main><script src="app.js"></script></body></html>"""


STYLE = """
:root{--ink:#172033;--muted:#667085;--line:#d9dee8;--paper:#f4f6f9;--blue:#3157d5;--purple:#7c3aed;--red:#b42318;--green:#087a55}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.55 Inter,ui-sans-serif,system-ui,sans-serif}header{padding:34px max(24px,calc((100vw - 1500px)/2));background:#111827;color:white}header h1{margin:4px 0 7px;font-size:32px}header p{max-width:900px;margin:0;color:#cbd5e1}.eyebrow{font:700 11px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:#93c5fd}nav{display:grid;grid-template-columns:repeat(6,minmax(145px,1fr));gap:8px;max-width:1500px;margin:18px auto;padding:0 20px}nav button{border:1px solid var(--line);background:white;border-radius:10px;padding:11px;text-align:left;cursor:pointer}nav button.active{border-color:var(--blue);box-shadow:0 0 0 2px #dbe5ff}nav b,nav span{display:block}nav span{color:var(--muted);font-size:11px}main{max-width:1500px;margin:auto;padding:0 20px 70px}.panel,.event{background:white;border:1px solid var(--line);border-radius:12px}.panel{padding:18px;margin:12px 0}.panel h2,.panel h3{margin:0 0 10px}.meta,.stats,.badges{display:flex;gap:7px;flex-wrap:wrap}.badge{padding:3px 8px;border-radius:999px;background:#edf0f5;font:700 10px ui-monospace,monospace}.badge.browser{background:#cffafe;color:#155e75}.badge.edit{background:#fee2e2;color:#991b1b}.badge.deploy{background:#dbeafe;color:#1e40af}.badge.view-image{background:#ede9fe;color:#5b21b6}.badge.submit{background:#dcfce7;color:#166534}.warning{border-left:4px solid #f59e0b;background:#fffbeb;padding:10px 12px;color:#7a4b00}.ok{border-left-color:#10b981;background:#ecfdf5;color:#065f46}.stat{min-width:130px;padding:9px 12px;border:1px solid var(--line);border-radius:9px}.stat b{display:block;font-size:20px}.stat span{font-size:11px;color:var(--muted)}details summary{cursor:pointer;font-weight:700}.prompt pre,pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5edf7;border-radius:8px;padding:12px;max-height:520px;overflow:auto;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.timeline{position:relative;margin-top:15px}.timeline:before{content:"";position:absolute;left:24px;top:0;bottom:0;width:2px;background:#dbe1ea}.event{position:relative;margin:10px 0 10px 50px;overflow:hidden}.event:before{content:"";position:absolute;left:-34px;top:19px;width:12px;height:12px;border-radius:50%;background:#98a2b3;border:3px solid var(--paper)}.event.action:before{background:var(--blue)}.event.reasoning:before,.event.model_text:before{background:var(--purple)}.event.observation:before{background:var(--green)}.event.error{border-color:#fda29b}.event-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:9px 12px;background:#f8fafc;border-bottom:1px solid var(--line)}.event-head time{margin-left:auto;color:var(--muted);font-size:10px}.event-body{padding:12px}.event-body>p{white-space:pre-wrap}.reasoning-box{border-left:4px solid var(--purple);background:#f5f3ff;padding:9px 11px;white-space:pre-wrap;margin-bottom:10px}.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}.images figure{margin:0;border:1px solid var(--line);border-radius:8px;overflow:hidden}.images img{width:100%;max-height:720px;object-fit:contain;background:#0f172a;display:block}.images figcaption{padding:8px;font:10px ui-monospace,monospace;color:var(--muted)}.model-image{background:#eefdf8;border:1px solid #a7f3d0;padding:8px;margin:10px 0}.model-image.warn{background:#fff7ed;border-color:#fdba74}.raw a{margin-right:10px}.muted{color:var(--muted)}@media(max-width:1050px){nav{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){nav{grid-template-columns:1fr 1fr}.event{margin-left:35px}.timeline:before{left:14px}.event:before{left:-27px}}
"""


STYLE += """
.filters{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}
.filters button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 11px;cursor:pointer}
.filters button.active{background:#172033;color:#fff;border-color:#172033}
.event[hidden]{display:none}
"""


APP = r"""
const S={manifest:null,current:null};const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function nav(){ $('#runs').innerHTML=S.manifest.runs.map(r=>`<button data-id="${esc(r.id)}" class="${r.id===S.current?'active':''}"><b>${esc(r.model)}</b><span>${r.framework==='openhands'?'OpenHands':'Claude Code'} · ${esc(r.status||'pending')} · ${r.events} events</span></button>`).join('');document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>load(b.dataset.id)); }
function badge(x){return `<span class="badge ${esc(x)}">${esc(x)}</span>`}
function images(e,d){if(!e.images?.length)return '';const note=d.endpoint_vision_probe_passed?`<div class="model-image"><b>图像在此进入模型上下文</b> · ${esc(e.image_provenance)}</div>`:`<div class="model-image warn"><b>CLI / scaffold 在此形成了图像块，但 GLM-5.2 relay 的视觉探针未通过；不能声称模型感知了像素。</b></div>`;return note+`<div class="images">${e.images.map(x=>`<figure><a target="_blank" href="${esc(x.path)}"><img loading="lazy" src="${esc(x.path)}"></a><figcaption>${esc(x.media_type)} · ${x.bytes.toLocaleString()} bytes · sha256 ${esc(x.sha256.slice(0,16))}</figcaption></figure>`).join('')}</div>`}
function event(e,d,i){const reasoning=e.reasoning?`<div class="reasoning-box"><b>Model-emitted reasoning</b>\n${esc(e.reasoning)}</div>`:'';const text=e.text?`<p>${esc(e.text)}</p>`:'';const payload=e.payload&&Object.keys(e.payload).length?`<details><summary>完整 ${e.kind==='action'?'action':'structured payload'}</summary><pre>${esc(JSON.stringify(e.payload,null,2))}</pre></details>`:'';const source=e.source_action_payload&&Object.keys(e.source_action_payload).length?`<details><summary>对应 tool_use 输入</summary><pre>${esc(JSON.stringify(e.source_action_payload,null,2))}</pre></details>`:'';const scope=e.scope==='nested_subagent'?badge('nested subagent'):'';const app=/^https?:\/\/(localhost|127\.0\.0\.1):3000(?:\/|$)/i.test(e.browser_url_context||'');const tags=[e.kind,e.category,e.images?.length?'images':'',app?'app':''].filter(Boolean).join(' ');return `<article data-tags="${esc(tags)}" class="event ${esc(e.kind)} ${e.is_error?'error':''}"><div class="event-head"><b>#${i+1} · ${esc(e.kind)}</b>${e.tool?`<code>${esc(e.tool)}</code>`:''}${e.category?badge(e.category):''}${app?badge('deployed app'):''}${scope}${e.is_error?badge('ERROR'):''}<span class="muted">attempt ${e.attempt} · raw line ${e.raw_line}</span><time>${esc(e.timestamp||'')}</time></div><div class="event-body">${reasoning}${text}${payload}${source}${images(e,d)}</div></article>`}
function bindFilters(){document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-filter]').forEach(x=>x.classList.remove('active'));b.classList.add('active');const f=b.dataset.filter;document.querySelectorAll('.timeline .event').forEach(e=>e.hidden=f!=='all'&&!e.dataset.tags.split(' ').includes(f))})}
function page(d){const s=d.summary;const vision=d.endpoint_vision_probe_passed?`<p class="warning ok">端点图像探针通过。下方仍只在原始 observation/tool_result 含图像时展示像素。</p>`:`<p class="warning">GLM-5.2 的 tool calling 可用，但 relay 图像探针失败。本轨迹可以研究工具选择与文本快照使用，不能作为视觉自验证成功证据。</p>`;const checkpoint=d.checkpoint_audit?.recorded_p_first_is_late?`<p class="warning"><b>历史 P_first 边界偏晚：</b>原始流在 ${esc(d.checkpoint_audit.first_native_app_observation_timestamp)} 已返回已部署页面状态，随后发生 ${d.checkpoint_audit.edits_between_native_observation_and_p_first} 次编辑；旧记录器到 ${esc(d.checkpoint_audit.recorded_p_first_timestamp)} 的 PNG Read 才保存 P_first。网页按原始流判断行为，不伪造旧 checkpoint；未来运行已修正。</p>`:'';const record=d.development?.prompt||{};const prompt=record.prompt||'';const promptMeta={...record};delete promptMeta.prompt;const interfaceText=d.framework==='openhands'?'OpenHands：原生代码/终端工具 + BrowserToolSet + browser_execute_plan。':'Claude Code：内置 Read/Edit/Write/Bash + 官方镜像里的 playwright-cli skill；没有另造浏览器栈。';return `<section class="panel"><div class="badges">${badge(d.model)}${badge(d.framework==='openhands'?'OpenHands':'Claude Code')}${badge(d.backend)}${badge(d.mode)}${badge(d.status)}</div><h2>${esc(d.case_id)}</h2>${vision}${checkpoint}<p><b>框架接口：</b>${esc(interfaceText)}</p><p class="muted">六条轨迹的用户 effective prompt SHA 相同；不同的是框架自身的 system/tool harness。框架未写入原始流的隐藏 system prompt 不会被杜撰。</p><div class="stats"><div class="stat"><b>${s.events}</b><span>timeline events</span></div><div class="stat"><b>${s.actions}</b><span>tool actions</span></div><div class="stat"><b>${s.nested_subagent_actions}</b><span>nested actions</span></div><div class="stat"><b>${s.browser_actions}</b><span>all browser actions</span></div><div class="stat"><b>${s.deployed_app_browser_actions}</b><span>app browser actions</span></div><div class="stat"><b>${s.model_context_image_events}</b><span>all image results</span></div><div class="stat"><b>${s.deployed_app_image_events}</b><span>app images injected</span></div><div class="stat"><b>${s.edit_after_deployed_app_browser?'YES':'NO'}</b><span>edit after app evidence</span></div><div class="stat"><b>${s.deployed_app_recheck_after_edit?'YES':'NO'}</b><span>app recheck after edit</span></div></div></section><section class="panel prompt"><details><summary>本次完整 effective prompt</summary><pre>${esc(prompt||'轨迹尚未创建 prompt artifact')}</pre></details><details><summary>prompt / browser-interface provenance</summary><pre>${esc(JSON.stringify(promptMeta,null,2))}</pre></details></section><section class="panel"><h3>从开始到结束的原始事件时间轴</h3><p class="muted">只展示模型实际发出的可记录 reasoning/text；未暴露的隐藏思维不会被推断或补写。浏览原型图不计为已部署应用检查；app 指向 localhost:3000。</p><div class="filters"><button class="active" data-filter="all">全部</button><button data-filter="action">Actions</button><button data-filter="browser">浏览器</button><button data-filter="app">已部署应用</button><button data-filter="edit">编辑</button><button data-filter="images">图像注入</button></div><div class="timeline">${d.timeline.length?d.timeline.map((e,i)=>event(e,d,i)).join(''):'<p>任务尚未产生原始事件。</p>'}</div></section><section class="panel raw"><h3>原始证据</h3>${d.raw_links.map(x=>`<a target="_blank" href="${esc(x)}">${esc(x.split('/').pop())}</a>`).join('')||'暂无'}<details><summary>workspace / checkpoint 被动记录</summary><pre>${esc(JSON.stringify(d.development?.events||[],null,2))}</pre></details></section>`}
async function load(id){S.current=id;nav();history.replaceState(null,'',`#${encodeURIComponent(id)}`);const row=S.manifest.runs.find(x=>x.id===id);const d=await fetch(row.data).then(r=>r.json());$('#main').innerHTML=page(d);bindFilters()}
fetch('data/manifest.json').then(r=>r.json()).then(m=>{S.manifest=m;const requested=decodeURIComponent(location.hash.slice(1));load(m.runs.some(x=>x.id===requested)?requested:m.runs[0].id)}).catch(e=>$('#main').innerHTML=`<pre>${esc(e.stack||e)}</pre>`);
"""


def main() -> int:
    global RUN_ROOT, CASE_ID, MODE, RUNS
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports/vision2web_scaffold_model_comparison",
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--case-id", default="frontend/smartrecruiters")
    parser.add_argument(
        "--mode",
        choices=(
            "official",
            "browser_enabled",
            "guided_vsv",
            "tools",
            "self_verify",
        ),
        default="guided_vsv",
    )
    parser.add_argument("--only-model", action="append", default=[])
    args = parser.parse_args()
    RUN_ROOT = args.run_root.resolve()
    CASE_ID = args.case_id
    MODE = args.mode
    if args.only_model:
        selected_models = set(args.only_model)
        RUNS = tuple(spec for spec in RUNS if spec.model in selected_models)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = [build_one(spec, output) for spec in RUNS]
    manifest = {
        "schema": "vision2web-native-trajectory-comparison-manifest-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": CASE_ID,
        "mode": MODE,
        "workflow_json_read": False,
        "official_evaluator_read": False,
        "runs": rows,
    }
    (output / "data").mkdir(exist_ok=True)
    (output / "data/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(
        INDEX.replace("__CASE_ID__", CASE_ID).replace("__MODE__", MODE),
        encoding="utf-8",
    )
    (output / "style.css").write_text(STYLE, encoding="utf-8")
    (output / "app.js").write_text(APP, encoding="utf-8")
    (output / "README.md").write_text(
        "# Vision2Web native scaffold/model trajectory comparison\n\n"
        "Generated only from native OpenHands and Claude Code event streams.\n\n"
        "See [REPORT.md](REPORT.md) for the evidence-backed summary.\n\n"
        "```bash\nPYTHONPATH=src python scripts/vision2web/build_scaffold_model_comparison_site.py\n"
        "python -m http.server 8097 --bind 127.0.0.1 "
        "--directory reports/vision2web_scaffold_model_comparison\n```\n",
        encoding="utf-8",
    )
    print(json.dumps({"index": str(output / "index.html"), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Native Claude Code and OpenHands parsing shared by trajectory tools."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

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
        payload = base64.b64decode("".join(encoded.split()), validate=True)
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
            else:
                images.append({'path': '', 'available': False, 'media_type': 'image/png',
                               'unavailable_reason': 'Recorded screenshot bytes absent or invalid'})
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
            else:
                # A recorded image input remains evidence even when the export
                # omits its bytes. Never turn a placeholder into a local image.
                images.append({"path": "", "available": False,
                               "source": encoded_value if encoded_value.startswith(('http://', 'https://')) else None,
                               "media_type": source.get("media_type"),
                               "unavailable_reason": "Image bytes absent or invalid in source export"})
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


def copy_raw(path: Path, spec: Any, output: Path) -> str:
    target = output / "raw" / spec.slug / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target.relative_to(output).as_posix()


def parse_openhands(spec: Any, output: Path) -> tuple[list[dict[str, Any]], list[str]]:
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
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def parse_claude(spec: Any, output: Path, *, source_paths=None) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = source_paths or sorted(spec.run_dir.glob("claude.events.attempt-*.jsonl"))
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
                        kind = "user_text" if event.get("type") == "user" else "model_text"
                        if kind == "user_text" and text.startswith("[Image:"):
                            kind = "tool_metadata"
                        timeline.append({**base, "kind": kind, "text": text})
                elif block_type == "image" and event.get('type') == 'user':
                    timeline.append({**base, 'kind': 'user_text', 'text': '',
                                     'images': extract_images([block], output)})
                elif block_type == "tool_use":
                    tool_id = str(block.get("id") or "")
                    tool = str(block.get("name") or "unknown")
                    payload = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if tool_id:
                        tools[tool_id] = tool
                        tool_inputs[tool_id] = payload
                    command = str(payload.get("command") or "")
                    navigations = re.findall(
                        r"(?:^|[\s;|&(])playwright-cli\s+(?:--[\w=-]+\s+)*(?:open|goto)\s+"
                        r"(?:--[\w=-]+\s+)*['\"]?(https?://[^\s'\";&|)]+)",
                        command,
                        re.I,
                    )
                    if navigations:
                        current_url = navigations[-1]
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

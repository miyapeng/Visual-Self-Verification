"""Normalize raw scaffold trajectories without discarding the originals."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            chunks.append(item.get("text", ""))
        elif item.get("type") == "image_url":
            url = item.get("image_url", {}).get("url", "")
            chunks.append(_image_marker(url))
    return "\n".join(chunk for chunk in chunks if chunk)


def _openhands_text(value: Any) -> str:
    """Extract useful event text without copying image payloads into the audit."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_openhands_text(item) for item in value)))
    if not isinstance(value, dict):
        return str(value)
    if value.get("type") == "text":
        return str(value.get("text", ""))
    if value.get("type") == "image_url":
        return _image_marker(value.get("image_url", {}).get("url", ""))
    image_urls = value.get("image_urls")
    if isinstance(image_urls, list):
        return "\n".join(_image_marker(str(url)) for url in image_urls)
    chunks = []
    for key in ("thought", "text", "command", "path", "content", "error", "detail", "output"):
        if key in value:
            text = _openhands_text(value[key])
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _image_marker(url: str) -> str:
    if not url.startswith("data:") or "," not in url:
        return f"[image:{url}]"
    header, encoded = url.split(",", 1)
    try:
        payload = base64.b64decode(encoded)
    except Exception:
        return "[image:invalid-data-url]"
    return f"[image:{header[5:].split(';', 1)[0]} sha256={hashlib.sha256(payload).hexdigest()} bytes={len(payload)}]"


def normalize_mini(raw_path: Path, output_path: Path, case: dict[str, Any]) -> dict[str, Any]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    events = []
    for sequence, message in enumerate(raw.get("messages", [])):
        extra = message.get("extra", {})
        actions = extra.get("actions", [])
        events.append(
            {
                "sequence": sequence,
                "actor": message.get("role", "unknown"),
                "kind": "action" if actions else message.get("role", "message"),
                "text": _text(message.get("content")),
                "tools": [action.get("command", "") for action in actions],
                "timestamp": extra.get("timestamp"),
                "exit_status": extra.get("exit_status"),
            }
        )
    normalized = {
        "schema": "multimodalcode-agent-trajectory-1",
        "benchmark": case["benchmark"],
        "case_id": case["case_id"],
        "scaffold": "mini-swe-agent",
        "events": events,
        "summary": raw.get("info", {}),
        "raw_trajectory": str(raw_path),
    }
    output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def normalize_openhands(raw_path: Path, output_path: Path, case: dict[str, Any]) -> dict[str, Any]:
    events = []
    if raw_path.is_file():
        for sequence, line in enumerate(raw_path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                # The headless CLI interleaves Rich terminal rendering with
                # JSON events. Keep that byte-for-byte in the raw artifact,
                # but do not misrepresent UI lines as agent trajectory events.
                continue
            source = item.get("source") or item.get("role") or item.get("actor") or "unknown"
            action = item.get("action")
            observation = item.get("observation")
            llm_message = item.get("llm_message") or {}
            content = (
                item.get("message")
                or item.get("content")
                or llm_message.get("content")
                or observation
                or action
                or item.get("thought")
                or item.get("error")
                or item.get("detail")
                or ""
            )
            tool_calls = llm_message.get("tool_calls") or []
            events.append(
                {
                    "sequence": sequence,
                    "actor": source,
                    "kind": item.get("kind") or item.get("type") or ("action" if action else "observation" if observation else "event"),
                    "text": _openhands_text(content),
                    "tool": item.get("tool_name") or item.get("tool") or item.get("action_name"),
                    "tools": [call.get("function", {}).get("name", "") for call in tool_calls if isinstance(call, dict)],
                    "error_code": item.get("code"),
                    "timestamp": item.get("timestamp"),
                }
            )
    normalized = {
        "schema": "multimodalcode-agent-trajectory-1",
        "benchmark": case["benchmark"],
        "case_id": case["case_id"],
        "scaffold": "openhands-headless-cli",
        "events": events,
        "raw_trajectory": str(raw_path),
    }
    output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def _claude_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    message = item.get("message")
    content = message.get("content") if isinstance(message, dict) else item.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def normalize_claude_code(
    raw_path: Path, output_path: Path, case: dict[str, Any]
) -> dict[str, Any]:
    """Normalize Claude Code stream-json while retaining its raw artifact."""

    events: list[dict[str, Any]] = []
    if raw_path.is_file():
        for sequence, line in enumerate(
            raw_path.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = str(item.get("type") or "event")
            blocks = _claude_blocks(item)
            text_chunks = [
                str(block.get("text", ""))
                for block in blocks
                if block.get("type") == "text" and block.get("text")
            ]
            tool_blocks = [block for block in blocks if block.get("type") == "tool_use"]
            if tool_blocks:
                for offset, block in enumerate(tool_blocks):
                    action_input = block.get("input")
                    if not isinstance(action_input, dict):
                        action_input = {}
                    events.append(
                        {
                            "sequence": sequence,
                            "subsequence": offset,
                            "actor": "assistant",
                            "kind": "action",
                            "text": _openhands_text(action_input),
                            "tool": block.get("name"),
                            "tools": [block.get("name", "")],
                            "tool_use_id": block.get("id"),
                            "timestamp": item.get("timestamp"),
                        }
                    )
                continue
            actor = {
                "assistant": "assistant",
                "user": "tool" if any(
                    block.get("type") == "tool_result" for block in blocks
                ) else "user",
                "system": "system",
                "result": "agent",
            }.get(event_type, event_type)
            content: Any = "\n".join(text_chunks)
            if not content and event_type == "result":
                content = item.get("result") or item.get("error") or item.get("subtype") or ""
            if not content and blocks:
                content = blocks
            events.append(
                {
                    "sequence": sequence,
                    "actor": actor,
                    "kind": "observation" if actor == "tool" else event_type,
                    "text": _openhands_text(content),
                    "tool": None,
                    "tools": [],
                    "error_code": item.get("subtype") if item.get("is_error") else None,
                    "timestamp": item.get("timestamp"),
                }
            )
    normalized = {
        "schema": "multimodalcode-agent-trajectory-1",
        "benchmark": case["benchmark"],
        "case_id": case["case_id"],
        "scaffold": "claude-code-cli",
        "events": events,
        "raw_trajectory": str(raw_path),
    }
    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return normalized

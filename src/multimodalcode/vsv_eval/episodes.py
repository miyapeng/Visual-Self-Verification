from __future__ import annotations

import hashlib
import fnmatch
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multimodalcode.io import read_json


_LOCAL_APP = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\[::1\]):3000\b", re.I)
_INTERACTION = re.compile(
    r"(?:playwright-cli\s+(?:click|fill|type|hover|press|select|check|uncheck|drag|scroll)\b|"
    r"\.(?:click|fill|type|hover|press|select_option|check|uncheck|drag_to)\s*\()",
    re.I,
)
_NON_PROGRAM_NAMES = {"prompt.txt", "README.md", "DESIGN.md"}


def _program_change_path(value: str) -> bool:
    path = Path(value)
    return (
        path.name not in _NON_PROGRAM_NAMES
        and ".playwright-cli" not in path.parts
        and ".claude" not in path.parts
    )


def _time(value: Any) -> datetime:
    text = str(value or "")
    if not text:
        return datetime.max.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_text(event: dict[str, Any]) -> str:
    text = event.get("text")
    if isinstance(text, str) and text:
        return text
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("command", "description", "path", "file_path"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _resolve_image(path_value: str, run_json: Path) -> str | None:
    path = Path(path_value)
    if path.is_absolute() and path.is_file():
        return str(path)
    for parent in [run_json.parent, *run_json.parents]:
        candidate = parent / path
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _images(event: dict[str, Any], run_json: Path) -> list[str]:
    result: list[str] = []
    for row in event.get("images") or []:
        value = row if isinstance(row, str) else row.get("path") if isinstance(row, dict) else None
        if value:
            resolved = _resolve_image(str(value), run_json)
            if resolved and resolved not in result:
                result.append(resolved)
    return result


def _development_changes(run: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    initial = None
    changes: list[dict[str, Any]] = []
    development = run.get("development") or {}
    for row in development.get("events") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") or {}
        if row.get("type") == "recorder_started":
            initial = payload.get("initial_program_sha256") or initial
        if row.get("type") == "workspace_change":
            created = [value for value in payload.get("created") or [] if _program_change_path(str(value))]
            modified = [value for value in payload.get("modified") or [] if _program_change_path(str(value))]
            deleted = [value for value in payload.get("deleted") or [] if _program_change_path(str(value))]
            if not (created or modified or deleted):
                continue
            changes.append(
                {
                    "timestamp": row.get("timestamp"),
                    "program_sha256": payload.get("program_sha256"),
                    "created": created,
                    "modified": modified,
                    "deleted": deleted,
                }
            )
    changes.sort(key=lambda row: _time(row["timestamp"]))
    return initial, changes


def _program_at(
    timestamp: Any,
    initial: str | None,
    changes: list[dict[str, Any]],
) -> str | None:
    current = initial
    target = _time(timestamp)
    for row in changes:
        if _time(row["timestamp"]) > target:
            break
        current = row.get("program_sha256") or current
    return current


def _changes_between(
    start: Any,
    end: Any,
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    low, high = _time(start), _time(end)
    return [row for row in changes if low < _time(row["timestamp"]) < high]


def _stable_id(case_id: str, first: int, last: int) -> str:
    digest = hashlib.sha256(f"{case_id}:{first}:{last}".encode()).hexdigest()[:10]
    return f"verification-{first:04d}-{last:04d}-{digest}"


def _local_context(event: dict[str, Any]) -> bool:
    return bool(
        _LOCAL_APP.search(str(event.get("browser_url_context") or ""))
        or _LOCAL_APP.search(_event_text(event))
    )


def _local_screenshot_action(
    event: dict[str, Any], output_path: str | None = None
) -> bool:
    text = _event_text(event)
    return (
        event.get("kind") == "action"
        and event.get("tool") == "Bash"
        and _local_context(event)
        and _output_mentioned(text, output_path)
        and bool(re.search(r"playwright|(?:chrome|chromium)\b|\bscreenshot\s*\(|\bnode\s+", text, re.I))
    )


def _output_mentioned(command: str, output_path: str | None) -> bool:
    if not output_path:
        return False
    paths = re.findall(r"/[^\s'\";<>]+\.(?:png|jpe?g|webp)", command)
    return any(fnmatch.fnmatchcase(output_path, re.sub(r"\$\{[^}]+\}|\$\w+", "*", p)) for p in paths)


def _next_action(timeline: list[dict[str, Any]], start: int) -> int:
    for index in range(start + 1, len(timeline)):
        if timeline[index].get("kind") == "action":
            return index
    return len(timeline)


def _unit_end(timeline: list[dict[str, Any]], action_index: int) -> int:
    return _next_action(timeline, action_index) - 1


def _is_visual_evidence_action(
    timeline: list[dict[str, Any]], index: int, source: Path
) -> bool:
    event = timeline[index]
    if event.get("kind") != "action":
        return False
    if event.get("category") == "view-image":
        producer = _visual_producer(timeline, index)
        return producer != index and _local_context(timeline[producer]) and any(
            _images(row, source)
            for row in timeline[index + 1 : _next_action(timeline, index)]
        )
    if not _local_context(event):
        return False
    if event.get("category") != "browser":
        return False
    return any(
        _images(row, source)
        for row in timeline[index + 1 : _next_action(timeline, index)]
    )


def _is_functional_action(event: dict[str, Any]) -> bool:
    return (
        event.get("kind") == "action"
        and event.get("category") == "browser"
        and _local_context(event)
        and bool(_INTERACTION.search(_event_text(event)))
    )


def _visual_producer(timeline: list[dict[str, Any]], index: int) -> int:
    """Include the browser command that produced an image later read by the policy."""
    payload = timeline[index].get("payload") or {}
    output_path = payload.get("file_path") if isinstance(payload, dict) else None
    for candidate in range(index - 1, -1, -1):
        event = timeline[candidate]
        if event.get("kind") != "action":
            continue
        if _local_screenshot_action(event, output_path):
            return candidate
        # A resized screenshot must trace to a browser-produced input, not just
        # inherit the last URL. Asset contact sheets are not application views.
        text = _event_text(event)
        if _output_mentioned(text, output_path) and re.search(r"\.save\s*\(", text):
            inputs = re.findall(r"Image\.open\(\s*['\"]([^'\"]+)['\"]", text)
            for image_input in inputs:
                synthetic = {"kind": "action", "payload": {"file_path": image_input}}
                origin = _visual_producer(timeline[:candidate] + [synthetic], candidate)
                if origin != candidate:
                    return candidate
    return index


def extract_episodes(run_json: str | Path) -> dict[str, Any]:
    source = Path(run_json).resolve()
    run = read_json(source)
    timeline = [row for row in run.get("timeline") or [] if isinstance(row, dict)]
    timeline.sort(key=lambda row: int(row.get("ordinal", 0)))
    initial, changes = _development_changes(run)
    anchors: list[dict[str, Any]] = []
    for index, event in enumerate(timeline):
        if _is_visual_evidence_action(timeline, index, source):
            anchors.append(
                {
                    "kind": "visual",
                    "start": (
                        _visual_producer(timeline, index)
                        if event.get("category") == "view-image"
                        else index
                    ),
                    "evidence": index,
                    "end": _unit_end(timeline, index),
                }
            )
        elif _is_functional_action(event):
            anchors.append(
                {
                    "kind": "functional",
                    "start": index,
                    "evidence": index,
                    "end": _unit_end(timeline, index),
                }
            )

    groups: list[list[dict[str, Any]]] = []
    for anchor in anchors:
        if not groups or groups[-1][-1]["kind"] != anchor["kind"]:
            groups.append([anchor])
        else:
            groups[-1].append(anchor)

    episodes: list[dict[str, Any]] = []
    case_id = str(run.get("case_id") or "unknown")
    for group in groups:
        first, last = group[0], group[-1]
        rows = timeline[first["start"] : last["end"] + 1]
        started_at = rows[0].get("timestamp")
        ended_at = rows[-1].get("timestamp")
        interval_changes = _changes_between(started_at, ended_at, changes)
        first_evidence_at = timeline[first["evidence"]].get("timestamp")
        evidence_changes = [
            change for change in interval_changes
            if _time(change["timestamp"]) > _time(first_evidence_at)
        ]
        latest_change_at = max(
            (_time(change["timestamp"]) for change in evidence_changes),
            default=None,
        )
        recheck_after_edit = bool(
            latest_change_at is not None
            and any(
                _time(timeline[anchor["evidence"]].get("timestamp")) > latest_change_at
                for anchor in group
            )
        )
        image_paths: list[str] = []
        for anchor in group:
            evidence_rows = timeline[
                anchor["evidence"] + 1 : _next_action(timeline, anchor["evidence"])
            ]
            for row in evidence_rows:
                for image in _images(row, source):
                    if image not in image_paths:
                        image_paths.append(image)
        producer_ordinals = {
            int(timeline[anchor["start"]].get("ordinal", 0))
            for anchor in group
            if anchor["kind"] == "visual"
        }
        browser_actions = [
            _event_text(row) for row in rows
            if row.get("kind") == "action"
            and (
                row.get("category") == "browser"
                or int(row.get("ordinal", 0)) in producer_ordinals
            )
        ]
        observations = [
            _event_text(row) for row in rows
            if row.get("kind") == "observation"
            and row.get("category") in {"browser", "view-image"}
            and _event_text(row)
        ]
        policy_messages = [
            _event_text(row) for row in rows
            if row.get("kind") in {"reasoning", "model_text"} and _event_text(row)
        ]
        edit_actions = [
            {
                "ordinal": row.get("ordinal"),
                "tool": row.get("tool"),
                "text": _event_text(row),
                "payload": row.get("payload") or {},
            }
            for row in rows
            if row.get("kind") == "action" and row.get("category") == "edit"
        ]
        episodes.append(
            {
                "episode_id": _stable_id(
                    case_id,
                    int(rows[0].get("ordinal", 0)),
                    int(rows[-1].get("ordinal", 0)),
                ),
                "verification_kind": first["kind"],
                "start_ordinal": int(rows[0].get("ordinal", 0)),
                "end_ordinal": int(rows[-1].get("ordinal", 0)),
                "evidence_ordinals": [
                    int(timeline[anchor["evidence"]].get("ordinal", 0))
                    for anchor in group
                ],
                "program_before": _program_at(started_at, initial, changes),
                "program_after": _program_at(ended_at, initial, changes),
                "action_sequence": browser_actions,
                "observations": observations,
                "policy_messages": policy_messages,
                "edit_actions": edit_actions,
                "images": image_paths,
                "workspace_changes": interval_changes,
                "edit_after_evidence": bool(evidence_changes),
                "recheck_after_edit": recheck_after_edit,
                "events": rows,
            }
        )

    return {
        "schema": "multimodalcode-verification-spans-3",
        "source_run": str(source),
        "case_id": case_id,
        "model": run.get("model"),
        "framework": run.get("framework"),
        "mode": run.get("mode"),
        "selection": "deployed visual evidence consumed by the policy or deployed functional browser interaction",
        "episode_count": len(episodes),
        "episodes": episodes,
    }

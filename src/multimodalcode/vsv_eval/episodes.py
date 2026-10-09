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


def _call_window(timeline: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Keep a call, its ID-paired returns and immediately adjacent policy text."""
    from .checks import _call_id, _results

    action = timeline[index]
    positions = {id(row): i for i, row in enumerate(timeline)}
    observed = _results(timeline, index)
    indices = {index, *(positions[id(row)] for row in observed)}
    before = index - 1
    while before >= 0 and timeline[before].get("kind") in {"model_text", "reasoning", "tool_metadata"}:
        indices.add(before)
        before -= 1
    if observed:
        after = max(positions[id(row)] for row in observed) + 1
        while after < len(timeline) and timeline[after].get("kind") in {"model_text", "reasoning", "tool_metadata"}:
            indices.add(after)
            after += 1
    return {
        "id": f"window-{action['ordinal']}", "action_ordinal": action["ordinal"],
        "tool_call_id": _call_id(action), "events": [timeline[i] for i in sorted(indices)],
    }


def extract_candidate_windows(run_json: str | Path) -> list[dict[str, Any]]:
    """One recorded call per window, including unsuccessful check attempts.

    Inspect/deploy calls are deliberately broad candidates: shell checks are not
    necessarily classified as browser actions. The text-only filter decides
    whether they concern the agent's own output. No image file is opened here.
    """
    run = read_json(Path(run_json).resolve())
    timeline = sorted(
        (row for row in run.get("timeline") or [] if isinstance(row, dict)),
        key=lambda row: int(row["ordinal"]),
    )
    initial, changes = _development_changes(run)
    windows = []
    for index, action in enumerate(timeline):
        if action.get("kind") != "action" or action.get("category", "inspect") not in {
            "browser", "view-image", "inspect", "deploy", "test",
        }:
            continue
        producer = _visual_producer(timeline, index) if action.get("category") == "view-image" else index
        windows.append({
            **_call_window(timeline, index),
            "program_sha256": (_program_at(action["timestamp"], initial, changes)
                               if action.get("timestamp") else None),
            # A distant producer stays a source reference, not an interval or
            # an additional action in this window. Missing provenance is unknown.
            "image_source": ({
                "producer_ordinal": timeline[producer]["ordinal"],
                **{key: timeline[producer][key] for key in (
                    "tool", "payload", "text", "browser_url_context",
                ) if key in timeline[producer]},
            } if producer != index else None),
        })
    return windows


def split_visual_windows(
    windows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Route already-selected checks without changing or merging their events.

    Visual reads, actual image returns, explicit capture attempts and referenced
    image producers go to the visual set. A browser call or a preceding visual
    claim alone is insufficient. No file-existence or execution-success gate.
    """
    producers = {
        window["image_source"]["producer_ordinal"]
        for window in windows if window.get("image_source")
    }
    visual, other = [], []
    for window in windows:
        action = next(row for row in window["events"] if row.get("kind") == "action")
        tool = str(action.get("tool") or "")
        payload = action.get("payload") or {}
        command = str(payload.get("command") or payload.get("cmd") or action.get("text") or "")
        capture = bool(
            re.search(r"screenshot|screen[_-]?capture|capture[_-]?screen|view[_-]?image", tool, re.I)
            or re.search(r"\.screenshot\s*\(|(?:^|[\s;&|])(?:\S*/)?(?:screenshot|screencapture)(?=\s|$)", command, re.I)
        )
        has_image = any(row.get("images") for row in window["events"] if row.get("kind") == "observation")
        target = visual if (action.get("category") == "view-image" or has_image
                            or action["ordinal"] in producers or capture) else other
        target.append(window)
    return visual, other


def extract_verification_processes(
    run_json: str | Path, windows: list[dict[str, Any]], judge: Any,
    *, max_input_chars: int = 240000,
) -> dict[str, Any]:
    """Associate checks and follow-up calls without inventing a continuous span.

    Windows remain the evidence units. A process is a set of references, not an
    old scoring episode or a claim of successful repair. Shared calls are stored
    once and explicitly counted once across processes.
    """
    from .judge import group_verification_windows

    source = Path(run_json).resolve()
    run = read_json(source)
    timeline = sorted(run.get("timeline") or [], key=lambda e: e["ordinal"])
    visual, _ = split_visual_windows(windows)
    visual_ids = {w["id"] for w in visual}
    selected_actions = {w["action_ordinal"] for w in windows}
    first = min((w["action_ordinal"] for w in visual), default=float("inf"))
    # Later edits may resume an earlier check after unrelated work.
    # Present complete call units; the Judge selects IDs, never arbitrary ranges.
    context = [_call_window(timeline, i) for i, e in enumerate(timeline)
               if e.get("kind") == "action" and e.get("category") == "edit" and e["ordinal"] >= first
               and e["ordinal"] not in selected_actions]
    evidence = {e["ordinal"]: e for w in windows + context for e in w["events"]}
    packet = {
        "visual_window_ids": [w["id"] for w in visual],
        "windows": [{**{k: v for k, v in w.items() if k != "events"},
                     "event_ordinals": [e["ordinal"] for e in w["events"]]} for w in windows],
        "context_calls": [{"action_ordinal": w["action_ordinal"],
                           "event_ordinals": [e["ordinal"] for e in w["events"]]} for w in context],
        "events": [evidence[o] for o in sorted(evidence)],
    }
    groups = group_verification_windows(packet, judge, max_input_chars=max_input_chars) if visual else []
    by_id = {w["id"]: w for w in windows}
    by_action = {w["action_ordinal"]: w for w in context}
    processes = []
    for group in groups:
        ids = sorted(group["window_ids"], key=lambda value: by_id[value]["action_ordinal"])
        calls = sorted(group["context_action_ordinals"])
        units = [by_id[value] for value in ids] + [by_action[value] for value in calls]
        ordinals = sorted({e["ordinal"] for w in units for e in w["events"]})
        processes.append({
            "id": f"process-{min(by_id[value]['action_ordinal'] for value in ids if value in visual_ids)}-"
                  + hashlib.sha256(str((ids, calls)).encode()).hexdigest()[:8],
            "window_ids": ids, "context_action_ordinals": calls,
            "event_ordinals": ordinals,
            "image_input_ordinals": [o for o in ordinals if evidence[o].get("kind") == "observation"
                                     and evidence[o].get("images")],
        })
    processes.sort(key=lambda p: (p["event_ordinals"][0], p["id"]))
    used = {value for p in processes for value in p["window_ids"]}
    used_events = {o for p in processes for o in p["event_ordinals"]}
    shared = [w["id"] for w in windows if sum(w["id"] in p["window_ids"] for p in processes) > 1]
    return {
        "schema": "multimodalcode-verification-processes-1",
        "source_run": str(source), "source_run_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "grouping_status": "llm_grouped" if visual else "no_visual_checks", "intended_use": "visual_process_review",
        "process_count": len(processes), "processes": processes,
        "windows": windows,  # one canonical copy, including archived text checks
        "events": [evidence[o] for o in sorted(used_events)],
        "shared_window_ids": shared,
        "unassigned_window_ids": [w["id"] for w in windows if w["id"] not in used],
        "visual_window_count": len(visual),
        "image_input_count": len({o for p in processes for o in p["image_input_ordinals"]}),
    }


def verification_round_summary(episodes: list[dict[str, Any]]) -> dict[str, int]:
    """Compute counts from references; image receipt does not require a local file."""
    return {
        "episode_count": len(episodes),
        "image_episode_count": sum(bool(e["evidence_states"]) for e in episodes),
        "image_input_count": len({s["event_id"] for e in episodes for s in e["evidence_states"]}),
        "visual_attempt_without_image_count": sum(e["verification_kind"] == "visual" and not e["evidence_states"] for e in episodes),
        "non_visual_episode_count": sum(e["verification_kind"] == "non_visual" for e in episodes),
    }


def extract_verification_rounds(
    run_json: str | Path, judge: Any, *, max_input_chars: int = 240000,
) -> dict[str, Any]:
    """Extract visual and nonvisual rounds with paired evidence and follow-ups."""
    from .checks import _TOOL_ERROR, _results, _call_id
    from .judge import annotate_verification_candidates

    source = Path(run_json).resolve()
    run = read_json(source)
    timeline = sorted((e for e in run.get("timeline") or [] if isinstance(e, dict)), key=lambda e: e["ordinal"])
    events = {e["ordinal"]: e for e in timeline}
    positions = {e["ordinal"]: i for i, e in enumerate(timeline)}
    calls = {_call_id(e): e["ordinal"] for e in timeline if e.get("kind") == "action" and _call_id(e)}
    initial, changes = _development_changes(run)
    candidates = extract_candidate_windows(source)
    edits = [_call_window(timeline, i) for i, e in enumerate(timeline)
             if e.get("kind") == "action" and e.get("category") == "edit"]
    by_id = {w["id"]: w for w in candidates}
    by_action = {w["action_ordinal"]: w for w in candidates + edits}
    change_calls = set()
    for unit in candidates + edits:
        action = events[unit["action_ordinal"]]
        observed = _results(timeline, positions[action["ordinal"]])
        failed = any(e.get("is_error") is True or _TOOL_ERROR.search(str(e.get("text") or "")) for e in observed)
        before = _program_at(action["timestamp"], initial, changes) if action.get("timestamp") else None
        end = max((e.get("timestamp") for e in observed if e.get("timestamp")), key=_time, default=None)
        after = _program_at(end, initial, changes) if end else None
        unit["program_sha256"] = before
        unit["program_after_call_sha256"] = after
        unit["session_key"] = [action.get(k) for k in ("attempt", "scope", "parent_tool_use_id")]
        unit["recorded_changes"] = [c for c in changes if action.get("timestamp") and end
                                   and _time(action["timestamp"]) <= _time(c["timestamp"]) <= _time(end)]
        path = str((action.get("payload") or {}).get("file_path") or (action.get("payload") or {}).get("path") or "")
        unit["modification_status"] = ("failed" if failed else "recorded" if unit["recorded_changes"]
                                       or (action.get("category") == "edit" and observed and _program_change_path(path))
                                       else "unknown" if action.get("category") == "edit" else "none")
        if unit["modification_status"] == "recorded" or unit["recorded_changes"] or (before and after and before != after):
            change_calls.add(action["ordinal"])
        if unit.get("image_source"):
            producer = by_action.get(unit["image_source"]["producer_ordinal"])
            start_state = producer.get("program_sha256") if producer else None
            end_state = producer.get("program_after_call_sha256") if producer else None
            unit["image_source"]["program_before_call_sha256"] = start_state
            unit["image_source"]["program_sha256"] = start_state if start_state == end_state else None
        unit["image_availability"] = [
            {"event_id": e["ordinal"], "path": path, "available": bool(_resolve_image(str(path), source))}
            for e in unit["events"] if e.get("kind") == "observation"
            for image in e.get("images") or []
            for path in [image if isinstance(image, str) else image.get("path", "")]
        ]

    annotation = annotate_verification_candidates(candidates, edits, judge, max_input_chars=max_input_chars)
    # Across budgeted batches a boundary round can be continued by reference.
    parent = {value: value for row in annotation["rounds"] for value in row["candidate_ids"]}

    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for row in annotation["rounds"]:
        first = root(row["candidate_ids"][0])
        for value in row["candidate_ids"][1:]:
            parent[root(value)] = first
    if set(parent) & set(annotation["excluded_candidate_ids"]):
        raise ValueError("A context candidate was retained although its owning batch excluded it")
    grouped = {}
    for row in annotation["rounds"]:
        group = grouped.setdefault(root(row["candidate_ids"][0]), {
            "candidate_ids": set(), "policy_event_ids": set(), "judgment_event_ids": set(), "context_event_ids": set(),
        })
        for key in group:
            group[key].update(row[key])

    episodes, owner, policy_owner = [], {}, {}
    for group in grouped.values():
        ids = sorted(group["candidate_ids"], key=lambda value: by_id[value]["action_ordinal"])
        actions = [by_id[value]["action_ordinal"] for value in ids]
        if len({tuple(by_id[v]["session_key"]) for v in ids}) > 1:
            raise ValueError("A check round crosses recorded session boundaries")
        if any(actions[0] < o < actions[-1] for o in change_calls):
            raise ValueError("A check round crosses a recorded modification; link a new round instead")
        known = {by_id[v]["program_sha256"] for v in ids if by_id[v]["program_sha256"]}
        if len(known) > 1:
            raise ValueError("A check round mixes recorded implementation states")
        core = set(group["policy_event_ids"])
        for ordinal in actions:
            core.add(ordinal)
            core.update(e["ordinal"] for e in _results(timeline, positions[ordinal]))
        observations = {o for o in core | group["context_event_ids"] if events[o].get("kind") == "observation"}
        if any(not any(o < j for o in observations) or j < actions[0] for j in group["judgment_event_ids"]):
            raise ValueError("A round judgment must follow its recorded evidence")
        if any(o > max(core) for o in group["context_event_ids"]):
            raise ValueError("Future events cannot be a check round's current context")
        if any(actions[0] < o < max(core) for o in change_calls):
            raise ValueError("A check response crosses a modification boundary")
        episode_id = _stable_id(str(run.get("case_id") or "unknown"), min(core), max(core))
        for value in ids:
            owner[value] = episode_id
        for ordinal in group["policy_event_ids"]:
            if ordinal in policy_owner and policy_owner[ordinal] != episode_id:
                raise ValueError("A policy event cannot belong to two independent check cores; reference shared context instead")
            policy_owner[ordinal] = episode_id
        context = set(group["context_event_ids"])
        for o in list(context):
            origin = o if events[o].get("kind") == "action" else calls.get(_call_id(events[o]))
            if origin is not None:
                paired = {e["ordinal"] for e in _results(timeline, positions[origin])}
                if any(p > max(core) for p in paired):
                    raise ValueError("Context call returned after this check's evidence boundary")
                context.update({origin, *paired})
        evidence_states = []
        for value in ids:
            w = by_id[value]
            producer = (w.get("image_source") or {}).get("producer_ordinal")
            if producer is not None:
                # Shared producer remains a full call reference, outside the core.
                context.update(e["ordinal"] for e in by_action[producer]["events"]
                               if e["ordinal"] <= max(core))
            for e in w["events"]:
                if e.get("kind") == "observation" and e.get("images"):
                    capture = (by_action.get(producer) if producer is not None else
                               w if events[w["action_ordinal"]].get("category") != "view-image" else None)
                    capture_start = capture.get("program_sha256") if capture else None
                    capture_end = capture.get("program_after_call_sha256") if capture else None
                    capture_state = capture_start if capture_start == capture_end else None
                    evidence_states.append({"event_id": e["ordinal"], "producer_event_id": producer,
                                            "program_sha256": capture_state,
                                            "read_program_sha256": w["program_sha256"]})
        context -= core
        visual, _ = split_visual_windows([by_id[v] for v in ids])
        observed = [events[o] for o in sorted(core) if events[o].get("kind") == "observation"]
        episodes.append({
            "episode_id": episode_id, "candidate_ids": ids,
            "verification_kind": "visual" if visual else "non_visual",
            "evidence_modalities": [m for m, present in (
                ("image", any(e.get("images") for e in observed)),
                ("text", any(str(e.get("text") or "").strip() for e in observed)),
            ) if present],
            "core_event_ids": sorted(core),
            "judgment_event_ids": sorted(group["judgment_event_ids"]),
            "context_event_ids": sorted(context), "evidence_states": evidence_states,
            "program_sha256": next(iter(known), None), "repair_links": [],
            "relation_annotation_complete": any(
                set(ids) <= set(b["owned_candidate_ids"] + b["context_candidate_ids"])
                and not b["omitted_candidate_ids"] and not b["omitted_edit_event_ids"]
                for b in annotation["batches"]),
        })
    episodes.sort(key=lambda e: min(e["core_event_ids"]))
    episode_by_id = {e["episode_id"]: e for e in episodes}
    for link in annotation["repair_links"]:
        source_id = owner.get(link["source_candidate_id"])
        if source_id is None:
            raise ValueError("Repair source must be a retained check")
        episode = episode_by_id[source_id]
        edit_ids = sorted(link["repair_event_ids"])
        if any(o <= max(episode["core_event_ids"]) or o not in by_action
               or (events[o].get("category") != "edit" and o not in change_calls
                   and not ((events[o].get("payload") or {}).get("command")
                            and _results(timeline, positions[o]))) for o in edit_ids):
            raise ValueError("Repair must reference a later recorded modifying call")
        rechecks = []
        for value in link["recheck_candidate_ids"]:
            target_id = owner.get(value)
            if target_id is None or target_id == source_id:
                raise ValueError("Recheck must reference a different retained round")
            target = episode_by_id[target_id]
            completed = max([*edit_ids, *(e["ordinal"] for o in edit_ids for e in _results(timeline, positions[o]))])
            if min(by_id[v]["action_ordinal"] for v in target["candidate_ids"]) <= completed:
                raise ValueError("Recheck must follow the referenced modifications")
            if target["evidence_states"] and all(e["producer_event_id"] is not None
                                                 and e["producer_event_id"] <= completed for e in target["evidence_states"]):
                raise ValueError("Historical screenshots cannot establish a post-modification recheck")
            if target_id not in rechecks:
                rechecks.append(target_id)
        normalized = {"repair_event_ids": edit_ids, "recheck_episode_ids": rechecks,
                      "evidence_event_ids": sorted(link["evidence_event_ids"])}
        if normalized not in episode["repair_links"]:
            episode["repair_links"].append(normalized)
    used_context = {o for e in episodes for o in e["context_event_ids"]}
    return {
        "schema": "multimodalcode-verification-rounds-1", "source_run": str(source),
        "source_run_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "annotation_status": "llm_annotated", "intended_use": "verification_analysis_input",
        **{k: run.get(k) for k in ("case_id", "model", "framework", "mode", "raw_links")},
        "versions": (run.get("result") or {}).get("versions"),
        "development_root": (run.get("development") or {}).get("root"),
        "workspace_artifact": (run.get("result") or {}).get("workspace_artifact"),
        "candidate_count": len(candidates), **verification_round_summary(episodes),
        "episodes": episodes,
        "windows": [{
            **{k: v for k, v in w.items() if k not in {"events", "image_source"}},
            "event_ids": [e["ordinal"] for e in w["events"]],
            "image_source": ({k: w["image_source"][k] for k in ("producer_ordinal", "program_sha256")}
                             if w.get("image_source") else None),
        } for w in candidates],
        "events": timeline, "annotation": annotation,
        "source_only_candidate_ids": [w["id"] for w in candidates if w["id"] not in owner
                                      and w["action_ordinal"] in used_context],
        "annotation_limitations": ([] if len(annotation["batches"]) <= 1 else
                                   ["Budgeted batches include bounded neighboring checks and whole edit calls; omitted IDs are recorded per batch. Cross-batch links lacking joint evidence are unannotated, not negative findings."]),
    }


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

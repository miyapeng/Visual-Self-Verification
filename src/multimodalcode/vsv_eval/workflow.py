from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from multimodalcode.io import read_json


_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "page", "that", "the", "this",
    "to", "verify", "when", "with", "successfully", "positive", "scenario",
}


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in _STOP
    }


def load_workflow(path: str | Path) -> list[dict[str, Any]]:
    raw = read_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"Expected a workflow list: {path}")
    rows: list[dict[str, Any]] = []
    for group_position, group in enumerate(raw):
        if not isinstance(group, dict):
            continue
        group_index = group.get("index", group_position)
        for item_position, item in enumerate(group.get("content") or []):
            if not isinstance(item, dict):
                continue
            workflow_id = f"{group_index}.{item_position}"
            row = {
                "workflow_id": workflow_id,
                "group_index": group_index,
                "item_index": item_position,
                "summary": str(group.get("summary") or ""),
                "objective": str(item.get("objective") or ""),
                "actions": [str(value) for value in item.get("actions") or []],
                "validations": [
                    str(value) for value in item.get("validations") or []
                ],
                "prototype": group.get("prototype") or {},
            }
            row["search_text"] = " ".join(
                [row["summary"], row["objective"], *row["actions"], *row["validations"]]
            )
            rows.append(row)
    return rows


def lexical_candidates(
    action_text: str,
    workflow: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query = _tokens(action_text)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in workflow:
        target = _tokens(str(row["search_text"]))
        overlap = query & target
        score = len(overlap) / max(1, min(len(query), len(target)))
        path_bonus = 0.0
        for marker in re.findall(r"/[a-z0-9_-]+", action_text.casefold()):
            if marker.strip("/") in target:
                path_bonus += 0.15
        scored.append((min(1.0, score + path_bonus), row))
    scored.sort(key=lambda item: (-item[0], item[1]["workflow_id"]))
    return [
        {
            **{key: value for key, value in row.items() if key != "search_text"},
            "lexical_score": round(score, 4),
        }
        for score, row in scored[:limit]
        if score > 0
    ]


def prototype_paths(
    task_root: str | Path,
    workflow_rows: list[dict[str, Any]],
) -> list[str]:
    root = Path(task_root)
    result: list[str] = []
    for row in workflow_rows:
        for name in (row.get("prototype") or {}).keys():
            candidates = [
                root / "prototypes" / f"{name}.jpg",
                root / "prototypes" / f"{name}.png",
                root / "prototypes" / f"{name.replace('_', '-')}.jpg",
            ]
            match = next((path for path in candidates if path.is_file()), None)
            if match is not None and str(match) not in result:
                result.append(str(match))
    return result

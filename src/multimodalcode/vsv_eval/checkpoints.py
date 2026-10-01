from __future__ import annotations

import json
import os
import difflib
from pathlib import Path
from typing import Any

from multimodalcode.io import read_json


def materialize_manifest(
    manifest_path: str | Path,
    object_root: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid program manifest: {manifest_path}")
    objects = Path(object_root).resolve()
    output = Path(destination).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    missing: list[str] = []
    for name, row in sorted(manifest.items()):
        digest = str(row["sha256"])
        source = objects / digest[:2] / digest
        if not source.is_file():
            missing.append(name)
            continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        os.chmod(target, int(row.get("mode", 0o644)))
    if missing:
        raise FileNotFoundError(
            "Missing program objects for: " + ", ".join(missing[:20])
        )
    return {"destination": str(output), "file_count": len(manifest)}


def _bindings(value: Any):
    if isinstance(value, dict):
        if value.get("schema") == "multimodalcode-observation-binding-1":
            yield value
        for item in value.values():
            yield from _bindings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _bindings(item)


def checkpoint_index(run_json: str | Path) -> dict[str, dict[str, Any]]:
    run = read_json(run_json)
    root_value = (run.get("development") or {}).get("root")
    if not root_value:
        return {}
    root = Path(root_value)
    result: dict[str, dict[str, Any]] = {}
    versions_path = root / "versions.json"
    if versions_path.is_file():
        versions = read_json(versions_path)
        for name in ("P_first", "P_final"):
            row = versions.get(name)
            if isinstance(row, dict) and row.get("program_sha256") and Path(str(row.get("path"))).is_dir():
                result[str(row["program_sha256"])] = {
                    "source": name,
                    "workspace": str(Path(str(row["path"])).resolve()),
                    "reconstructable": True,
                }
    for event_name in ("browser.events.jsonl", "workspace.events.jsonl"):
        path = root / event_name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for binding in _bindings(row):
                digest = binding.get("program_sha256")
                if not digest:
                    continue
                if result.get(str(digest), {}).get("reconstructable"):
                    continue
                objects = binding.get("program_objects") or {}
                result[str(digest)] = {
                    "source": event_name,
                    "manifest": binding.get("file_manifest_path"),
                    "object_root": objects.get("program_object_root"),
                    "reconstructable": bool(objects.get("reconstructable")),
                }
    return result


def checkpoint_workspace(
    checkpoint: dict[str, Any] | None,
    destination: str | Path,
) -> Path | None:
    if not checkpoint or not checkpoint.get("reconstructable"):
        return None
    workspace = checkpoint.get("workspace")
    if workspace and Path(str(workspace)).is_dir():
        return Path(str(workspace)).resolve()
    manifest = checkpoint.get("manifest")
    objects = checkpoint.get("object_root")
    if not manifest or not objects:
        return None
    output = Path(destination).resolve()
    if not output.exists():
        materialize_manifest(str(manifest), str(objects), output)
    return output


def workspace_patch(
    before: str | Path,
    after: str | Path,
    *,
    max_chars: int = 40_000,
) -> dict[str, Any]:
    left, right = Path(before), Path(after)
    left_files = {path.relative_to(left).as_posix(): path for path in left.rglob("*") if path.is_file()}
    right_files = {path.relative_to(right).as_posix(): path for path in right.rglob("*") if path.is_file()}
    changed: list[str] = []
    chunks: list[str] = []
    truncated = False
    for name in sorted(set(left_files) | set(right_files)):
        old = left_files.get(name).read_bytes() if name in left_files else b""
        new = right_files.get(name).read_bytes() if name in right_files else b""
        if old == new:
            continue
        changed.append(name)
        try:
            old_lines = old.decode("utf-8").splitlines(keepends=True)
            new_lines = new.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            chunks.append(f"Binary file changed: {name}\n")
            continue
        chunks.extend(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )
        if sum(len(chunk) for chunk in chunks) > max_chars:
            truncated = True
            break
    text = "".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[patch truncated]\n"
    return {"changed_files": changed, "unified_diff": text, "truncated": truncated}

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..io import write_json
from .interactive import (
    PROGRAM_RUNTIME_DIRECTORIES,
    PROGRAM_SOURCE_SUFFIXES,
    hash_program,
)


class SafeFileToolExecutor:
    """Applies model-produced file maps inside an isolated case workspace."""

    def __init__(self, workspace: str | Path, checkpoint_root: str | Path):
        self.workspace = Path(workspace).resolve()
        self.checkpoint_root = Path(checkpoint_root).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def prepare(
        cls,
        source: str | Path,
        workspace: str | Path,
        checkpoint_root: str | Path,
    ) -> "SafeFileToolExecutor":
        source_path = Path(source).resolve()
        target = Path(workspace).resolve()
        if not target.exists():
            if source_path.is_dir():
                shutil.copytree(
                    source_path,
                    target,
                    ignore=shutil.ignore_patterns(*PROGRAM_RUNTIME_DIRECTORIES),
                )
                dependency_root = source_path / "node_modules"
                if dependency_root.is_dir():
                    # Dependencies are immutable benchmark/runtime inputs, but
                    # Vite writes its optimizer cache to node_modules/.vite.
                    # Link every released dependency into the isolated case
                    # while keeping that cache directory local and writable.
                    target_dependencies = target / "node_modules"
                    target_dependencies.mkdir()
                    for dependency in sorted(dependency_root.iterdir()):
                        if dependency.name == ".vite":
                            continue
                        (target_dependencies / dependency.name).symlink_to(
                            dependency,
                            target_is_directory=dependency.is_dir(),
                        )
                    (target_dependencies / ".vite").mkdir()
            else:
                target.mkdir(parents=True)
                shutil.copy2(source_path, target / source_path.name)
        return cls(target, checkpoint_root)

    def apply_files(self, files: Dict[str, str]) -> List[str]:
        if not files:
            raise ValueError("A patch decision requires at least one file")
        written: List[str] = []
        for relative, content in files.items():
            destination = (self.workspace / relative).resolve()
            if not destination.is_relative_to(self.workspace):
                raise ValueError(f"Patch path escapes workspace: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(str(content), encoding="utf-8")
            written.append(str(destination.relative_to(self.workspace)))
        return written

    def apply_revision(
        self, *, files: Dict[str, str], edits: List[Dict[str, Any]]
    ) -> List[str]:
        """Validate a revision completely, then apply it to the workspace.

        No workspace write occurs before all typed operations have passed
        validation.  A rejected mixed payload therefore cannot create an
        untracked intermediate code version.
        """

        staged, written = self._stage_revision(files=files, edits=edits)
        for destination, content in staged.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        return written

    def _stage_revision(
        self, *, files: Dict[str, str], edits: List[Dict[str, Any]]
    ) -> tuple[Dict[Path, str], List[str]]:
        """Validate and stage a revision without mutating the workspace."""

        if not files and not edits:
            raise ValueError("A patch decision requires at least one edit or new file")
        staged: Dict[Path, str] = {}
        written: List[str] = []
        for edit in edits:
            relative = str(edit["path"])
            destination = self._safe_destination(relative)
            if not destination.is_file():
                raise FileNotFoundError(f"Exact edit target does not exist: {relative}")
            old = str(edit["old"])
            new = str(edit["new"])
            if old == new:
                raise ValueError(
                    f"Exact edit for {relative} is a no-op; return keep or make a real change"
                )
            content = staged.get(destination)
            if content is None:
                content = destination.read_text(encoding="utf-8")
            count = content.count(old)
            replace_all = bool(edit.get("replace_all", False))
            if count == 0:
                raise ValueError(f"Exact edit text was not found in {relative}")
            if count != 1 and not replace_all:
                raise ValueError(
                    f"Exact edit text occurs {count} times in {relative}; "
                    "provide more surrounding text or set replace_all"
                )
            staged[destination] = content.replace(
                old, new, -1 if replace_all else 1
            )
            if relative not in written:
                written.append(relative)
        for relative, content in files.items():
            destination = self._safe_destination(relative)
            if destination.exists():
                raise ValueError(
                    f"Full-content payload may only create a new file: {relative}; "
                    "use exact edits for existing files"
                )
            staged[destination] = str(content)
            if relative not in written:
                written.append(relative)
        if all(
            destination.is_file()
            and destination.read_text(encoding="utf-8") == content
            for destination, content in staged.items()
        ):
            raise ValueError("Admitted patch did not create a new program version")
        return staged, written

    def _admit_file_payloads(
        self, *, files: Dict[str, str], edits: List[Dict[str, Any]]
    ) -> tuple[Dict[str, str], List[str]]:
        edit_paths = {str(edit["path"]) for edit in edits}
        admitted_files: Dict[str, str] = {}
        rejected_existing_payloads: List[str] = []
        for relative, content in files.items():
            normalized = str(relative)
            destination = self._safe_destination(normalized)
            if destination.exists() and normalized in edit_paths:
                rejected_existing_payloads.append(normalized)
                continue
            admitted_files[normalized] = str(content)
        return admitted_files, rejected_existing_payloads

    def validate_revision_with_admission(
        self, *, files: Dict[str, str], edits: List[Dict[str, Any]]
    ) -> tuple[List[str], List[str]]:
        """Validate the exact revision admission policy without writing files."""

        admitted_files, rejected = self._admit_file_payloads(
            files=files, edits=edits
        )
        _staged, written = self._stage_revision(
            files=admitted_files, edits=edits
        )
        return written, rejected

    def apply_revision_with_admission(
        self, *, files: Dict[str, str], edits: List[Dict[str, Any]]
    ) -> tuple[List[str], List[str]]:
        """Admit exact edits and reject redundant existing-file overwrites.

        If a response contains both a valid exact edit and a full-content
        payload for the same existing path, the exact edit is the unambiguous
        safe action.  The overwrite subaction is rejected and returned for the
        audit log.  An existing-file payload without a same-path exact edit is
        still a hard contract error.
        """

        admitted_files, rejected_existing_payloads = self._admit_file_payloads(
            files=files, edits=edits
        )
        written = self.apply_revision(files=admitted_files, edits=edits)
        return written, rejected_existing_payloads

    def _safe_destination(self, relative: str) -> Path:
        destination = (self.workspace / relative).resolve()
        if not destination.is_relative_to(self.workspace):
            raise ValueError(f"Patch path escapes workspace: {relative}")
        return destination

    def checkpoint(self, version: str | None = None) -> Path:
        selected = version or hash_program(self.workspace)
        target = self.checkpoint_root / selected
        if not target.exists():
            shutil.copytree(
                self.workspace,
                target,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    *sorted(PROGRAM_RUNTIME_DIRECTORIES - {"node_modules"})
                ),
            )
        return target

    def restore(self, version: str) -> None:
        source = self.checkpoint_root / version
        if not source.is_dir():
            raise FileNotFoundError(f"Checkpoint not found: {version}")
        for item in list(self.workspace.iterdir()):
            if item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in source.iterdir():
            destination = self.workspace / item.name
            if item.is_symlink():
                destination.symlink_to(os.readlink(item), target_is_directory=item.is_dir())
            elif item.is_dir():
                shutil.copytree(item, destination, symlinks=True)
            else:
                shutil.copy2(item, destination)
        dependencies = self.workspace / "node_modules"
        if dependencies.is_dir():
            (dependencies / ".vite").mkdir(exist_ok=True)

    def render_program(
        self,
        *,
        max_bytes: int = 200_000,
        relative_paths: Iterable[str] | None = None,
    ) -> str:
        rendered, _manifest = self.render_program_context(
            max_bytes=max_bytes,
            relative_paths=relative_paths,
        )
        return rendered

    def render_program_context(
        self,
        *,
        max_bytes: int = 200_000,
        relative_paths: Iterable[str] | None = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Render complete source units and record the exact supplied byte set."""

        requested = None if relative_paths is None else [str(value) for value in relative_paths]
        ignored_requested: List[str] = []
        if requested is None:
            candidates = sorted(
                item for item in self.workspace.rglob("*") if item.is_file()
            )
        else:
            candidates = []
            seen: set[Path] = set()
            for relative_text in requested:
                path = (self.workspace / relative_text).resolve()
                if not path.is_relative_to(self.workspace):
                    ignored_requested.append(relative_text)
                    continue
                if path in seen:
                    continue
                seen.add(path)
                candidates.append(path)

        eligible: List[Path] = []
        for path in candidates:
            try:
                relative = path.relative_to(self.workspace)
            except ValueError:
                if requested is not None:
                    ignored_requested.append(str(path))
                continue
            if (
                not path.is_file()
                or path.is_symlink()
                or any(
                    part in PROGRAM_RUNTIME_DIRECTORIES or part == ".git"
                    for part in relative.parts
                )
                or path.suffix.lower() not in PROGRAM_SOURCE_SUFFIXES
            ):
                if requested is not None:
                    ignored_requested.append(relative.as_posix())
                continue
            eligible.append(path)

        parts: List[str] = []
        rows: List[Dict[str, Any]] = []
        omitted_due_to_budget: List[str] = []
        used = 0
        for position, path in enumerate(eligible):
            relative = path.relative_to(self.workspace).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            content_bytes = content.encode("utf-8")
            header = f"\n--- FILE: {relative} ---\n"
            header_bytes = header.encode("utf-8")
            remaining = int(max_bytes) - used - len(header_bytes)
            if remaining <= 0 or len(content_bytes) > remaining:
                omitted_due_to_budget.extend(
                    item.relative_to(self.workspace).as_posix()
                    for item in eligible[position:]
                )
                break
            snippet = content
            supplied_content_bytes = len(content_bytes)
            rendered_piece = header + snippet
            rendered_bytes = len(rendered_piece.encode("utf-8"))
            parts.append(rendered_piece)
            used += rendered_bytes
            rows.append(
                {
                    "path": relative,
                    "source_bytes": len(content_bytes),
                    "supplied_content_bytes": supplied_content_bytes,
                    "rendered_bytes": rendered_bytes,
                    "truncated": False,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        return "".join(parts), {
            "selection": "all_renderable" if requested is None else "explicit_paths",
            "requested_paths": requested,
            "selected_files": rows,
            "selected_file_count": len(rows),
            "selected_source_bytes": sum(row["source_bytes"] for row in rows),
            "supplied_content_bytes": sum(
                row["supplied_content_bytes"] for row in rows
            ),
            "rendered_bytes": used,
            "max_bytes": int(max_bytes),
            "ignored_requested_paths": ignored_requested,
            "omitted_due_to_budget": omitted_due_to_budget,
        }


def parse_revision(
    text: str,
    *,
    max_edits: int | None = None,
    max_patch_chars: int | None = None,
) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    if start < 0:
        raise ValueError("Revision response does not contain a JSON object")

    # Some otherwise useful model responses repeat the exact same JSON object or
    # append one unmatched closing brace. JSONDecoder.raw_decode lets resume
    # recover those saved responses without a second model call. Different
    # trailing objects remain an error because silently choosing one would make
    # the revision contract ambiguous.
    decoder = json.JSONDecoder()
    payload = stripped[start:]
    boundary_normalizations: List[str] = []
    try:
        value, end = decoder.raw_decode(payload)
    except json.JSONDecodeError as exc:
        repaired = _close_missing_json_boundary(payload)
        if repaired is None:
            raise ValueError("Revision response does not contain valid JSON") from exc
        payload, suffix = repaired
        value, end = decoder.raw_decode(payload)
        boundary_normalizations.append(
            f"append_missing_json_delimiters:{suffix}"
        )
    trailing = payload[end:].strip()
    while trailing:
        if trailing == "```":
            trailing = ""
            break
        if set(trailing) == {"}"}:
            trailing = ""
            break
        try:
            duplicate, duplicate_end = decoder.raw_decode(trailing)
        except json.JSONDecodeError as exc:
            raise ValueError("Revision response has unexpected trailing content") from exc
        if duplicate != value:
            raise ValueError("Revision response contains multiple different JSON objects")
        trailing = trailing[duplicate_end:].strip()
    if not isinstance(value, dict):
        raise ValueError("Revision response must be a JSON object")
    unknown_top_level = sorted(
        set(value) - {"decision", "files", "edits", "reason", "checkpoint"}
    )
    if unknown_top_level:
        raise ValueError(
            f"Revision response has unknown top-level fields: {unknown_top_level}"
        )
    decision = str(value.get("decision", "patch")).lower()
    if decision not in {"patch", "keep", "rollback", "stop"}:
        raise ValueError(f"Unsupported revision decision: {decision}")
    files = value.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("Revision files must be an object mapping paths to contents")
    raw_edits = value.get("edits", [])
    if not isinstance(raw_edits, list):
        raise ValueError("Revision edits must be a list")
    if max_edits is not None and len(raw_edits) > max_edits:
        raise ValueError(f"Revision exceeds the edit budget {max_edits}")
    edits: List[Dict[str, Any]] = []
    nested_reasons: List[str] = []
    normalizations: List[str] = list(boundary_normalizations)
    for index, edit in enumerate(raw_edits):
        if not isinstance(edit, dict):
            raise ValueError(f"Revision edit {index} must be an object")
        unknown_edit_fields = sorted(
            set(edit) - {"path", "old", "new", "replace_all", "reason"}
        )
        if unknown_edit_fields:
            raise ValueError(
                f"Revision edit {index} has unknown fields: {unknown_edit_fields}; "
                "files belong at the top level"
            )
        if edit.get("reason"):
            nested_reasons.append(str(edit["reason"]))
            normalizations.append(f"edit[{index}].reason->reason")
        path = str(edit.get("path", "")).strip()
        old = edit.get("old")
        if not path or not isinstance(old, str) or not old:
            raise ValueError(
                f"Revision edit {index} requires path and non-empty exact old text"
            )
        new = str(edit.get("new", ""))
        if old == new:
            raise ValueError(
                f"Revision edit {index} is a no-op; old and new must differ"
            )
        edits.append(
            {
                "path": path,
                "old": old,
                "new": new,
                "replace_all": bool(edit.get("replace_all", False)),
            }
        )
    if decision == "patch" and not files and not edits:
        raise ValueError("A patch decision requires edits or new files")
    patch_chars = sum(len(edit["old"]) + len(edit["new"]) for edit in edits)
    patch_chars += sum(len(str(content)) for content in files.values())
    if max_patch_chars is not None and patch_chars > max_patch_chars:
        raise ValueError(f"Revision exceeds the patch character budget {max_patch_chars}")
    reason = str(value.get("reason", ""))
    if not reason and nested_reasons:
        reason = " ".join(nested_reasons)
    return {
        "decision": decision,
        "files": {str(path): str(content) for path, content in files.items()},
        "edits": edits,
        "reason": reason,
        "checkpoint": value.get("checkpoint"),
        "normalizations": normalizations,
    }


def _close_missing_json_boundary(payload: str) -> Optional[tuple[str, str]]:
    """Close at most two uniquely implied outer JSON delimiters.

    This is intentionally narrower than general JSON repair.  It accepts only
    a response that ends immediately after a complete object/array value, is
    not inside a string, has a structurally valid delimiter stack, and becomes
    exactly one JSON object after appending the stack's unique closing suffix.
    Strings, commas, colons, mismatched delimiters, and deeper truncations stay
    rejected by the typed admission gate.
    """

    candidate = payload.rstrip()
    if not candidate or candidate[-1] not in "}]":
        return None
    stack: List[str] = []
    in_string = False
    escaped = False
    pairs = {("{", "}"), ("[", "]")}
    for character in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or (stack[-1], character) not in pairs:
                return None
            stack.pop()
    if in_string or not stack or len(stack) > 2:
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    repaired = candidate + suffix
    try:
        value, end = json.JSONDecoder().raw_decode(repaired)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or repaired[end:].strip():
        return None
    return repaired, suffix

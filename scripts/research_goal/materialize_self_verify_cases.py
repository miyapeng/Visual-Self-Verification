#!/usr/bin/env python3
"""Materialize frozen public programs for a non-root browser runner.

The input manifest has already passed the leakage-safe benchmark adapter.  This
step copies only each generated program into node-local scratch, makes copied
source readable, preserves released dependencies either through a symlink or a
content-identical node-local copy, and rewrites ``program_path``.  It does not
inspect evaluator or trajectory files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


IGNORED_COPY_PARTS = {
    ".cache",
    ".git",
    ".parcel-cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value)
    return normalized.strip("_") or "case"


def require_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty materialization root: {path}")
    path.mkdir(parents=True, exist_ok=True)


def make_publicly_readable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            executable = path.stat().st_mode & 0o111
            path.chmod(0o644 | executable)
    root.chmod(0o755)


def dependency_tree_identity(root: Path) -> dict[str, Any]:
    """Hash dependency layout and contents without following symlink targets."""

    records: list[list[Any]] = []
    file_count = 0
    symlink_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if ".vite" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            records.append(["symlink", relative, path.readlink().as_posix()])
            symlink_count += 1
        elif path.is_file():
            records.append(
                ["file", relative, hashlib.sha256(path.read_bytes()).hexdigest()]
            )
            file_count += 1
        elif path.is_dir():
            records.append(["directory", relative])
    return {
        "sha256": canonical_sha256(records),
        "file_count": file_count,
        "symlink_count": symlink_count,
        "excluded_runtime_cache": ".vite",
    }


def copy_program(
    source: Path,
    target: Path,
    *,
    dependency_source: Path | None = None,
    copy_dependencies: bool = False,
) -> Path | None:
    if not source.is_dir():
        raise ValueError(f"Pilot requires a program directory: {source}")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*sorted(IGNORED_COPY_PARTS)),
    )
    make_publicly_readable(target)
    dependencies = dependency_source or (source / "node_modules")
    if dependencies.is_dir():
        resolved_dependencies = dependencies.resolve()
        if copy_dependencies:
            shutil.copytree(
                resolved_dependencies,
                target / "node_modules",
                symlinks=True,
                ignore=shutil.ignore_patterns(".vite"),
            )
            make_publicly_readable(target / "node_modules")
        else:
            (target / "node_modules").symlink_to(
                resolved_dependencies, target_is_directory=True
            )
        return resolved_dependencies
    return None


def verify_dependency_manifest(root: Path, manifest: Path) -> None:
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip() or "  " not in line:
            raise ValueError(
                f"Invalid dependency checksum line {line_number}: {manifest}"
            )
        expected, relative = line.split("  ", 1)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(
                f"Dependency checksum path is missing or unsafe: {relative}"
            )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Dependency checksum mismatch: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--copy-dependencies",
        action="store_true",
        help=(
            "copy released node_modules into node-local output instead of linking "
            "to shared storage; useful for non-root runtimes that cannot realpath /data"
        ),
    )
    args = parser.parse_args()

    case_manifest = Path(args.cases).resolve()
    output = Path(args.output_dir).resolve()
    require_empty(output)
    rows = [
        json.loads(line)
        for line in case_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Public case manifest must contain JSON objects")

    rewritten: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for row in rows:
        if not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ValueError("Every case requires a non-empty case_id")
        source = Path(str(row.get("program_path", ""))).resolve()
        target_name = safe_name(row["case_id"])
        if target_name in seen_targets:
            raise ValueError(f"Materialized case path collision: {target_name}")
        seen_targets.add(target_name)
        target = output / "programs" / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        runtime = row.get("web_runtime") or {}
        dependency_source = None
        if isinstance(runtime, dict) and runtime.get("dependency_path"):
            dependency_source = Path(str(runtime["dependency_path"])).resolve()
            if not dependency_source.is_dir():
                raise FileNotFoundError(
                    f"Frozen runtime dependencies are missing: {dependency_source}"
                )
            package = dependency_source.parent / "package.json"
            lock = dependency_source.parent / "package-lock.json"
            dependency_manifest = Path(
                str(runtime.get("dependency_manifest_path", ""))
            ).resolve()
            if (
                not package.is_file()
                or hashlib.sha256(package.read_bytes()).hexdigest()
                != runtime.get("dependency_package_sha256")
                or not lock.is_file()
                or hashlib.sha256(lock.read_bytes()).hexdigest()
                != runtime.get("dependency_lock_sha256")
                or not dependency_manifest.is_file()
                or hashlib.sha256(dependency_manifest.read_bytes()).hexdigest()
                != runtime.get("dependency_manifest_sha256")
            ):
                raise ValueError("Frozen runtime dependency hashes do not match the case row")
            verify_dependency_manifest(dependency_source.parent, dependency_manifest)
        resolved_dependency = (
            dependency_source or (source / "node_modules")
        )
        source_dependency_identity = (
            dependency_tree_identity(resolved_dependency.resolve())
            if resolved_dependency.is_dir()
            else None
        )
        linked_dependency = copy_program(
            source,
            target,
            dependency_source=dependency_source,
            copy_dependencies=bool(getattr(args, "copy_dependencies", False)),
        )
        materialized_dependency_identity = None
        if linked_dependency is not None:
            materialized_dependencies = target / "node_modules"
            materialized_dependency_identity = dependency_tree_identity(
                materialized_dependencies.resolve()
                if materialized_dependencies.is_symlink()
                else materialized_dependencies
            )
            if materialized_dependency_identity != source_dependency_identity:
                raise ValueError(
                    f"Materialized dependency tree differs from source: {row['case_id']}"
                )
        copied = dict(row)
        copied["program_path"] = str(target)
        rewritten.append(copied)
        sources.append(
            {
                "case_id": row["case_id"],
                "source_program_path": str(source),
                "materialized_program_path": str(target),
                "source_row_sha256": canonical_sha256(row),
                "materialized_row_sha256": canonical_sha256(copied),
                "dependency_linked": linked_dependency is not None,
                "dependency_source": (
                    str(linked_dependency) if linked_dependency is not None else None
                ),
                "dependency_mode": (
                    "node_local_copy"
                    if linked_dependency is not None
                    and bool(getattr(args, "copy_dependencies", False))
                    else "shared_storage_symlink"
                    if linked_dependency is not None
                    else None
                ),
                "dependency_tree_identity": source_dependency_identity,
            }
        )

    materialized_manifest = output / "cases.jsonl"
    materialized_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rewritten),
        encoding="utf-8",
    )
    materialized_manifest.chmod(0o644)
    provenance = {
        "schema": "multimodalcode-self-verify-materialization-1",
        "source_manifest": str(case_manifest),
        "source_manifest_sha256": hashlib.sha256(
            case_manifest.read_bytes()
        ).hexdigest(),
        "materialized_manifest_sha256": hashlib.sha256(
            materialized_manifest.read_bytes()
        ).hexdigest(),
        "official_evaluator_visible": False,
        "private_files_read": [],
        "cases": sources,
    }
    provenance_path = output / "provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    provenance_path.chmod(0o644)
    output.chmod(0o755)
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

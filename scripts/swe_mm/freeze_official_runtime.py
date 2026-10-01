#!/usr/bin/env python3
"""Create a content-addressed, write-protected SWE-MM task runtime snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = PROJECT_ROOT / "runs" / "swe_mm_official" / "runtime-20260813d"

COPY_PATHS = (
    "agent_run.py",
    "src/multimodalcode",
    "scaffolds/mini_swe_agent/src/minisweagent",
    "scaffolds/mini_swe_agent/UPSTREAM.json",
    "evaluate/swe_mm/upstream",
    "evaluate/swe_mm/UPSTREAM.json",
    "scripts/swe_mm/direct_case_eval.py",
    "scripts/swe_mm/run_official_case.sh",
    "scripts/swe_mm/run_clean_eval_case.sh",
    "scripts/agent_smoke/audit_trajectory.py",
    "configs/swe_mm/official_dev.json",
    "configs/swe_mm/clean_eval.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()
    target = args.target.resolve()
    if target.exists():
        raise SystemExit(f"Refusing to overwrite frozen runtime: {target}")
    target.mkdir(parents=True)

    for relative in COPY_PATHS:
        source = PROJECT_ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        else:
            shutil.copy2(source, destination)

    # The frozen dataset is large and already checksum-pinned. A symlink keeps
    # the runtime code immutable without duplicating 301 task images.
    (target / "data").symlink_to(PROJECT_ROOT / "data", target_is_directory=True)
    files = sorted(path for path in target.rglob("*") if path.is_file() and not path.is_symlink())
    records = [
        {"path": str(path.relative_to(target)), "sha256": sha256(path), "bytes": path.stat().st_size}
        for path in files
    ]
    tree = hashlib.sha256(
        "".join(f"{row['path']}\0{row['sha256']}\n" for row in records).encode()
    ).hexdigest()
    manifest = {
        "schema": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(PROJECT_ROOT),
        "target": str(target),
        "file_count": len(records),
        "tree_sha256": tree,
        "data_symlink": str(PROJECT_ROOT / "data"),
        "files": records,
    }
    manifest_path = target / "runtime_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Tasks only need to read/execute the snapshot. Future source edits cannot
    # race with already-submitted jobs.
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    for path in sorted(
        (item for item in target.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(path.stat().st_mode & ~0o222)
    target.chmod(target.stat().st_mode & ~0o222)
    print(json.dumps({
        "runtime": str(target),
        "manifest": str(manifest_path),
        "file_count": len(records),
        "tree_sha256": tree,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

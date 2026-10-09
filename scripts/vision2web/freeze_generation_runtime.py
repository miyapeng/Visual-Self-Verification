#!/usr/bin/env python3
"""Freeze the code used by long-running Vision2Web generation jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = PROJECT_ROOT / "runs" / "vision2web_generation" / "runtime-20260816b"
INCLUDE = (
    Path("scripts/agents/run.py"),
    Path("src/multimodalcode"),
    Path("scaffolds/openhands"),
    Path("evaluate/vision2web"),
    Path("scripts/agent_smoke/audit_trajectory.py"),
    Path("scripts/vision2web/run_generation_case.sh"),
    Path("scripts/vision2web/litellm_output_token_cap.py"),
    Path("scripts/vision2web/probe_claude_code_runtime.py"),
    Path("scripts/vision2web/write_litellm_proxy_config.py"),
)


def ignored(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == "runtime_manifest.json" or relative == "data":
            continue
        digest.update(relative.encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()
    target = args.target.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite frozen runtime: {target}")
    target.mkdir(parents=True)
    for relative in INCLUDE:
        source = PROJECT_ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, ignore=ignored)
        else:
            shutil.copy2(source, destination)
    (target / "data").symlink_to(PROJECT_ROOT / "data", target_is_directory=True)
    manifest = {
        "schema": "multimodalcode-frozen-runtime-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(PROJECT_ROOT),
        "target": str(target),
        "included": [str(path) for path in INCLUDE],
        "data_symlink": str(PROJECT_ROOT / "data"),
        "tree_sha256": tree_digest(target),
    }
    (target / "runtime_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

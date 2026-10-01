#!/usr/bin/env python3
"""Verify deterministic hashes for the two in-repository agent runtimes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    paths = (
        item
        for item in root.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.parts
        and item.suffix not in {".pyc", ".pyo"}
    )
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def verify(name: str) -> dict[str, str | bool]:
    root = PROJECT_ROOT / "scaffolds" / name
    manifest = json.loads((root / "UPSTREAM.json").read_text(encoding="utf-8"))
    expected = manifest["source_tree_sha256"]
    actual = tree_hash(root / "src")
    return {"name": name, "expected": expected, "actual": actual, "ok": actual == expected}


def main() -> int:
    results = [verify("mini_swe_agent"), verify("openhands")]
    print(json.dumps(results, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

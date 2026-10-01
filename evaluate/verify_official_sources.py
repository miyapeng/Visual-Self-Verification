#!/usr/bin/env python3
"""Verify that frozen evaluator sources match their recorded SHA-256 lists."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EVALUATE_ROOT = Path(__file__).resolve().parent
BENCHMARKS = (
    "chartmimic",
    "vision2web",
    "swe_mm",
    "frontalk",
    "interactweb_bench",
)


def verify(name: str) -> dict:
    root = EVALUATE_ROOT / name
    metadata = json.loads((root / "UPSTREAM.json").read_text(encoding="utf-8"))
    manifest = root / metadata["file_manifest"]
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    failures = []
    expected_paths = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split(maxsplit=1)
        relative = relative.removeprefix("*").removeprefix("./")
        expected_paths.add(relative)
        path = root / "upstream" / relative
        if not path.is_file():
            failures.append({"path": relative, "error": "missing"})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append({"path": relative, "expected": expected, "actual": actual})
    actual_paths = {
        path.relative_to(root / "upstream").as_posix()
        for path in (root / "upstream").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    for relative in sorted(actual_paths - expected_paths):
        failures.append({"path": relative, "error": "unexpected"})
    return {
        "benchmark": name,
        "commit": metadata["source_commit"],
        "files": len(expected_paths),
        "manifest_ok": manifest_digest == metadata["file_manifest_sha256"],
        "failures": failures,
        "ok": not failures and manifest_digest == metadata["file_manifest_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmarks", nargs="*")
    args = parser.parse_args()
    names = args.benchmarks or BENCHMARKS
    unknown = sorted(set(names) - set(BENCHMARKS))
    if unknown:
        parser.error(f"unknown benchmark(s): {', '.join(unknown)}")
    results = [verify(name) for name in names]
    print(json.dumps(results, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

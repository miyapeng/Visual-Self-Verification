#!/usr/bin/env python3
"""Thin command adapter for the frozen, unmodified InteractWeb-Bench release."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
UPSTREAM = ROOT / "upstream"
DATA_ROOT = PROJECT_ROOT / "data" / "interactweb_bench"
FULL_DATA = DATA_ROOT / "all.jsonl"
MINI_DATA = DATA_ROOT / "test_mini.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "interactweb_bench"

COMMANDS = {
    "run": UPSTREAM / "src" / "experiment" / "run_simulation.py",
    "run-mini": UPSTREAM / "src" / "experiment" / "run_simulation.py",
    "analyze": UPSTREAM / "src" / "experiment" / "result_analyze.py",
    "intent-eval": UPSTREAM / "src" / "experiment" / "evaluate_intent_and_ask.py",
    "aesthetics": UPSTREAM / "src" / "experiment" / "evaluate_artimuse_api.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def preflight() -> dict:
    rows = _load_jsonl(FULL_DATA)
    mini = _load_jsonl(MINI_DATA)
    personas = Counter(row.get("persona") for row in rows)
    difficulties = Counter(row.get("difficulty") for row in rows)
    seed_ids = {row.get("original_id") for row in rows}
    required = list(COMMANDS.values()) + [UPSTREAM / "Dockerfile", UPSTREAM / "requirements.txt"]
    report = {
        "benchmark": "interactweb_bench",
        "upstream_root": str(UPSTREAM),
        "full_data_path": str(FULL_DATA),
        "mini_data_path": str(MINI_DATA),
        "tasks": len(rows),
        "seed_tasks": len(seed_ids),
        "mini_tasks": len(mini),
        "personas": dict(sorted(personas.items())),
        "difficulties": dict(sorted(difficulties.items())),
        "full_data_sha256": _sha256(FULL_DATA),
        "upstream_data_sha256": _sha256(UPSTREAM / "data" / "all.jsonl"),
        "missing": [str(path) for path in required if not path.exists()],
    }
    report["ok"] = (
        not report["missing"]
        and report["tasks"] == 404
        and report["seed_tasks"] == 101
        and report["mini_tasks"] == 1
        and set(personas.values()) == {101}
        and report["full_data_sha256"] == report["upstream_data_sha256"]
        == "f6f3b9bc94d509b882fa6c9f0ffd2da066387141168ba06771185f47112a7ded"
    )
    return report


def _has_option(arguments: list[str], option: str) -> bool:
    return option in arguments or any(arg.startswith(option + "=") for arg in arguments)


def build_command(command: str, arguments: list[str]) -> list[str]:
    if command not in COMMANDS:
        raise ValueError(f"unknown InteractWeb-Bench command: {command}")
    forwarded = list(arguments)
    if command in {"run", "run-mini"}:
        if not _has_option(forwarded, "--config") and not _has_option(forwarded, "--data_path"):
            forwarded.extend(("--data_path", str(MINI_DATA if command == "run-mini" else FULL_DATA)))
        if not _has_option(forwarded, "--config") and not _has_option(forwarded, "--output_dir"):
            forwarded.extend(("--output_dir", str(DEFAULT_OUTPUT)))
    elif command == "intent-eval" and not _has_option(forwarded, "--data_path"):
        forwarded.extend(("--data_path", str(FULL_DATA)))
    return [sys.executable, str(COMMANDS[command]), *forwarded]


def usage() -> str:
    commands = "|".join(COMMANDS)
    return f"usage: {Path(sys.argv[0]).name} preflight | <{commands}> [official arguments...]"


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(usage())
        return 0
    command = arguments.pop(0)
    if command == "preflight":
        report = preflight()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    print_only = "--print-command" in arguments
    arguments = [arg for arg in arguments if arg != "--print-command"]
    try:
        child = build_command(command, arguments)
    except ValueError as error:
        print(error, file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    if print_only:
        print(json.dumps({"cwd": str(UPSTREAM), "command": child}, indent=2))
        return 0
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    previous = env.get("PYTHONPATH")
    source_root = str(UPSTREAM / "src")
    env["PYTHONPATH"] = source_root if not previous else os.pathsep.join((source_root, previous))
    return subprocess.run(child, cwd=UPSTREAM, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

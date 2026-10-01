#!/usr/bin/env python3
"""Thin command adapter for the frozen, unmodified FronTalk release."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
UPSTREAM = ROOT / "upstream"
FROZEN_DATA = PROJECT_ROOT / "data" / "frontalk" / "data.jsonl"

COMMANDS = {
    "infer-text": "infer_multiturn_textual.py",
    "infer-visual": "infer_multiturn_visual.py",
    "infer-acecoder-text": "infer_acecoder_textual.py",
    "infer-acecoder-visual": "infer_acecoder_visual.py",
    "evaluate": "evaluate_all.py",
    "usability": "usability.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight() -> dict:
    upstream_data = UPSTREAM / "data.jsonl"
    rows = [json.loads(line) for line in FROZEN_DATA.read_text(encoding="utf-8").splitlines() if line]
    turns = sum(len(row["cases"]) for row in rows)
    tests = sum(len(case["test_conditions"]) for row in rows for case in row["cases"])
    required = [UPSTREAM / script for script in COMMANDS.values()]
    required.extend((UPSTREAM / "webvoyager" / "run_evaluate.py", UPSTREAM / "outputs_comparison_ref"))
    report = {
        "benchmark": "frontalk",
        "upstream_root": str(UPSTREAM),
        "data_path": str(FROZEN_DATA),
        "dialogues": len(rows),
        "turns": turns,
        "test_conditions": tests,
        "data_sha256": _sha256(FROZEN_DATA),
        "upstream_data_sha256": _sha256(upstream_data),
        "missing": [str(path) for path in required if not path.exists()],
    }
    report["ok"] = (
        not report["missing"]
        and report["dialogues"] == 100
        and report["turns"] == 1000
        and report["test_conditions"] == 3676
        and report["data_sha256"] == report["upstream_data_sha256"]
        == "81a9cf5a739fb4d7245fd72f775126f9cca34e61f7ac0f9c9212e08db8837137"
    )
    return report


def build_command(command: str, arguments: list[str], invocation_cwd: Path) -> list[str]:
    if command not in COMMANDS:
        raise ValueError(f"unknown FronTalk command: {command}")
    forwarded = list(arguments)
    if forwarded and not forwarded[0].startswith("-"):
        path = Path(forwarded[0]).expanduser()
        if not path.is_absolute():
            path = invocation_cwd / path
        forwarded[0] = str(path.resolve())
    return [sys.executable, str(UPSTREAM / COMMANDS[command]), *forwarded]


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
        child = build_command(command, arguments, Path.cwd())
    except ValueError as error:
        print(error, file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    if print_only:
        print(json.dumps({"cwd": str(UPSTREAM), "command": child}, indent=2))
        return 0
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(child, cwd=UPSTREAM, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Record Claude Code/playwright-cli capabilities in the pinned case image."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def command_record(arguments: list[str]) -> dict[str, object]:
    binary = shutil.which(arguments[0])
    if not binary:
        return {"command": arguments, "available": False}
    completed = subprocess.run(
        [binary, *arguments[1:]],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    return {
        "command": [binary, *arguments[1:]],
        "available": True,
        "returncode": completed.returncode,
        "output": completed.stdout[:20000],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    home = Path.home()
    skill_roots = [home / ".claude", home / ".agents"]
    skill_files: list[str] = []
    for root in skill_roots:
        if root.is_dir():
            skill_files.extend(
                str(path)
                for path in sorted(root.rglob("*"))
                if path.is_file()
                and ("playwright" in path.as_posix().casefold() or path.name == "SKILL.md")
            )
    claude_help = command_record(["claude", "--help"])
    record = {
        "schema": "multimodalcode-vision2web-claude-runtime-probe-2",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "claude": command_record(["claude", "--version"]),
        "claude_help": claude_help,
        "claude_resume_supported": "--resume" in str(claude_help.get("output", "")),
        "playwright_cli_version": command_record(["playwright-cli", "--version"]),
        "playwright_cli_help": command_record(["playwright-cli", "--help"]),
        "discovered_skill_files": skill_files,
        "inference_performed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if (
        record["claude"]["available"]
        and record["claude_resume_supported"]
        and record["playwright_cli_version"]["available"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

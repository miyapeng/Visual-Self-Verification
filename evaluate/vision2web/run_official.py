#!/usr/bin/env python3
"""Forward to the frozen, unmodified Vision2Web evaluation CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(UPSTREAM) if not previous else os.pathsep.join((str(UPSTREAM), previous))
    command = [sys.executable, "-m", "vision2web.cli", "evaluate", *arguments]
    return subprocess.run(command, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

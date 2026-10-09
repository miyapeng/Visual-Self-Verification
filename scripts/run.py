"""Repository-local command-line entry point.

Usage:
    python scripts/run.py design2code web2code [options]
    python scripts/run.py generate BENCHMARK [options]
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from multimodalcode.cli import main  # noqa: E402


if __name__ == "__main__":
    arguments = sys.argv[1:]
    explicit_commands = {
        "list",
        "inspect",
        "doctor",
        "generate",
        "render",
        "evaluate",
        "run",
    }
    if not arguments or arguments in (["-h"], ["--help"]):
        arguments = ["run", "--help"]
    elif arguments[0] not in explicit_commands and arguments[0] != "--config":
        arguments.insert(0, "run")
    elif (
        len(arguments) >= 3
        and arguments[0] == "--config"
        and arguments[2] not in explicit_commands
    ):
        arguments.insert(2, "run")
    raise SystemExit(main(arguments))

#!/usr/bin/env python3
"""Plain-Python entry point for coding-agent benchmark runs."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from multimodalcode.agent_harness.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

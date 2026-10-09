#!/usr/bin/env python3
"""Run the existing research workflow."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from multimodalcode.research.self_verify_cli import main

if __name__ == '__main__':
    raise SystemExit(main())

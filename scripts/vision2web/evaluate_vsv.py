#!/usr/bin/env python3
"""Execute one or more prepared verification evaluations."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from multimodalcode.vsv_eval.system import run_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Validate inputs without API calls or replay')
    args = parser.parse_args()
    return run_experiment(args.manifest, args.output_dir, check_only=args.check_only)


if __name__ == '__main__':
    raise SystemExit(main())

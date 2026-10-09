#!/usr/bin/env python3
"""Restore explicitly selected file mutations from a canonical trajectory."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from multimodalcode.io import read_json
from multimodalcode.vsv_eval.version_restore import restore_versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-json', required=True)
    parser.add_argument('--plan', required=True, help='Audited source root, base snapshot and mutation IDs')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    result = restore_versions(args.run_json, read_json(args.plan), args.output_dir)
    print(f"Restored {len(result['versions'])} checkpoints; skipped {len(result['skipped_failed_event_ids'])} failed mutations.")


if __name__ == '__main__':
    main()

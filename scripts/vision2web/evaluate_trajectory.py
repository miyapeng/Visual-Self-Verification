#!/usr/bin/env python3
"""One entry for Test, Visual Judgment and audited Safe Repair; read-only reuse by default."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from multimodalcode.vsv_eval.pipeline import evaluate_pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Pipeline manifest; data paths are relative to project root")
    parser.add_argument("--output-dir", type=Path, required=True, help="New empty destination; never overwrites old results")
    parser.add_argument("--run-missing", action="store_true", help="Run configured missing stages via existing CLIs; may use judge API and browser")
    args = parser.parse_args()
    result = evaluate_pipeline(args.config, args.output_dir, ROOT, run_missing=args.run_missing)
    print(json.dumps({"execution_status": result["execution_status"], "research_status": result["research_status"],
                      "stages": {k: v["status"] for k, v in result["stages"].items()},
                      "scores": str(args.output_dir.resolve() / "scores.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a trusted post-hoc Design2Code manifest from completed agent runs."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def load_reference(dataset: Path, case_id: str) -> str:
    with dataset.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row["id"]) == case_id:
                return str(row["reference"])
    raise KeyError(f"Design2Code case not found: {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="append", nargs=2, metavar=("MODEL", "RUN_DIR"), required=True)
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    work = args.output.resolve()
    refs = work / "references"
    preds = work / "predictions"
    refs.mkdir(parents=True, exist_ok=True)
    preds.mkdir(parents=True, exist_ok=True)
    reference = load_reference(project / "data" / "design2code" / "data.jsonl", args.case)
    reference_path = refs / f"case-{args.case}.html"
    reference_path.write_text(reference, encoding="utf-8")

    rick = project / "evaluate" / "design2code" / "Design2Code" / "prompting" / "rick.jpg"
    if rick.is_file():
        shutil.copy2(rick, refs / "rick.jpg")
        shutil.copy2(rick, preds / "rick.jpg")

    manifest = []
    for model, run_dir_text in args.run:
        run_dir = Path(run_dir_text).resolve()
        prediction = run_dir / "workspace" / "index.html"
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        if result.get("status") != "success" or not prediction.is_file():
            raise RuntimeError(f"run is not a successful Design2Code output: {run_dir}")
        safe_model = "".join(character if character.isalnum() else "_" for character in model)
        prediction_path = preds / f"{safe_model}-case-{args.case}.html"
        shutil.copy2(prediction, prediction_path)
        manifest.append(
            {
                "key": f"{model}::{args.case}",
                "item_id": args.case,
                "sample_index": 0,
                "reference_html": str(reference_path),
                "prediction_html": str(prediction_path),
            }
        )

    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

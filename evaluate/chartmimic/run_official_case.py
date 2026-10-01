#!/usr/bin/env python3
"""Run one case through ChartMimic's unmodified low-level evaluators."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"


def require_official_environment() -> None:
    mpl_config = Path(tempfile.gettempdir()) / "mmcode-chartmimic-mpl"
    mpl_config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))
    import matplotlib
    from matplotlib.axes._base import _process_plot_var_args

    if matplotlib.__version__ != "3.8.4" or not hasattr(_process_plot_var_args, "_makeline"):
        raise RuntimeError(
            "The frozen ChartMimic evaluator requires its released environment "
            "(Python 3.9, matplotlib==3.8.4). Do not downgrade mmcode; invoke this "
            "script with a separate ChartMimic evaluator environment."
        )


def evaluate(candidate: Path, reference: Path) -> dict:
    require_official_environment()
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="chartmimic-official-") as temporary:
        runtime = Path(temporary)
        (runtime / "chart2code").symlink_to(UPSTREAM / "chart2code", target_is_directory=True)
        (runtime / "eval_configs").symlink_to(UPSTREAM / "eval_configs", target_is_directory=True)
        candidate_dir = runtime / "candidate"
        reference_dir = runtime / "reference"
        candidate_dir.mkdir()
        reference_dir.mkdir()
        candidate_copy = candidate_dir / candidate.name
        reference_copy = reference_dir / reference.name
        shutil.copy2(candidate, candidate_copy)
        shutil.copy2(reference, reference_copy)

        os.environ["PROJECT_PATH"] = str(runtime)
        os.environ["MPLCONFIGDIR"] = str(runtime / ".matplotlib")
        os.environ["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"]))
        sys.path.insert(0, str(UPSTREAM / "chart2code"))
        sys.path.insert(0, str(runtime))

        from utils.evaluator.chart_type_evaluator import ChartTypeEvaluator
        from utils.evaluator.color_evaluator import ColorEvaluator
        from utils.evaluator.layout_evaluator import LayoutEvaluator
        from utils.evaluator.text_evaluator import TextEvaluator

        old_cwd = Path.cwd()
        os.chdir(runtime)
        try:
            evaluators = [
                ("text_metrics", TextEvaluator(use_position=False, use_axs=False)),
                ("chart_type_metrics", ChartTypeEvaluator()),
                ("color_metrics", ColorEvaluator()),
                ("layout_metrics", LayoutEvaluator()),
            ]
            result = {}
            for name, evaluator in evaluators:
                evaluator(str(candidate_copy), str(reference_copy))
                result[name] = evaluator.metrics
        finally:
            os.chdir(old_cwd)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    reference = args.reference.resolve()
    if not candidate.is_file() or not reference.is_file():
        raise FileNotFoundError(f"Missing candidate/reference: {candidate}, {reference}")
    result = {
        "protocol": "ChartMimic Code4Evaluation",
        "upstream_commit": "92ba5b97b908c607f630c3ffbb5043de9de62b76",
        "candidate": str(candidate),
        "reference": str(reference),
        **evaluate(candidate, reference),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Analyze a Vision2Web paper-protocol run with the frozen official analyzer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPER_UPSTREAM = PROJECT_ROOT / "evaluate/vision2web/paper_upstream"
DEFAULT_DATASETS = PROJECT_ROOT / "data/vision2web/extracted"
sys.path.insert(0, str(PAPER_UPSTREAM))

from vision2web.analyze.analyzer import ResultAnalyzer  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def average(values: list[float | int]) -> float:
    return sum(values) / len(values) if values else 0.0


def task_row(statistics: dict[str, Any], task: str, framework: str, model: str) -> dict[str, Any]:
    row = statistics.get(f"{task}:{framework}:{model}", {})
    return {
        "projects": len(row.get("projects", [])),
        "evaluated_projects": int(row.get("success_count", 0)),
        "visual_score": average(row.get("prototype_scores", [])) * 100,
        "functional_score": average(row.get("function_scores", [])) * 100,
        "desktop": average(row.get("desktop_scores", [])) * 100,
        "tablet": average(row.get("tablet_scores", [])) * 100,
        "mobile": average(row.get("mobile_scores", [])) * 100,
        "prototype_nodes": len(row.get("prototype_scores", [])),
        "functional_nodes": len(row.get("function_scores", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--framework", default="openhands")
    parser.add_argument("--model", default="Qwen3.5-9B")
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    results_root = output_root / "paper_results"
    datasets_dir = args.datasets_dir.resolve()
    expected = {
        (task, project.name)
        for task in ("webpage", "frontend", "website")
        for project in (datasets_dir / task).iterdir()
        if project.is_dir() and not project.name.startswith(".")
    }
    actual = {
        (task, project.name)
        for task in ("webpage", "frontend", "website")
        for project in (results_root / task / args.framework / args.model).iterdir()
        if project.is_dir()
    }
    if expected != actual:
        raise RuntimeError(
            "paper result inventory is incomplete: "
            f"missing={sorted(expected - actual)[:20]}, extra={sorted(actual - expected)[:20]}"
        )

    analyzer = ResultAnalyzer(results_dir=str(results_root))
    analysis = analyzer.analyze(datasets_dir=str(datasets_dir))
    statistics = analysis["statistics"]
    l1 = task_row(statistics, "webpage", args.framework, args.model)
    l2 = task_row(statistics, "frontend", args.framework, args.model)
    l3 = task_row(statistics, "website", args.framework, args.model)
    functional_values = []
    for row in (l2, l3):
        functional_values.extend(
            statistics.get(
                f"{'frontend' if row is l2 else 'website'}:{args.framework}:{args.model}",
                {},
            ).get("function_scores", [])
        )
    summary = {
        "schema": "multimodalcode-vision2web-paper-score-1",
        "model": args.model,
        "framework": args.framework,
        "dataset_tasks": len(expected),
        "evaluated_projects": sum(row["evaluated_projects"] for row in (l1, l2, l3)),
        "zero_projects": len(expected) - sum(row["evaluated_projects"] for row in (l1, l2, l3)),
        "evaluator": {
            "source_commit": "3111cc312a2ff86d764d93477cb7c17205153e50",
            "functional_protocol": "WebVoyager-style GUIAgentTester",
            "functional_model": "glm-4.6v",
            "visual_model": None,
            "functional_paper_exact": True,
            "visual_paper_exact": False,
            "paper_visual_model": "gemini-3-pro-preview",
            "mode": "functional-only",
        },
        "level1": {**l1, "functional_score": None, "visual_score": None},
        "level2": {**l2, "visual_score": None},
        "level3": {**l3, "visual_score": None},
        "pooled_functional_score": average(functional_values) * 100,
        "overall": None,
    }
    write_json(output_root / "analysis.json", analysis)
    write_json(output_root / "score_summary.json", summary)
    markdown = f"""# Vision2Web score: {args.model} ({args.framework})

Evaluator: paper commit `3111cc3` and the paper's GLM-4.6V functional verifier.
This is an explicit functional-only run: no Gemini request was made. Vision2Web
visual and overall scores are therefore intentionally not reported.

| Split | Projects | Evaluated | Functional score |
|---|---:|---:|---:|
| Level 1 | {l1['projects']} | {l1['evaluated_projects']} | N/A (visual-only split) |
| Level 2 | {l2['projects']} | {l2['evaluated_projects']} | {l2['functional_score']:.2f} |
| Level 3 | {l3['projects']} | {l3['evaluated_projects']} | {l3['functional_score']:.2f} |
| L2+L3 pooled test cases | — | — | {summary['pooled_functional_score']:.2f} |

Generation or evaluation failures remain in the denominator as zero. The
complete frozen official-analyzer output is stored in `analysis.json`. A paper
overall score requires visual evaluation and is deliberately absent.
"""
    (output_root / "SCORE.md").write_text(markdown, encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

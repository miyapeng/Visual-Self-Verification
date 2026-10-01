#!/usr/bin/env python3
"""Validate ChartMimic data and create aliases for the pristine evaluator."""

from __future__ import annotations

import argparse
from pathlib import Path


EXPECTED = {
    "direct_1800": 1800,
    "direct_600": 600,
    "customized_1800": 1800,
    "customized_600": 600,
}


def validate_data(data_root: Path) -> None:
    for split, expected_count in EXPECTED.items():
        split_root = data_root / split
        if not split_root.is_dir():
            raise RuntimeError(f"Missing ChartMimic split: {split_root}")
        stems = {
            suffix: {
                path.relative_to(split_root).with_suffix("").as_posix()
                for path in split_root.rglob(f"*{suffix}")
            }
            for suffix in (".py", ".pdf", ".png")
        }
        if any(len(items) != expected_count for items in stems.values()):
            counts = {suffix: len(items) for suffix, items in stems.items()}
            raise RuntimeError(f"Incomplete {split}: {counts}, expected {expected_count}")
        if not (stems[".py"] == stems[".pdf"] == stems[".png"]):
            raise RuntimeError(f"Mismatched .py/.pdf/.png case IDs in {split}")


def ensure_link(link: Path, target: Path) -> Path:
    if link.is_symlink():
        if link.resolve() != target.resolve():
            raise RuntimeError(
                f"Existing link points to {link.resolve()}, expected {target}"
            )
        return link
    if link.exists():
        raise RuntimeError(f"Refusing to replace existing non-symlink path: {link}")
    link.symlink_to(target.resolve(), target_is_directory=True)
    return link


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=project_root / "data/chartmimic/extracted",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=project_root / "evaluate/chartmimic/runtime",
    )
    args = parser.parse_args()
    data_root = args.data_root.resolve()
    upstream_root = project_root / "evaluate/chartmimic/upstream"
    runtime_root = args.runtime_root.resolve()
    validate_data(data_root)
    runtime_root.mkdir(parents=True, exist_ok=True)
    dataset_root = runtime_root / "dataset"
    dataset_root.mkdir(exist_ok=True)
    ensure_link(runtime_root / "chart2code", upstream_root / "chart2code")
    ensure_link(runtime_root / "eval_configs", upstream_root / "eval_configs")
    links = {
        "ori_500": data_root / "direct_600",
        "customized_500": data_root / "customized_600",
        "direct_600": data_root / "direct_600",
        "customized_600": data_root / "customized_600",
    }
    for name, target in links.items():
        ensure_link(dataset_root / name, target)
    print(f"ChartMimic data verified: {sum(EXPECTED.values())} cases")
    print(f"Pristine official source: {upstream_root}")
    print(f"Compatibility runtime: {runtime_root}")
    for name in links:
        link = dataset_root / name
        print(f"Dataset alias: {link} -> {link.resolve()}")


if __name__ == "__main__":
    main()

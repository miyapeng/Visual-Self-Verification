from __future__ import annotations

import argparse
import json
import os
import sys
import types
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List


def _ensure_pkg_resources_compat() -> None:
    """Support openai-clip 1.0.1 after pkg_resources was removed."""
    try:
        import pkg_resources  # noqa: F401
    except ModuleNotFoundError:
        import packaging

        compatibility = types.ModuleType("pkg_resources")
        compatibility.packaging = packaging
        sys.modules["pkg_resources"] = compatibility


def _load_visual_eval(upstream: Path):
    """Load upstream metric, repairing its released indentation typo in memory."""
    _ensure_pkg_resources_compat()
    module_name = "Design2Code.metrics.visual_score"
    try:
        from Design2Code.metrics.visual_score import visual_eval_v3_multi

        return visual_eval_v3_multi
    except IndentationError:
        source_path = upstream / "Design2Code" / "metrics" / "visual_score.py"
        source = source_path.read_text(encoding="utf-8")
        broken = """    for k, predict_blocks in enumerate(predict_blocks_list):
         if len(predict_blocks) == 0:
                print("[Warning] No detected blocks in: ", predict_img_list[k])
                final_clip_score = calculate_clip_similarity_with_blocks(predict_img_list[k], original_img, predict_blocks, original_blocks)
                return_score_list.append([0.0, 0.2 * final_clip_score, (0.0, 0.0, 0.0, 0.0, final_clip_score)])
                continue
            elif len(original_blocks) == 0:
                print("[Warning] No detected blocks in: ", original_img)
                final_clip_score = calculate_clip_similarity_with_blocks(predict_img_list[k], original_img, predict_blocks, original_blocks)
                return_score_list.append([0.0, 0.2 * final_clip_score, (0.0, 0.0, 0.0, 0.0, final_clip_score)])
                continue
"""
        repaired = """    for k, predict_blocks in enumerate(predict_blocks_list):
        if len(predict_blocks) == 0:
            print("[Warning] No detected blocks in: ", predict_img_list[k])
            final_clip_score = calculate_clip_similarity_with_blocks(predict_img_list[k], original_img, predict_blocks, original_blocks)
            return_score_list.append([0.0, 0.2 * final_clip_score, (0.0, 0.0, 0.0, 0.0, final_clip_score)])
            continue
        elif len(original_blocks) == 0:
            print("[Warning] No detected blocks in: ", original_img)
            final_clip_score = calculate_clip_similarity_with_blocks(predict_img_list[k], original_img, predict_blocks, original_blocks)
            return_score_list.append([0.0, 0.2 * final_clip_score, (0.0, 0.0, 0.0, 0.0, final_clip_score)])
            continue
"""
        if broken not in source:
            raise RuntimeError(
                "Upstream Design2Code metric has an unknown syntax error; "
                "the known indentation compatibility patch did not match."
            )
        source = source.replace(broken, repaired, 1)
        module = types.ModuleType(module_name)
        module.__file__ = str(source_path)
        module.__package__ = "Design2Code.metrics"
        sys.modules[module_name] = module
        exec(compile(source, str(source_path), "exec"), module.__dict__)
        return module.visual_eval_v3_multi


def evaluate_one(upstream_root: str, row: Dict[str, Any]) -> Dict[str, Any]:
    upstream = Path(upstream_root).resolve()
    package_workdir = upstream / "Design2Code"
    sys.path.insert(0, str(upstream))
    # Upstream launches screenshot helpers with the bare command `python3`.
    # Keep those subprocesses in this evaluator's environment; ClusterX's
    # system Python does not contain the frozen Playwright dependency.
    python_bin = str(Path(sys.executable).resolve().parent)
    os.environ["PATH"] = os.pathsep.join((python_bin, os.environ.get("PATH", "")))
    previous_workdir = Path.cwd()
    try:
        os.chdir(package_workdir)
        visual_eval_v3_multi = _load_visual_eval(upstream)
        result = visual_eval_v3_multi(
            [[row["prediction_html"]], row["reference_html"]], debug=False
        )[0]
    finally:
        os.chdir(previous_workdir)
    _matched, final_score, metrics = result
    block_match, text, position, color, clip = metrics
    return {
        "key": row["key"],
        "item_id": row["item_id"],
        "sample_index": row["sample_index"],
        "profile": "design2code_official",
        "status": "ok",
        "score": float(final_score),
        "score_normalized": float(final_score),
        "block_match": float(block_match),
        "text": float(text),
        "position": float(position),
        "color": float(color),
        "clip": float(clip),
    }


def _read_checkpoint(path: Path) -> Dict[str, Dict[str, Any]]:
    """Return the latest durable result for every manifest key.

    A process can be interrupted in the middle of its final append.  Ignore a
    malformed trailing line so all earlier completed cases remain resumable.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("key"):
            latest[str(row["key"])] = row
    return latest


def _append_checkpoint(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _error_result(row: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
    return {
        "key": row["key"],
        "item_id": row["item_id"],
        "sample_index": row["sample_index"],
        "profile": "design2code_official",
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    args.upstream_root = str(Path(args.upstream_root).resolve())
    rows = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    output_path = Path(args.output)
    checkpoint_path = output_path.with_name(f"{output_path.stem}.partial.jsonl")
    latest = _read_checkpoint(checkpoint_path)
    completed = {
        key for key, row in latest.items() if row.get("status") == "ok"
    }
    pending = [row for row in rows if str(row["key"]) not in completed]
    if completed:
        print(
            f"[design2code] resume {len(completed)}/{len(rows)}; "
            f"pending {len(pending)}",
            flush=True,
        )

    def record(result: Dict[str, Any]) -> None:
        latest[str(result["key"])] = result
        _append_checkpoint(checkpoint_path, result)

    if args.workers <= 1:
        for index, row in enumerate(pending, len(completed) + 1):
            try:
                result = evaluate_one(args.upstream_root, row)
            except Exception as exc:
                result = _error_result(row, exc)
            record(result)
            print(
                f"[design2code] {index}/{len(rows)} {row['key']} "
                f"{result['status']}",
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(evaluate_one, args.upstream_root, row): row
                for row in pending
            }
            for index, future in enumerate(
                as_completed(futures), len(completed) + 1
            ):
                row = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _error_result(row, exc)
                record(result)
                print(
                    f"[design2code] {index}/{len(rows)} {row['key']} "
                    f"{result['status']}",
                    flush=True,
                )

    results: List[Dict[str, Any]] = []
    for manifest_row in rows:
        result = latest.get(str(manifest_row["key"]))
        if result is None:
            result = _error_result(
                manifest_row,
                RuntimeError("No evaluation result was recorded"),
            )
        results.append(result)
    valid = [row for row in results if row.get("status") == "ok"]
    metric_names = ("score", "block_match", "text", "position", "color", "clip")
    summary: Dict[str, Any] = {
        "profile": "design2code_official",
        "protocol": "Design2Code Block-Match/Text/Position/Color/CLIP",
        "total": len(results),
        "valid": len(valid),
        "failed": len(results) - len(valid),
    }
    for metric in metric_names:
        summary[metric] = (
            sum(float(row[metric]) for row in valid) / len(valid)
            if valid
            else None
        )
    output_path.write_text(
        json.dumps({"summary": summary, "rows": results}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

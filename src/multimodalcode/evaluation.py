from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .backends import ModelBackend
from .io import append_jsonl, read_json, safe_name, write_json
from .judge_profiles import parse_profile, profile_prompt
from .schema import GenerationRequest


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _latest_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the newest JSONL record for each resumable sample."""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("key", ""))
        profile = str(row.get("profile", ""))
        latest[(profile, key)] = row
    return list(latest.values())


def _successful_renders(run_dir: Path) -> List[Dict[str, Any]]:
    return [
        row
        for row in _latest_rows(_read_rows(run_dir / "renders.jsonl"))
        if row.get("status") == "ok"
    ]


def _summary_from_scores(
    profile: str, rows: List[Dict[str, Any]], protocol: str
) -> Dict[str, Any]:
    valid = [row for row in rows if row.get("status") == "ok"]
    summary: Dict[str, Any] = {
        "profile": profile,
        "protocol": protocol,
        "total": len(rows),
        "valid": len(valid),
        "failed": len(rows) - len(valid),
    }
    if valid:
        summary["score"] = sum(float(row["score"]) for row in valid) / len(valid)
        summary["score_normalized"] = (
            sum(float(row["score_normalized"]) for row in valid) / len(valid)
        )
    if profile == "web2code_vlm" and valid:
        for key in (
            "visual_structure_and_alignment",
            "color_and_aesthetic_design",
            "textual_content_consistency",
            "user_interface_and_interactivity",
        ):
            summary[key] = sum(float(row[key]) for row in valid) / len(valid)
    return summary


def evaluate_vlm_judge(
    run_dir: Path,
    profile: str,
    backend: ModelBackend,
    *,
    judge_model: str,
    workers: int,
    max_tokens: int,
    temperature: float,
    retries: int,
    overwrite: bool,
) -> Dict[str, Any]:
    score_path = run_dir / "scores.jsonl"
    existing = set()
    if score_path.exists() and not overwrite:
        for row in _read_rows(score_path):
            if row.get("status") == "ok" and row.get("profile") == profile:
                existing.add(row["key"])
    jobs = [row for row in _successful_renders(run_dir) if row["key"] not in existing]
    if not backend.thread_safe:
        workers = 1
    lock = threading.Lock()

    def judge(row: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "key": row["key"],
            "benchmark": row["benchmark"],
            "item_id": row["item_id"],
            "sample_index": row["sample_index"],
            "profile": profile,
            "judge_model": judge_model,
        }
        request = GenerationRequest(
            prompt=profile_prompt(profile),
            image_paths=[row["reference_image"], row["rendered_path"]],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        last_error = None
        for _attempt in range(retries + 1):
            try:
                response = backend.generate(request)
                result.update(parse_profile(profile, response))
                result["judge_response"] = response
                result["status"] = "ok"
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        result["status"] = "error"
        result["error"] = last_error
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(judge, row) for row in jobs]
        for future in as_completed(futures):
            result = future.result()
            with lock:
                append_jsonl(score_path, result)
                print(f"[judge] {result['key']} {result['status']}", flush=True)
    all_rows = _latest_rows(
        [
            row
            for row in _read_rows(score_path)
            if row.get("profile") == profile
        ]
    )
    protocol = (
        "UI2Code_N two-image 0-100 VLM judge"
        if profile == "ui2code_vlm"
        else "Web2Code ten-criterion VLM judge"
    )
    return _summary_from_scores(profile, all_rows, protocol)


def _react_feature_counts(code: str) -> Dict[str, int]:
    hooks = len(re.findall(r"\buse[A-Z][A-Za-z0-9_]*\s*\(", code))
    events = len(re.findall(r"\bon[A-Z][A-Za-z0-9_]*\s*=", code))
    updates = len(
        re.findall(
            r"\bon[A-Z][A-Za-z0-9_]*\s*=\s*\{[^}]*\b(?:set[A-Z]\w*|dispatch)\s*\(",
            code,
            re.DOTALL,
        )
    )
    components = len(
        re.findall(r"\b(?:function|class|const|let|var)\s+[A-Z][A-Za-z0-9_]*", code)
    )
    return {
        "hooksUsed": hooks,
        "eventAttributes": events,
        "eventCallbackUpdatesCount": updates,
        "components": components,
    }


def _flame_code_score(generated: str, reference: str) -> Tuple[float, Dict[str, Any]]:
    generated_counts = _react_feature_counts(generated)
    reference_counts = _react_feature_counts(reference)
    keys = ("hooksUsed", "eventAttributes", "eventCallbackUpdatesCount")
    generated_sum = sum(generated_counts[key] for key in keys)
    reference_sum = sum(reference_counts[key] for key in keys)
    score = 0.0 if generated_sum == 0 and reference_sum != 0 else 1.0
    return score, {
        "generated": generated_counts,
        "reference": reference_counts,
        "implementation": "Python regex compatibility port of Flame score.js",
    }


def _pixel_cosine(path_a: str, path_b: str) -> float:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Flame fallback metric requires Pillow") from exc
    with Image.open(path_a) as image_a, Image.open(path_b) as image_b:
        size = (256, 256)
        values_a = list(image_a.convert("RGB").resize(size).getdata())
        values_b = list(image_b.convert("RGB").resize(size).getdata())
    dot = norm_a = norm_b = 0.0
    for pixel_a, pixel_b in zip(values_a, values_b):
        for value_a, value_b in zip(pixel_a, pixel_b):
            centered_a = float(value_a) - 127.5
            centered_b = float(value_b) - 127.5
            dot += centered_a * centered_b
            norm_a += centered_a * centered_a
            norm_b += centered_b * centered_b
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def _multipart_image_request(url: str, image_path: str, timeout: float = 120) -> Any:
    boundary = "----MultimodalCodeBoundary"
    image = Path(image_path).read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{Path(image_path).name}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + image + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _mean_embedding(payload: Any) -> List[float]:
    values = payload.get("last_hidden_state_list", [])
    if values and isinstance(values[0], list) and values[0] and isinstance(values[0][0], list):
        values = values[0]
    if not values:
        raise ValueError("Embedding endpoint returned an empty last_hidden_state_list")
    dimension = len(values[0])
    result = [0.0] * dimension
    for token in values:
        for index, value in enumerate(token):
            result[index] += float(value)
    return [value / len(values) for value in result]


def _embedding_cosine(url: str, path_a: str, path_b: str) -> float:
    vector_a = _mean_embedding(_multipart_image_request(url, path_a))
    vector_b = _mean_embedding(_multipart_image_request(url, path_b))
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _pass_at_k(scores: List[float], threshold: float, k: int) -> float:
    n = len(scores)
    if not n:
        return 0.0
    c = sum(score >= threshold for score in scores)
    if n - c < k:
        return 1.0
    if k > n:
        return 0.0
    product = 1.0
    for value in range(n - c + 1, n + 1):
        product *= 1.0 - k / value
    return 1.0 - product


def evaluate_flame(run_dir: Path, evaluator: Dict[str, Any]) -> Dict[str, Any]:
    predictions = {
        f"{row['item_id']}::{row['sample_index']}": row
        for row in _read_rows(run_dir / "predictions.jsonl")
        if row.get("status") == "ok"
    }
    embedding_url = (
        evaluator.get("embedding_url")
        or os.environ.get("FLAME_EMBEDDING_URL")
    )
    visual_mode = "embedding_endpoint" if embedding_url else "pixel_cosine_fallback"
    score_path = run_dir / "scores.jsonl"
    prior_rows = _latest_rows(
        [
            row
            for row in _read_rows(score_path)
            if row.get("profile") == "flame"
        ]
    )
    existing = {row["key"] for row in prior_rows if row.get("status") == "ok"}
    new_rows = []
    for render in _successful_renders(run_dir):
        if render["key"] in existing:
            continue
        prediction = predictions.get(render["key"])
        if not prediction:
            continue
        reference = str(prediction.get("metadata", {}).get("reference_code") or "")
        code_score, code_details = _flame_code_score(prediction["output"], reference)
        if embedding_url:
            image_score = _embedding_cosine(
                str(embedding_url), render["rendered_path"], render["reference_image"]
            )
        else:
            image_score = _pixel_cosine(
                render["rendered_path"], render["reference_image"]
            )
        new_rows.append(
            {
                "key": render["key"],
                "benchmark": render["benchmark"],
                "item_id": render["item_id"],
                "sample_index": render["sample_index"],
                "profile": "flame",
                "status": "ok",
                "image_similarity": image_score,
                "code_similarity": code_score,
                "score": image_score * code_score,
                "score_normalized": image_score * code_score,
                "code_details": code_details,
                "visual_mode": visual_mode,
            }
        )
    for row in new_rows:
        append_jsonl(score_path, row)
    rows = prior_rows + new_rows
    grouped: Dict[str, List[float]] = {}
    for row in rows:
        grouped.setdefault(row["item_id"], []).append(float(row["score"]))
    thresholds = evaluator.get("thresholds", [0.7, 0.8, 0.85, 0.9, 0.95, 0.99])
    pass_scores = {}
    for threshold in thresholds:
        for k in (1, 3, 5):
            values = [_pass_at_k(scores, float(threshold), k) for scores in grouped.values()]
            pass_scores[f"pass@{k}/threshold={threshold}"] = (
                sum(values) / len(values) if values else 0.0
            )
    summary = {
        "profile": "flame",
        "protocol": (
            "Flame code-score and image-embedding cosine"
            if embedding_url
            else "Flame-compatible code-score with NON-OFFICIAL pixel-cosine fallback"
        ),
        "official_visual_metric": bool(embedding_url),
        "visual_mode": visual_mode,
        "total": len(rows),
        "score": sum(row["score"] for row in rows) / len(rows) if rows else None,
        **pass_scores,
    }
    return summary


def evaluate_design2code(
    run_dir: Path, evaluator: Dict[str, Any], workers: int
) -> Dict[str, Any]:
    upstream_root = Path(
        os.path.expandvars(str(evaluator.get("upstream_root", "")))
    ).expanduser().resolve()
    if not upstream_root.exists():
        raise FileNotFoundError(f"Design2Code upstream root not found: {upstream_root}")
    predictions = [
        row for row in _read_rows(run_dir / "predictions.jsonl")
        if row.get("status") == "ok" and row.get("html_path")
    ]
    work = run_dir / "official_design2code"
    refs = work / "references"
    preds = work / "predictions"
    refs.mkdir(parents=True, exist_ok=True)
    preds.mkdir(parents=True, exist_ok=True)
    rick = evaluator.get("rick_path")
    if rick:
        rick_path = Path(os.path.expandvars(str(rick))).expanduser().resolve()
    else:
        rick_path = upstream_root / "Design2Code" / "prompting" / "rick.jpg"
    if rick_path.exists():
        shutil.copy2(rick_path, refs / "rick.jpg")
        shutil.copy2(rick_path, preds / "rick.jpg")
    manifest = []
    for row in predictions:
        key = f"{safe_name(row['item_id'])}__s{row['sample_index']}"
        reference_code = row.get("metadata", {}).get("reference_code")
        if not reference_code:
            continue
        reference_path = refs / f"{key}.html"
        prediction_path = preds / f"{key}.html"
        reference_path.write_text(str(reference_code), encoding="utf-8")
        shutil.copy2(row["html_path"], prediction_path)
        manifest.append(
            {
                "key": row["key"] if "key" in row else f"{row['item_id']}::{row['sample_index']}",
                "item_id": row["item_id"],
                "sample_index": row["sample_index"],
                "reference_html": str(reference_path),
                "prediction_html": str(prediction_path),
            }
        )
    manifest_path = work / "manifest.json"
    output_path = work / "scores.json"
    write_json(manifest_path, manifest)
    driver = Path(__file__).with_name("official_design2code.py")
    command = [
        sys.executable,
        str(driver),
        "--upstream-root",
        str(upstream_root),
        "--manifest",
        str(manifest_path),
        "--output",
        str(output_path),
        "--workers",
        str(max(1, workers)),
    ]
    subprocess.run(command, check=True)
    result = read_json(output_path)
    existing = {
        row["key"]
        for row in _read_rows(run_dir / "scores.jsonl")
        if row.get("profile") == "design2code_official" and row.get("status") == "ok"
    }
    for row in result.get("rows", []):
        if row.get("key") not in existing:
            append_jsonl(run_dir / "scores.jsonl", row)
    return result["summary"]


def evaluate_external(run_dir: Path, evaluator: Dict[str, Any]) -> Dict[str, Any]:
    command_value = evaluator.get("command")
    if not command_value:
        raise ValueError("External evaluator requires a command")
    command = (
        shlex.split(command_value)
        if isinstance(command_value, str)
        else [str(value) for value in command_value]
    )
    manifest = read_json(run_dir / "run.json")
    replacements = {
        "run_dir": str(run_dir),
        "benchmark": str(manifest["benchmark"]),
        "model": str(manifest["model"]),
    }
    command = [part.format(**replacements) for part in command]
    cwd_value = evaluator.get("cwd")
    cwd = Path(str(cwd_value)).resolve() if cwd_value else None
    result = subprocess.run(command, check=False, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"External evaluator exited with status {result.returncode}: {command}"
        )
    summary_file = evaluator.get("summary_file")
    if summary_file:
        return read_json(run_dir / str(summary_file))
    return {
        "profile": "external",
        "protocol": "Delegated to upstream benchmark evaluator",
        "command": command,
        "returncode": result.returncode,
    }


def evaluate_run(
    run_dir: str | Path,
    *,
    judge_backend: Optional[ModelBackend] = None,
    judge_model: str = "",
    workers: int = 4,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    retries: int = 2,
    overwrite: bool = False,
) -> Dict[str, Any]:
    target = Path(run_dir).resolve()
    score_file = target / "scores.jsonl"
    if overwrite and score_file.exists():
        score_file.unlink()
    manifest = read_json(target / "run.json")
    evaluator = dict(manifest["benchmark_config"].get("evaluator", {}))
    if isinstance(manifest["benchmark_config"].get("evaluator"), str):
        evaluator = {"type": manifest["benchmark_config"]["evaluator"]}
    config_dir = Path(
        manifest.get("benchmark_config_dir", target)
    ).resolve()
    for path_key in ("upstream_root", "rick_path", "cwd"):
        path_value = evaluator.get(path_key)
        if path_value and not Path(os.path.expandvars(str(path_value))).expanduser().is_absolute():
            evaluator[path_key] = str((config_dir / str(path_value)).resolve())
    evaluator_type = evaluator.get("type", "ui2code_vlm")
    if evaluator_type in {"ui2code_vlm", "web2code_vlm"}:
        if judge_backend is None:
            raise ValueError(f"{evaluator_type} requires a judge backend")
        summary = evaluate_vlm_judge(
            target,
            evaluator_type,
            judge_backend,
            judge_model=judge_model,
            workers=workers,
            max_tokens=max_tokens,
            temperature=temperature,
            retries=retries,
            overwrite=overwrite,
        )
    elif evaluator_type == "flame":
        summary = evaluate_flame(target, evaluator)
    elif evaluator_type == "design2code_official":
        summary = evaluate_design2code(
            target,
            evaluator,
            workers=int(evaluator.get("workers", workers)),
        )
    elif evaluator_type == "external":
        summary = evaluate_external(target, evaluator)
    else:
        raise ValueError(f"Unsupported evaluator type: {evaluator_type}")
    summary["benchmark"] = manifest["benchmark"]
    summary["model"] = manifest["model"]
    write_json(target / "evaluation_summary.json", summary)
    return summary

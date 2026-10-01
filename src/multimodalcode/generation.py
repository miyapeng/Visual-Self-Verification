from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Set

from .artifacts import extract_html, flame_browser_html, save_project_markdown
from .backends import ModelBackend
from .config import BenchmarkConfig
from .datasets import load_benchmark
from .io import append_jsonl, safe_name, write_json
from .prompts import build_prompt
from .schema import BenchmarkItem, GenerationRequest, Prediction


def _existing_keys(path: Path) -> Set[str]:
    keys: Set[str] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "ok":
                keys.add(f"{row['item_id']}::{row['sample_index']}")
    return keys


def _save_artifacts(
    run_dir: Path,
    item: BenchmarkItem,
    sample_index: int,
    output: str,
    output_format: str,
) -> Dict[str, str | None]:
    key = f"{safe_name(item.item_id)}__s{sample_index}"
    raw_path = run_dir / "raw" / f"{key}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(output, encoding="utf-8")
    html_path = None
    site_path = None
    if output_format == "html":
        target = run_dir / "html" / f"{key}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(extract_html(output), encoding="utf-8")
        html_path = str(target.resolve())
    elif output_format == "flame":
        target = run_dir / "html" / f"{key}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(flame_browser_html(output), encoding="utf-8")
        html_path = str(target.resolve())
    elif output_format == "project":
        site = run_dir / "sites" / key
        written = save_project_markdown(output, site)
        if not written:
            target = site / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(extract_html(output), encoding="utf-8")
            written = [str(target)]
        site_path = str(site.resolve())
        index = site / "index.html"
        if index.exists():
            html_path = str(index.resolve())
    else:
        raise ValueError(f"Unsupported output format: {output_format}")
    return {
        "raw_path": str(raw_path.resolve()),
        "html_path": html_path,
        "site_path": site_path,
    }


def generate_run(
    config: BenchmarkConfig,
    backend: ModelBackend,
    run_dir: str | Path,
    *,
    model_name: str,
    backend_name: str,
    limit: int | None = None,
    workers: int = 1,
    samples: int = 1,
    max_tokens: int = 8192,
    temperature: float = 0.0,
    retries: int = 2,
    system_prompt: str | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    target = Path(run_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    predictions_file = target / "predictions.jsonl"
    if overwrite and predictions_file.exists():
        predictions_file.unlink()
    items = load_benchmark(config, limit=limit)
    missing_images = [
        image
        for item in items
        for image in item.image_paths
        if not Path(image).exists()
    ]
    if missing_images:
        preview = "\n".join(missing_images[:10])
        raise FileNotFoundError(f"Benchmark image(s) missing:\n{preview}")
    manifest = {
        "format_version": 1,
        "benchmark": config.name,
        "benchmark_config": config.values,
        "benchmark_config_dir": str(config.config_dir.resolve()),
        "model": model_name,
        "backend": backend_name,
        "output_format": config.output_format,
        "items": len(items),
        "samples_per_item": samples,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(target / "run.json", manifest)
    existing = _existing_keys(predictions_file)
    jobs = [
        (item, sample_index)
        for item in items
        for sample_index in range(samples)
        if f"{item.item_id}::{sample_index}" not in existing
    ]
    if not backend.thread_safe:
        workers = 1
    write_lock = threading.Lock()
    counters = {"ok": 0, "error": 0, "skipped": len(items) * samples - len(jobs)}

    def process(item: BenchmarkItem, sample_index: int) -> Prediction:
        prompt = build_prompt(
            item.prompt,
            config.output_format,
            str(config.values.get("extra_prompt", "")),
        )
        request = GenerationRequest(
            prompt=prompt,
            image_paths=item.image_paths,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )
        started = time.monotonic()
        last_error = None
        output = ""
        for attempt in range(retries + 1):
            try:
                output = backend.generate(request)
                if not output.strip():
                    raise RuntimeError("Model returned an empty response")
                artifacts = _save_artifacts(
                    target, item, sample_index, output, config.output_format
                )
                return Prediction(
                    benchmark=config.name,
                    item_id=item.item_id,
                    sample_index=sample_index,
                    prompt=prompt,
                    image_paths=item.image_paths,
                    output=output,
                    status="ok",
                    latency_seconds=time.monotonic() - started,
                    raw_path=artifacts["raw_path"],
                    html_path=artifacts["html_path"],
                    site_path=artifacts["site_path"],
                    metadata={
                        **item.metadata,
                        "reference_code": item.reference_code,
                    },
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
        return Prediction(
            benchmark=config.name,
            item_id=item.item_id,
            sample_index=sample_index,
            prompt=prompt,
            image_paths=item.image_paths,
            output=output,
            status="error",
            latency_seconds=time.monotonic() - started,
            error=last_error,
            metadata={**item.metadata, "reference_code": item.reference_code},
        )

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(process, item, sample_index): (item, sample_index)
            for item, sample_index in jobs
        }
        completed = 0
        total = len(future_map)
        for future in as_completed(future_map):
            prediction = future.result()
            with write_lock:
                append_jsonl(predictions_file, prediction.to_dict())
                counters[prediction.status] += 1
                completed += 1
                print(
                    f"[{completed}/{total}] {prediction.item_id} "
                    f"sample={prediction.sample_index} {prediction.status}",
                    flush=True,
                )
    summary = {**manifest, **counters, "predictions_file": str(predictions_file)}
    write_json(target / "generation_summary.json", summary)
    return summary

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict, List, Tuple

from .io import append_jsonl, read_json, safe_name, write_json


def _image_size(path: str, default: Tuple[int, int]) -> Tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return default
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return default


def _load_predictions(run_dir: Path) -> List[Dict[str, Any]]:
    rows = []
    with (run_dir / "predictions.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if row.get("status") == "ok" and row.get("html_path"):
                    rows.append(row)
    return rows


def _render_with_node(
    run_dir: Path,
    rows: List[Dict[str, Any]],
    *,
    workers: int,
    width: int | None,
    height: int | None,
    full_page: bool,
    wait_ms: int,
    timeout_ms: int,
    overwrite: bool,
) -> Dict[str, Any]:
    try:
        import playwright
    except ImportError as exc:
        raise RuntimeError(
            "Rendering requires: pip install -r requirements.txt && "
            "python -m playwright install chromium"
        ) from exc
    driver_dir = Path(playwright.__file__).resolve().parent / "driver"
    node_bin = driver_dir / "node"
    driver_package = driver_dir / "package"
    renderer = Path(__file__).resolve().parents[2] / "scripts" / "render_playwright.js"
    if not node_bin.is_file() or not driver_package.is_dir() or not renderer.is_file():
        raise RuntimeError("The Playwright Node driver or project renderer is missing")
    render_file = run_dir / "renders.jsonl"
    if overwrite and render_file.exists():
        render_file.unlink()
    existing = set()
    if render_file.exists() and not overwrite:
        for line in render_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("status") == "ok":
                    existing.add(row["key"])
    results: List[Dict[str, Any]] = []
    output_dir = run_dir / "rendered"
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks: List[Dict[str, Any]] = []
    for row in rows:
        key = f"{row['item_id']}::{row['sample_index']}"
        if key in existing:
            continue
        filename = f"{safe_name(row['item_id'])}__s{row['sample_index']}.png"
        output_path = output_dir / filename
        target_width, target_height = (width or 1280, height or 960)
        if row.get("image_paths"):
            image_width, image_height = _image_size(
                row["image_paths"][0], (target_width, target_height)
            )
            target_width = width or image_width
            target_height = height or image_height
        target_width = max(320, min(int(target_width), 4096))
        target_height = max(240, min(int(target_height), 4096))
        result = {
            "key": key,
            "benchmark": row["benchmark"],
            "item_id": row["item_id"],
            "sample_index": row["sample_index"],
            "reference_image": row["image_paths"][0] if row.get("image_paths") else None,
            "html_path": row["html_path"],
            "rendered_path": str(output_path.resolve()),
            "viewport": [target_width, target_height],
        }
        tasks.append(
            {
                "result": result,
                "htmlPath": str(Path(row["html_path"]).resolve()),
                "outputPath": str(output_path.resolve()),
                "sitePath": bool(row.get("site_path")),
                "width": target_width,
                "height": target_height,
                "fullPage": full_page,
                "waitMs": wait_ms,
                "timeoutMs": timeout_ms,
            }
        )
    if not tasks:
        return {"processed": 0, "ok": 0, "error": 0}

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", dir=run_dir, delete=False
    ) as task_handle:
        json.dump(tasks, task_handle, ensure_ascii=False)
        task_path = Path(task_handle.name)
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_handle:
        try:
            process = subprocess.Popen(
                [
                    str(node_bin),
                    str(renderer),
                    str(driver_package),
                    str(task_path),
                    str(max(1, workers)),
                ],
                stdout=subprocess.PIPE,
                stderr=error_handle,
                text=True,
                encoding="utf-8",
            )
            assert process.stdout is not None
            for line in process.stdout:
                if not line.strip():
                    continue
                result = json.loads(line)
                append_jsonl(render_file, result)
                results.append(result)
                print(
                    f"[render] {result['key']} {result['status']}",
                    flush=True,
                )
            return_code = process.wait()
            if return_code:
                error_handle.seek(0)
                detail = error_handle.read().strip()
                raise RuntimeError(
                    f"Playwright renderer exited with code {return_code}: {detail}"
                )
        finally:
            task_path.unlink(missing_ok=True)
    ok = sum(row.get("status") == "ok" for row in results)
    return {"processed": len(results), "ok": ok, "error": len(results) - ok}


def render_run(
    run_dir: str | Path,
    *,
    workers: int = 4,
    width: int | None = None,
    height: int | None = None,
    full_page: bool | None = None,
    wait_ms: int | None = None,
    timeout_ms: int = 60000,
    overwrite: bool = False,
) -> Dict[str, Any]:
    target = Path(run_dir).resolve()
    manifest = read_json(target / "run.json")
    render_config = dict(manifest.get("benchmark_config", {}).get("render", {}))
    selected_full_page = (
        bool(full_page)
        if full_page is not None
        else bool(render_config.get("full_page", False))
    )
    selected_wait = wait_ms if wait_ms is not None else int(render_config.get("wait_ms", 1500))
    selected_width = width or render_config.get("width")
    selected_height = height or render_config.get("height")
    result = _render_with_node(
        target,
        _load_predictions(target),
        workers=workers,
        width=selected_width,
        height=selected_height,
        full_page=selected_full_page,
        wait_ms=selected_wait,
        timeout_ms=timeout_ms,
        overwrite=overwrite,
    )
    summary = {
        **result,
        "full_page": selected_full_page,
        "wait_ms": selected_wait,
        "width": selected_width,
        "height": selected_height,
    }
    write_json(target / "render_summary.json", summary)
    return summary

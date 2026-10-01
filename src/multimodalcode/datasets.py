from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .config import BenchmarkConfig
from .io import iter_jsonl, resolve_path
from .schema import BenchmarkItem


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _as_image_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise ValueError(f"Unsupported image field: {type(value).__name__}")


def _resolve_images(values: Iterable[str], root: Path) -> List[str]:
    return [str(resolve_path(value, root)) for value in values]


def load_ui2code_jsonl(config: BenchmarkConfig) -> List[BenchmarkItem]:
    data_file = config.path("data_file")
    assert data_file is not None
    image_root = config.path("image_root", required=False) or data_file.parent
    id_field = str(config.values.get("id_field", "id"))
    prompt_field = str(config.values.get("prompt_field", "prompt"))
    image_field = str(config.values.get("image_field", "image_path"))
    reference_field = str(config.values.get("reference_field", "reference"))
    rows = []
    for index, row in enumerate(iter_jsonl(data_file)):
        item_id = str(row.get(id_field, index))
        image_paths = _resolve_images(_as_image_list(row.get(image_field)), image_root)
        metadata = {
            key: value
            for key, value in row.items()
            if key not in {id_field, prompt_field, image_field, reference_field}
        }
        rows.append(
            BenchmarkItem(
                benchmark=config.name,
                item_id=item_id,
                prompt=str(row.get(prompt_field) or config.values.get("default_prompt", "")),
                image_paths=image_paths,
                reference_code=row.get(reference_field),
                metadata=metadata,
            )
        )
    return rows


def load_flame_official(config: BenchmarkConfig) -> List[BenchmarkItem]:
    data_file = config.path("data_file")
    assert data_file is not None
    image_root = config.path("image_root", required=False) or data_file.parent
    with data_file.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Flame data must be a JSON array: {data_file}")
    items = []
    output_contract = (
        "\n\nReturn exactly two sections and no explanation:\n"
        "// CSS\n<complete CSS>\n"
        "// JavaScript XML (JSX)\n<complete runnable React component>\n"
    )
    for index, row in enumerate(payload):
        problem_id = str(row.get("problem_id", index))
        prompt = (
            str(row.get("instruction_layout", "")).strip()
            + "\n\nFunctional requirements:\n"
            + str(row.get("instruction_requirement", "")).strip()
            + output_contract
        )
        items.append(
            BenchmarkItem(
                benchmark=config.name,
                item_id=problem_id,
                prompt=prompt,
                image_paths=_resolve_images(_as_image_list(row.get("image")), image_root),
                reference_code=row.get("component"),
                metadata={
                    "complexity_level": row.get("complexity_level"),
                    "style": row.get("style", ""),
                    "file_type": row.get("file_type", "jsx"),
                },
            )
        )
    return items


def load_design2code_directory(config: BenchmarkConfig) -> List[BenchmarkItem]:
    data_root = config.path("data_root")
    assert data_root is not None
    prompt = str(
        config.values.get(
            "default_prompt",
            "Generate a single self-contained HTML file that reproduces this screenshot.",
        )
    )
    items = []
    for image in sorted(
        (path for path in data_root.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name,
    ):
        reference = image.with_suffix(".html")
        if not reference.exists():
            continue
        items.append(
            BenchmarkItem(
                benchmark=config.name,
                item_id=image.stem,
                prompt=prompt,
                image_paths=[str(image.resolve())],
                reference_code=reference.read_text(encoding="utf-8"),
                metadata={"reference_html_path": str(reference.resolve())},
            )
        )
    return items


def load_webcompass_jsonl(config: BenchmarkConfig) -> List[BenchmarkItem]:
    data_file = config.path("data_file")
    assert data_file is not None
    images_root = config.path("images_root")
    assert images_root is not None
    items = []
    for index, row in enumerate(iter_jsonl(data_file)):
        item_id = str(row.get("instance_id", index))
        instruction = str(row.get("instruction") or item_id)
        image_dir = images_root / instruction
        screenshots = image_dir / "screenshots"
        if screenshots.is_dir():
            image_dir = screenshots
        images = sorted(
            str(path.resolve())
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ) if image_dir.is_dir() else []
        prompt = (
            "Generate a complete runnable website repository matching all supplied "
            "reference screenshots. Preserve layout, colors, typography, interactions, "
            "and responsive behavior."
        )
        items.append(
            BenchmarkItem(
                benchmark=config.name,
                item_id=item_id,
                prompt=prompt,
                image_paths=images,
                metadata={"source": row, "instruction": instruction},
            )
        )
    return items


LOADERS = {
    "ui2code_jsonl": load_ui2code_jsonl,
    "flame_official": load_flame_official,
    "design2code_directory": load_design2code_directory,
    "webcompass_jsonl": load_webcompass_jsonl,
}


def load_benchmark(config: BenchmarkConfig, limit: int | None = None) -> List[BenchmarkItem]:
    if not config.enabled:
        reason = config.values.get("disabled_reason", "disabled in configuration")
        raise RuntimeError(f"Benchmark {config.name!r} is disabled: {reason}")
    try:
        loader = LOADERS[config.adapter]
    except KeyError as exc:
        raise ValueError(f"Unsupported adapter: {config.adapter}") from exc
    items = loader(config)
    if limit is not None and limit >= 0:
        items = items[:limit]
    return items


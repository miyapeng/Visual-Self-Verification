#!/usr/bin/env python3
"""Frozen visual feedback tool for mini-swe-agent workspaces."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import tempfile
from pathlib import Path


# Split the protocol marker in source so printing this file during agent
# debugging cannot itself be mistaken for a multimodal observation.
MULTIMODAL_OPEN = "<MSWEA_" "MULTIMODAL_CONTENT>"
MULTIMODAL_CLOSE = "</MSWEA_" "MULTIMODAL_CONTENT>"


def emit_image(path: Path) -> None:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        print(
            f"{MULTIMODAL_OPEN}<CONTENT_TYPE>image_url</CONTENT_TYPE>"
            f"data:{mime};base64,{encoded}{MULTIMODAL_CLOSE}"
        )
    except BrokenPipeError:
        return


def render_html(input_path: Path, output_path: Path, width: int, height: int) -> None:
    try:
        import playwright
    except ImportError as exc:
        raise RuntimeError("Rendering needs playwright in the task environment") from exc
    source = input_path.resolve()
    target = output_path.resolve()
    driver_dir = Path(playwright.__file__).resolve().parent / "driver"
    node = driver_dir / "node"
    package = driver_dir / "package"
    renderer = Path(__file__).with_name("render_one_playwright.js")
    if not node.is_file() or not package.is_dir() or not renderer.is_file():
        raise RuntimeError("Playwright Node driver or agent renderer is missing")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as spec:
        json.dump(
            {
                "source": str(source),
                "target": str(target),
                "width": width,
                "height": height,
            },
            spec,
        )
        spec.flush()
        completed = subprocess.run(
            [str(node), str(renderer), str(package), spec.name],
            text=True,
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Playwright renderer failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    result = json.loads(completed.stdout)
    for error in result.get("errors", []):
        print(error)
    emit_image(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view-image")
    view.add_argument("path", type=Path)
    render = commands.add_parser("render-html")
    render.add_argument("input", type=Path)
    render.add_argument("output", type=Path)
    render.add_argument("--width", type=int, default=1440)
    render.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    if args.command == "view-image":
        emit_image(args.path)
    else:
        render_html(args.input, args.output, args.width, args.height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Design2Code screenshot helper adapted to the bundled Playwright Node driver.

The released helper uses Playwright's Python driver. On this host Python 3.10
cannot keep that Node driver pipe open, while the exact same bundled Node
driver works normally when invoked synchronously. This adapter preserves the
official 1280x720/full-page screenshot behavior without adding another
browser runtime.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def take_screenshot(
    url: str, output_file: str = "screenshot.png", do_it_again: bool = False
) -> None:
    source = Path(url).resolve()
    output = Path(output_file).resolve()
    if output.exists() and not do_it_again:
        print(f"{output} exists!")
        return
    if not source.is_file():
        raise FileNotFoundError(f"HTML input does not exist: {source}")

    try:
        import playwright
    except ImportError as exc:
        raise RuntimeError(
            "Design2Code screenshots require the project Playwright dependency"
        ) from exc

    driver_dir = Path(playwright.__file__).resolve().parent / "driver"
    node_bin = driver_dir / "node"
    driver_package = driver_dir / "package"
    project_root = Path(__file__).resolve().parents[4]
    renderer = project_root / "scripts" / "render_playwright.js"
    task = {
        "result": {"key": source.stem},
        "htmlPath": str(source),
        "outputPath": str(output),
        "sitePath": True,
        "width": 1280,
        "height": 720,
        "fullPage": True,
        "waitMs": 1000,
        "timeoutMs": 60000,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as handle:
        json.dump([task], handle)
        task_file = Path(handle.name)
    try:
        result = subprocess.run(
            [
                str(node_bin),
                str(renderer),
                str(driver_package),
                str(task_file),
                "1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        task_file.unlink(missing_ok=True)
    if result.returncode != 0 or not output.is_file():
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to render {source}: {detail}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--png", required=True)
    arguments = parser.parse_args()
    take_screenshot(arguments.html, arguments.png, do_it_again=True)

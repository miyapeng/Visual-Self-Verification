#!/usr/bin/env python3
"""Freeze the three official Vision2Web agent prompts into the dataset bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--upstream", type=Path, default=project_root / "evaluate/vision2web/upstream"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "data/vision2web/agent_visible/prompts",
    )
    args = parser.parse_args()
    source = args.upstream / "vision2web" / "inference" / "prompts.py"
    spec = importlib.util.spec_from_file_location("vision2web_frozen_prompts", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args.output.mkdir(parents=True, exist_ok=True)
    prompts = {
        "webpage": module.WEBPAGE_PROMPT,
        "frontend": module.FRONTEND_PROMPT,
        "website": module.WEBSITE_PROMPT,
    }
    for name, prompt in prompts.items():
        # Preserve the Python constant byte-for-byte; load_case also tolerates
        # the legacy carrier files that ended with one POSIX newline.
        (args.output / f"{name}.md").write_text(prompt, encoding="utf-8")
    manifest = {
        "schema": "vision2web-official-prompts-1",
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_revision": "577f9397b3db8fc6d828adde254a830caa65d515",
        "files": {
            name: {
                "path": f"{name}.md",
                "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
            for name, prompt in prompts.items()
        },
    }
    (args.output.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

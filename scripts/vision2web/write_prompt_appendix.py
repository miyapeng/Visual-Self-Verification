#!/usr/bin/env python3
"""Write the exact Vision2Web task prompts for every level and condition."""

from __future__ import annotations

import hashlib
from pathlib import Path

from multimodalcode.agent_harness.vision2web_experiment import prompt_for_mode


ROOT = Path(__file__).resolve().parents[2]
LEVELS = (
    ("Level 1", "webpage"),
    ("Level 2", "frontend"),
    ("Level 3", "website"),
)
MODES = ("official", "browser_enabled", "guided_vsv")


def main() -> int:
    output = ROOT / "reports/vision2web_self_verification_prompts.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Exact Vision2Web experiment prompts",
        "",
        "These are the complete task prompts supplied to OpenHands and Claude Code. Tool schemas are "
        "not task-prompt text. `official` and `browser_enabled` are byte-identical "
        "by design; `guided_vsv` appends the frozen detailed verification procedure. "
        "For Claude Code, `browser_enabled` is not an active condition because the "
        "official scaffold already exposes playwright-cli.",
        "",
    ]
    for level, task_type in LEVELS:
        official = (ROOT / f"data/vision2web/agent_visible/prompts/{task_type}.md").read_text(
            encoding="utf-8"
        )
        if official.endswith("\n"):
            official = official[:-1]
        for mode in MODES:
            effective = prompt_for_mode(official, mode)
            digest = hashlib.sha256(effective.encode()).hexdigest()
            lines.extend(
                [
                    f"## {level} / `{task_type}` / `{mode}`",
                    "",
                    f"SHA-256: `{digest}`",
                    "",
                    "````text",
                    effective,
                    "````",
                    "",
                ]
            )
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

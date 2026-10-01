#!/usr/bin/env python3
"""Make two minimal calls required by the released InteractWeb configuration."""

from __future__ import annotations

import argparse
import json
import stat
import time
from pathlib import Path

from openai import OpenAI


ALLOWED_KEYS = {
    "USER_MODEL_BASE_URL",
    "USER_MODEL_API_KEY",
    "WEBVOYAGER_BASE_URL",
    "WEBVOYAGER_API_KEY",
}
# Verified 1x1 PNG, kept inline so the preflight has no external asset.
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def load_credentials(path: Path) -> dict[str, str]:
    path = path.resolve()
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(f"credentials file must be chmod 600, got {mode:o}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"invalid record at line {line_number}")
        key, value = line.split("=", 1)
        if key not in ALLOWED_KEYS:
            raise RuntimeError(f"unsupported credential name at line {line_number}: {key}")
        if not value:
            raise RuntimeError(f"empty value for {key}")
        if "replace-me" in value or "your-openai-compatible" in value:
            raise RuntimeError(f"placeholder value remains for {key}")
        values[key] = value
    missing = sorted(ALLOWED_KEYS - values.keys())
    if missing:
        raise RuntimeError(f"missing credential records: {', '.join(missing)}")
    return values


def minimal_call(
    *, base_url: str, api_key: str, model: str, multimodal: bool
) -> float:
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0)
    content = (
        [
            {"type": "text", "text": "Reply with OK."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{TINY_PNG}"},
            },
        ]
        if multimodal
        else "Reply with OK."
    )
    started = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=8,
        seed=42,
    )
    if not response.choices:
        raise RuntimeError(f"{model} returned no choices")
    return time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials-file", required=True, type=Path)
    arguments = parser.parse_args()
    credentials = load_credentials(arguments.credentials_file)
    user_seconds = minimal_call(
        base_url=credentials["USER_MODEL_BASE_URL"],
        api_key=credentials["USER_MODEL_API_KEY"],
        model="deepseek-v3.2",
        multimodal=False,
    )
    judge_seconds = minimal_call(
        base_url=credentials["WEBVOYAGER_BASE_URL"],
        api_key=credentials["WEBVOYAGER_API_KEY"],
        model="gpt-5-mini",
        multimodal=True,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "user_model": "deepseek-v3.2",
                "user_text_call_seconds": round(user_seconds, 3),
                "judge_model": "gpt-5-mini",
                "judge_multimodal_call_seconds": round(judge_seconds, 3),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

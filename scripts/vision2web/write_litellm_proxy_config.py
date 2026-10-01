#!/usr/bin/env python3
"""Write a minimal LiteLLM proxy config for an OpenAI-compatible vLLM server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--api-base", required=True)
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument("--api-key", default=None)
    credentials.add_argument(
        "--api-key-env",
        metavar="NAME",
        help=(
            "write a LiteLLM environment reference instead of persisting the "
            "credential in the config (for example LLM_RELAY_API_KEY)"
        ),
    )
    parser.add_argument("--supports-vision", type=parse_bool, default=True)
    parser.add_argument("--max-input-tokens", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    args = parser.parse_args()
    if args.max_input_tokens <= 0 or args.max_output_tokens <= 0:
        parser.error("token limits must be positive")
    if args.max_output_tokens >= args.max_input_tokens:
        parser.error("max output tokens must be smaller than max input tokens")

    api_key = f"os.environ/{args.api_key_env}" if args.api_key_env else args.api_key
    if api_key is None:
        api_key = "EMPTY"

    data = {
        "model_list": [
            {
                "model_name": args.model_name,
                "litellm_params": {
                    "model": f"openai/{args.model_name}",
                    "api_base": args.api_base.rstrip("/"),
                    "api_key": api_key,
                    "drop_params": True,
                },
                "model_info": {
                    "mode": "chat",
                    "supports_vision": args.supports_vision,
                    "max_input_tokens": args.max_input_tokens,
                    "max_output_tokens": args.max_output_tokens,
                },
            }
        ],
        # Claude Code 2.1.197 requests 32K output tokens even when the routed
        # model advertises a smaller output limit.  Ask LiteLLM to enforce the
        # model_info limits before forwarding the request.  This keeps the
        # declared 8K generation budget effective and prevents image-heavy
        # Vision2Web turns from overflowing Qwen's 262K context window.
        "litellm_settings": {
            "modify_params": True,
            "callbacks": ["litellm_output_token_cap.proxy_handler_instance"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

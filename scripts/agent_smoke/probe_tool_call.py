#!/usr/bin/env python3
"""Probe one ClusterX vLLM server's automatic tool-call parser."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} SERVER_RUN OUTPUT_JSON", file=sys.stderr)
        return 2
    project = Path("/data/miyapeng/mmcode/MultimodalCode")
    ready = json.loads(
        (project / "runs" / "agent_smoke" / "servers" / sys.argv[1] / "ready.json").read_text()
    )
    payload = {
        "model": ready["model"],
        "messages": [
            {
                "role": "user",
                "content": "Use the get_weather tool to get the weather for Paris. Do not answer directly.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 512,
    }
    request = urllib.request.Request(
        f"{ready['endpoint'].rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    message = result["choices"][0]["message"]
    print(json.dumps({"content": message.get("content"), "tool_calls": message.get("tool_calls")}, indent=2))
    return 0 if message.get("tool_calls") else 1


if __name__ == "__main__":
    raise SystemExit(main())

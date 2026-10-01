"""Small Claude-to-vLLM request compatibility hook for Vision2Web."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Literal

from litellm.integrations.custom_logger import CustomLogger


def cap_max_tokens(data: dict, limit: int) -> dict:
    """Cap only ``max_tokens``; preserve every other request field."""

    requested = data.get("max_tokens")
    if isinstance(requested, (int, float)) and requested > limit:
        data["max_tokens"] = limit
    return data


def normalize_anthropic_image_blocks(data: dict) -> tuple[dict, list[dict]]:
    """Keep Claude Read images multimodal through LiteLLM 1.79's adapter.

    LiteLLM 1.79 stringifies an Anthropic image ``source`` dictionary inside a
    tool result.  That sends hundreds of kilobytes of base64 as text to vLLM.
    Its adapter does understand a top-level image when ``source`` is the raw
    base64 string, so retain the tool result and attach the same image there.
    """

    observations: list[dict] = []
    for message_index, message in enumerate(data.get("messages", [])):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        normalized: list = []
        attached: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                normalized.append(block)
                continue
            if block.get("type") == "image":
                source = block.get("source")
                if isinstance(source, dict) and isinstance(source.get("data"), str):
                    converted = dict(block)
                    converted["source"] = source["data"]
                    normalized.append(converted)
                    observations.append(
                        {
                            "message_index": message_index,
                            "location": "top_level",
                            "media_type": source.get("media_type"),
                            "base64_characters": len(source["data"]),
                        }
                    )
                    continue
            if block.get("type") != "tool_result" or not isinstance(
                block.get("content"), list
            ):
                normalized.append(block)
                continue
            retained: list = []
            for nested in block["content"]:
                source = nested.get("source") if isinstance(nested, dict) else None
                if (
                    isinstance(nested, dict)
                    and nested.get("type") == "image"
                    and isinstance(source, dict)
                    and isinstance(source.get("data"), str)
                ):
                    attached.append({"type": "image", "source": source["data"]})
                    observations.append(
                        {
                            "message_index": message_index,
                            "location": "tool_result",
                            "tool_use_id": block.get("tool_use_id"),
                            "media_type": source.get("media_type"),
                            "base64_characters": len(source["data"]),
                        }
                    )
                else:
                    retained.append(nested)
            converted_result = dict(block)
            # Preserve the tool-call/result pairing even when the image was
            # the result's only content; the pixels are attached immediately
            # after it as a user image block.
            converted_result["content"] = retained if retained else ""
            normalized.append(converted_result)
        normalized.extend(attached)
        message["content"] = normalized
    return data, observations


def append_audit(rows: list[dict], *, requested: object, effective: object) -> None:
    path = os.environ.get("MMCODE_PROXY_AUDIT_LOG")
    if not path or not rows:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "anthropic_image_protocol_normalized",
        "requested_max_tokens": requested,
        "effective_max_tokens": effective,
        "images": rows,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class OutputTokenCap(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict,
        cache,
        data: dict,
        call_type: Literal[
            "completion",
            "text_completion",
            "embeddings",
            "image_generation",
            "moderation",
            "audio_transcription",
            "pass_through_endpoint",
            "rerank",
            "mcp_call",
            "anthropic_messages",
        ],
    ) -> dict:
        limit = int(os.environ["MMCODE_PROXY_MAX_OUTPUT_TOKENS"])
        requested = data.get("max_tokens")
        data, images = normalize_anthropic_image_blocks(data)
        data = cap_max_tokens(data, limit)
        append_audit(
            images, requested=requested, effective=data.get("max_tokens")
        )
        return data


proxy_handler_instance = OutputTokenCap()

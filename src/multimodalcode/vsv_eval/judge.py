from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multimodalcode.io import read_json, write_json


SYSTEM_PROMPT = (
    "You are an offline evaluator of multimodal coding trajectories. Judge only "
    "from the supplied evidence. Never assume an unobserved action succeeded. "
    "Return exactly one JSON object matching the requested fields."
)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def load_judge_config(path: str | Path) -> dict[str, Any]:
    value = _expand(read_json(path))
    if value.get("schema") != "multimodalcode-vsv-judge-config-1":
        raise ValueError(f"Unsupported VSV judge config: {path}")
    return value


def _image_block(path: str, *, anthropic: bool) -> dict[str, Any]:
    image = Path(path)
    media_type = mimetypes.guess_type(image.name)[0] or "image/png"
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    if anthropic:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise ValueError(f"Judge returned no JSON object: {text[:500]!r}")
    if not isinstance(value, dict):
        raise ValueError("Judge output must be a JSON object")
    return value


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    max_tokens: int = 1200
    timeout: float = 600
    retries: int = 2
    thinking: dict[str, Any] | None = None
    json_mode: bool = False
    chat_template_kwargs: dict[str, Any] | None = None
    label_images: bool = False

    @classmethod
    def from_dict(cls, name: str, value: dict[str, Any]) -> "JudgeProfile":
        return cls(
            name=name,
            provider=str(value["provider"]),
            model=str(value["model"]),
            base_url=str(value.get("base_url") or "").rstrip("/"),
            api_key_env=str(value.get("api_key_env") or "OPENAI_API_KEY"),
            max_tokens=int(value.get("max_tokens", 1200)),
            timeout=float(value.get("timeout", 600)),
            retries=int(value.get("retries", 2)),
            thinking=value.get("thinking"),
            json_mode=bool(value.get("json_mode", False)),
            chat_template_kwargs=value.get("chat_template_kwargs"),
            label_images=bool(value.get("label_images", False)),
        )


class JudgeClient:
    def __init__(self, profile: JudgeProfile, cache_root: str | Path):
        if not profile.base_url:
            raise ValueError(f"No base URL configured for judge {profile.name}")
        self.profile = profile
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def _key(self, stage: str, prompt: str, images: list[str]) -> str:
        digest = hashlib.sha256()
        digest.update(self.profile.provider.encode())
        digest.update(self.profile.model.encode())
        digest.update(stage.encode())
        digest.update(json.dumps({
            "system": SYSTEM_PROMPT, "max_tokens": self.profile.max_tokens,
            "thinking": self.profile.thinking, "json_mode": self.profile.json_mode,
            "chat_template_kwargs": self.profile.chat_template_kwargs,
            "label_images": self.profile.label_images,
        }, sort_keys=True).encode())
        digest.update(prompt.encode())
        for image in images:
            path = Path(image)
            digest.update(str(path).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def judge(self, stage: str, prompt: str, images: list[str]) -> dict[str, Any]:
        key = self._key(stage, prompt, images)
        cache = self.cache_root / f"{key}.json"
        if cache.is_file():
            return read_json(cache)
        raw = self._request(prompt, images)
        parse_error = None
        try:
            parsed = _json_object(raw)
        except ValueError as exc:
            parsed, parse_error = {}, str(exc)
        record = {
            "schema": "multimodalcode-vsv-judge-response-1",
            "stage": stage,
            "profile": self.profile.name,
            "provider": self.profile.provider,
            "model": self.profile.model,
            "request_sha256": key,
            "generation_settings": {
                "temperature": 0, "max_tokens": self.profile.max_tokens,
                "thinking": self.profile.thinking, "json_mode": self.profile.json_mode,
                "chat_template_kwargs": self.profile.chat_template_kwargs,
                "label_images": self.profile.label_images,
            },
            "prompt": prompt,
            "image_paths": images,
            "parsed": parsed,
            "raw": raw,
        }
        if parse_error:
            record["error"] = parse_error
        write_json(cache, record)
        return record

    def _request(self, prompt: str, images: list[str]) -> str:
        provider = self.profile.provider.casefold().replace("_", "-")
        last_error: Exception | None = None
        for attempt in range(self.profile.retries + 1):
            try:
                if provider == "anthropic-compatible":
                    return self._anthropic(prompt, images)
                if provider == "openai-compatible":
                    return self._openai(prompt, images)
                raise ValueError(f"Unsupported judge provider: {self.profile.provider}")
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt >= self.profile.retries:
                    break
                time.sleep(min(4, 2 ** attempt))
        assert last_error is not None
        raise RuntimeError(f"Judge {self.profile.name} failed: {last_error}") from last_error

    def _openai(self, prompt: str, images: list[str]) -> str:
        endpoint = self.profile.base_url
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for index, path in enumerate(images, 1):
            if self.profile.label_images:
                content.append({"type": "text", "text": f"ATTACHED IMAGE {index}: {Path(path).name}. Identify its role/event using image_order in the packet."})
            content.append(_image_block(path, anthropic=False))
        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": self.profile.max_tokens,
        }
        if self.profile.thinking is not None:
            payload["thinking"] = self.profile.thinking
        if self.profile.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.profile.chat_template_kwargs is not None:
            payload["chat_template_kwargs"] = self.profile.chat_template_kwargs
        result = self._post(
            endpoint,
            payload,
            {"Authorization": f"Bearer {os.environ.get(self.profile.api_key_env, 'EMPTY')}"},
        )
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError(f"No choices in judge response: {result}")
        value = (choices[0].get("message") or {}).get("content")
        if isinstance(value, list):
            return "".join(
                str(row.get("text") or "") for row in value if isinstance(row, dict)
            )
        return str(value or "")

    def _anthropic(self, prompt: str, images: list[str]) -> str:
        endpoint = self.profile.base_url
        if not endpoint.endswith("/v1/messages"):
            endpoint += "/messages" if endpoint.endswith("/v1") else "/v1/messages"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for index, path in enumerate(images, 1):
            if self.profile.label_images:
                content.append({"type": "text", "text": f"ATTACHED IMAGE {index}: {Path(path).name}. Identify its role/event using image_order in the packet."})
            content.append(_image_block(path, anthropic=True))
        payload = {
            "model": self.profile.model,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": self.profile.max_tokens,
        }
        key = os.environ.get(self.profile.api_key_env, "")
        result = self._post(
            endpoint,
            payload,
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        return "".join(
            str(row.get("text") or "")
            for row in result.get("content") or []
            if isinstance(row, dict) and row.get("type") == "text"
        )

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.profile.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Judge endpoint returned a non-object response")
        return value


def build_client(
    config: dict[str, Any],
    profile_name: str,
    cache_root: str | Path,
) -> JudgeClient:
    profiles = config.get("profiles") or {}
    if profile_name not in profiles:
        raise KeyError(f"Unknown judge profile: {profile_name}")
    return JudgeClient(
        JudgeProfile.from_dict(profile_name, profiles[profile_name]), cache_root
    )

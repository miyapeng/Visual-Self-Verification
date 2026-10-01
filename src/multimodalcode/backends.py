from __future__ import annotations

import base64
import json
import mimetypes
import os
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import GenerationRequest


class ModelBackend(ABC):
    thread_safe = True

    @abstractmethod
    def generate(self, request: GenerationRequest) -> str:
        raise NotImplementedError


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class OpenAICompatibleBackend(ModelBackend):
    """Dependency-free client for vLLM/SGLang/LMDeploy/OpenAI-compatible APIs."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 600,
        extra_body: Optional[Dict[str, Any]] = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "EMPTY")
        self.timeout = timeout
        self.extra_body = extra_body or {}

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def generate(self, request: GenerationRequest) -> str:
        content: List[Dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for image_path in request.image_paths:
            content.append(
                {"type": "image_url", "image_url": {"url": _data_url(image_path)}}
            )
        messages: List[Dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": content})
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.seed is not None:
            payload["seed"] = int(request.seed)
        payload.update(self.extra_body)
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model endpoint returned HTTP {exc.code}: {detail}") from exc
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError(f"Model endpoint returned no choices: {result}")
        message = choices[0].get("message") or {}
        content_value = message.get("content")
        if isinstance(content_value, list):
            return "".join(
                str(part.get("text", ""))
                for part in content_value
                if isinstance(part, dict)
            )
        return str(content_value or "")


class EndpointPoolBackend(ModelBackend):
    """Round-robin requests across equivalent model-server replicas."""

    def __init__(self, backends: List[ModelBackend]):
        if not backends:
            raise ValueError("Endpoint pool requires at least one backend")
        self.backends = list(backends)
        self._lock = threading.Lock()
        self._next_index = 0

    def generate(self, request: GenerationRequest) -> str:
        with self._lock:
            start_index = self._next_index
            self._next_index = (self._next_index + 1) % len(self.backends)
        errors = []
        for offset in range(len(self.backends)):
            index = (start_index + offset) % len(self.backends)
            try:
                return self.backends[index].generate(request)
            except Exception as exc:
                errors.append(f"endpoint[{index}]: {type(exc).__name__}: {exc}")
        raise RuntimeError("All model endpoints failed: " + " | ".join(errors))


class CommandBackend(ModelBackend):
    """Adapter for arbitrary deployments through a JSON-over-stdin executable."""

    def __init__(self, command: str | List[str], timeout: float = 600):
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.command:
            raise ValueError("Command backend requires a command")
        self.timeout = timeout

    def generate(self, request: GenerationRequest) -> str:
        payload = {
            "prompt": request.prompt,
            "image_paths": request.image_paths,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system_prompt": request.system_prompt,
            "seed": request.seed,
        }
        result = subprocess.run(
            self.command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Backend command failed ({result.returncode}): {result.stderr.strip()}"
            )
        output = result.stdout.strip()
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return output
        if isinstance(parsed, dict) and "output" in parsed:
            return str(parsed["output"])
        return output


class TransformersBackend(ModelBackend):
    thread_safe = False

    def __init__(
        self,
        model: str,
        device_map: str = "auto",
        dtype: str = "bfloat16",
        trust_remote_code: bool = False,
    ):
        try:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Install the Transformers backend with: "
                "pip install torch transformers accelerate"
            ) from exc
        dtype_value = getattr(torch, dtype)
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(
            model, trust_remote_code=trust_remote_code
        )
        self.model = AutoModelForImageTextToText.from_pretrained(
            model,
            torch_dtype=dtype_value,
            device_map=device_map,
            trust_remote_code=trust_remote_code,
        )

    def generate(self, request: GenerationRequest) -> str:
        content: List[Dict[str, Any]] = []
        for image_path in request.image_paths:
            content.append({"type": "image", "image": image_path})
        content.append({"type": "text", "text": request.prompt})
        messages: List[Dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": content})
        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        with self.torch.no_grad():
            if request.seed is not None:
                self.torch.manual_seed(int(request.seed))
            generated = self.model.generate(
                **inputs,
                max_new_tokens=request.max_tokens,
                do_sample=request.temperature > 0,
                temperature=max(request.temperature, 1e-5),
            )
        prompt_length = inputs["input_ids"].shape[1]
        return self.processor.decode(
            generated[0][prompt_length:], skip_special_tokens=True
        )


class MockBackend(ModelBackend):
    def __init__(self, output: Optional[str] = None):
        self.output = output or (
            "<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body><main>MultimodalCode mock output</main></body></html>"
        )

    def generate(self, request: GenerationRequest) -> str:
        return self.output


def create_backend(
    backend: str,
    model: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 600,
    extra_body: Optional[Dict[str, Any]] = None,
    command: Optional[str] = None,
    trust_remote_code: bool = False,
) -> ModelBackend:
    normalized = backend.lower().replace("_", "-")
    if normalized in {"openai", "openai-compatible", "vllm", "sglang", "lmdeploy"}:
        selected_value = (
            base_url
            or os.environ.get("OPENAI_BASE_URLS")
            or os.environ.get("OPENAI_BASE_URL")
        )
        if not selected_value:
            raise ValueError("--base-url or OPENAI_BASE_URL is required")
        selected_urls = [
            value.strip() for value in selected_value.split(",") if value.strip()
        ]
        clients: List[ModelBackend] = [
            OpenAICompatibleBackend(
                model=model,
                base_url=selected_url,
                api_key=api_key,
                timeout=timeout,
                extra_body=extra_body,
            )
            for selected_url in selected_urls
        ]
        return clients[0] if len(clients) == 1 else EndpointPoolBackend(clients)
    if normalized == "transformers":
        return TransformersBackend(
            model=model, trust_remote_code=trust_remote_code
        )
    if normalized == "command":
        if not command:
            raise ValueError("--command is required for command backend")
        return CommandBackend(command, timeout=timeout)
    if normalized == "mock":
        return MockBackend()
    raise ValueError(f"Unsupported backend: {backend}")

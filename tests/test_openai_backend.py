from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from multimodalcode.backends import (
    EndpointPoolBackend,
    ModelBackend,
    OpenAICompatibleBackend,
    create_backend,
)
from multimodalcode.schema import GenerationRequest


class _Response:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.body


class _NamedBackend(ModelBackend):
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail

    def generate(self, request: GenerationRequest) -> str:
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return self.name


class OpenAIBackendTests(unittest.TestCase):
    def test_openai_compatible_request(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            return _Response(
                {"choices": [{"message": {"content": "<html>served</html>"}}]}
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            backend = OpenAICompatibleBackend(
                model="served-name",
                base_url="http://127.0.0.1:8000/v1",
                api_key="EMPTY",
            )
            output = backend.generate(
                GenerationRequest(
                    prompt="hello",
                    image_paths=[],
                    max_tokens=12,
                    temperature=0,
                    seed=7,
                )
            )
            self.assertEqual(output, "<html>served</html>")
            self.assertEqual(captured["url"], "http://127.0.0.1:8000/v1/chat/completions")
            self.assertEqual(captured["payload"]["model"], "served-name")
            self.assertEqual(captured["payload"]["seed"], 7)
            self.assertEqual(
                captured["payload"]["messages"][0]["content"][0]["text"], "hello"
            )

    def test_endpoint_pool_round_robin_and_failover(self) -> None:
        request = GenerationRequest(
            prompt="x", image_paths=[], max_tokens=8, temperature=0
        )
        pool = EndpointPoolBackend(
            [_NamedBackend("first"), _NamedBackend("second")]
        )
        self.assertEqual(
            [pool.generate(request) for _ in range(4)],
            ["first", "second", "first", "second"],
        )
        failover = EndpointPoolBackend(
            [_NamedBackend("broken", fail=True), _NamedBackend("healthy")]
        )
        self.assertEqual(failover.generate(request), "healthy")

    def test_comma_separated_urls_create_pool(self) -> None:
        backend = create_backend(
            backend="vllm",
            model="served",
            base_url=(
                "http://127.0.0.1:8001/v1,"
                "http://127.0.0.1:8002/v1"
            ),
        )
        self.assertIsInstance(backend, EndpointPoolBackend)
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            return _Response({"choices": [{"message": {"content": "ok"}}]})

        request = GenerationRequest(
            prompt="x", image_paths=[], max_tokens=8, temperature=0
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for _ in range(4):
                self.assertEqual(backend.generate(request), "ok")
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:8001/v1/chat/completions",
                "http://127.0.0.1:8002/v1/chat/completions",
                "http://127.0.0.1:8001/v1/chat/completions",
                "http://127.0.0.1:8002/v1/chat/completions",
            ],
        )


if __name__ == "__main__":
    unittest.main()

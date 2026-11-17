"""Tests for the LLM clients: stub behavior and the real client shape.

The real client is tested with an httpx MockTransport, so no network and
no API key are needed. It proves the request is built correctly and the
response is parsed correctly.
"""

import json
import unittest

import httpx

from harness.config import Config
from harness.llm import OpenAICompatibleLLM, StubLLM, get_llm


class TestStubLLM(unittest.TestCase):
    def test_stub_marks_itself(self):
        llm = StubLLM()
        out = llm.complete("Task: do a thing.\n\nContext:\n- a: 1\n")
        self.assertIn("STUB DRAFT", out)
        self.assertIn("a: 1", out)  # the gathered context shows up

    def test_get_llm_returns_stub_without_key(self):
        config = Config.from_env()
        config.llm_api_key = None
        self.assertIsInstance(get_llm(config), StubLLM)

    def test_get_llm_returns_real_client_with_key(self):
        config = Config.from_env()
        config.llm_api_key = "sk-test"
        self.assertIsInstance(get_llm(config), OpenAICompatibleLLM)


class TestOpenAICompatibleLLM(unittest.TestCase):
    def test_complete_posts_correct_shape(self):
        # Patch OpenAICompatibleLLM.complete's httpx.Client usage by
        # monkeypatching httpx.Client for the duration of the call.
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode())
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hello back"}}]},
            )

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        httpx.Client = lambda *a, **k: real_client(*a, transport=transport, **k)  # type: ignore
        try:
            client = OpenAICompatibleLLM(
                api_key="sk-test",
                base_url="https://example.invalid/v1",
                model="test-model",
            )
            out = client.complete(
                "hi", system="be helpful", model="override-model"
            )
        finally:
            httpx.Client = real_client  # type: ignore
        self.assertEqual(out, "hello back")
        self.assertEqual(
            captured["url"], "https://example.invalid/v1/chat/completions"
        )
        self.assertEqual(captured["auth"], "Bearer sk-test")
        self.assertEqual(captured["body"]["model"], "override-model")
        self.assertEqual(
            captured["body"]["messages"],
            [
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hi"},
            ],
        )

    def test_complete_raises_on_bad_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"nope": True})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        httpx.Client = lambda *a, **k: real_client(*a, transport=transport, **k)  # type: ignore
        try:
            client = OpenAICompatibleLLM(api_key="sk-test")
            with self.assertRaises(RuntimeError):
                client.complete("hi")
        finally:
            httpx.Client = real_client  # type: ignore


if __name__ == "__main__":
    unittest.main()

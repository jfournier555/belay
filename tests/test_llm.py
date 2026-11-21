"""Tests for the LLM clients: stub behavior and the real client shape.

The real client is tested with an httpx MockTransport, so no network and
no API key are needed. It proves the request is built correctly and the
response is parsed correctly.

The default chain (Ollama if reachable, then a key provider, then the
stub) is tested with ollama_reachable patched: whether Ollama happens
to be running on the test machine must never change a test result.
"""

import json
import os
import unittest
from unittest import mock

import httpx

from harness.config import (
    OLLAMA_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    OPENAI_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    Config,
)
from harness.llm import (
    OpenAICompatibleLLM,
    StubLLM,
    _model_pulled,
    get_llm,
    http_client,
    is_ollama_base_url,
    ollama_models,
    ollama_reachable,
)


class TestStubLLM(unittest.TestCase):
    def test_stub_marks_itself(self):
        llm = StubLLM()
        out = llm.complete("Task: do a thing.\n\nContext:\n- a: 1\n")
        self.assertIn("STUB DRAFT", out)
        self.assertIn("a: 1", out)  # the gathered context shows up

    def test_stub_header_names_ollama_and_setup(self):
        # The stub is the labeled fallback, never the silent default:
        # its header says Ollama is not reachable and points at setup.
        self.assertIn("Ollama", StubLLM.STUB_HEADER)
        self.assertIn("setup", StubLLM.STUB_HEADER)

    @mock.patch("harness.llm.ollama_reachable", return_value=False)
    def test_get_llm_returns_stub_without_key(self, _mock_reachable):
        config = Config.from_env()
        config.llm_api_key = None
        self.assertIsInstance(get_llm(config), StubLLM)

    def test_get_llm_returns_real_client_with_key(self):
        config = Config.from_env()
        config.llm_api_key = "sk-test"
        self.assertIsInstance(get_llm(config), OpenAICompatibleLLM)


class TestGetLlmResolution(unittest.TestCase):
    """The v1.12.0 default chain: Ollama if reachable, then a key
    provider, then the stub. An explicitly configured hosted provider
    wins over a locally running Ollama."""

    def setUp(self):
        self._reachable = mock.patch("harness.llm.ollama_reachable")
        self.reachable = self._reachable.start()
        self.addCleanup(self._reachable.stop)
        self._models = mock.patch(
            "harness.llm.ollama_models", return_value=[OLLAMA_DEFAULT_MODEL]
        )
        self.models = self._models.start()
        self.addCleanup(self._models.stop)

    def _config(self, **overrides):
        config = Config(
            llm_base_url=OLLAMA_BASE_URL,
            llm_api_key=None,
            llm_model=OLLAMA_DEFAULT_MODEL,
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    def test_stub_mode_forces_stub_even_when_ollama_is_up(self):
        self.reachable.return_value = True
        config = self._config(stub_mode=True)
        self.assertIsInstance(get_llm(config), StubLLM)

    def test_ollama_reachable_no_key_uses_ollama(self):
        self.reachable.return_value = True
        client = get_llm(self._config())
        self.assertIsInstance(client, OpenAICompatibleLLM)
        self.assertEqual(client.base_url, OLLAMA_BASE_URL)
        self.assertEqual(client.model, OLLAMA_DEFAULT_MODEL)
        self.assertEqual(client.api_key, "ollama")
        # Local inference gets the long timeout: the first call loads
        # the model, and a slow machine can take minutes.
        self.assertEqual(client.timeout, 600.0)

    def test_ollama_unreachable_no_key_uses_stub(self):
        self.reachable.return_value = False
        self.assertIsInstance(get_llm(self._config()), StubLLM)

    def test_ollama_reachable_model_not_pulled_uses_stub(self):
        # The server answers but the configured model is not pulled:
        # picking Ollama would make every chat call 404, so the
        # chain falls through to the stub instead of crashing.
        self.reachable.return_value = True
        self.models.return_value = ["qwen2.5:0.5b"]
        self.assertIsInstance(get_llm(self._config()), StubLLM)

    def test_ollama_models_unknown_keeps_ollama(self):
        # The tags list did not answer: unknown, not empty. The old
        # behavior stands and Ollama is picked.
        self.reachable.return_value = True
        self.models.return_value = None
        client = get_llm(self._config())
        self.assertIsInstance(client, OpenAICompatibleLLM)
        self.assertEqual(client.base_url, OLLAMA_BASE_URL)

    def test_model_pulled_matching(self):
        self.assertTrue(_model_pulled(["llama3.1:8b"], "llama3.1:8b"))
        self.assertTrue(_model_pulled(["llama3.1:latest"], "llama3.1"))
        self.assertTrue(_model_pulled(None, "llama3.1:8b"))
        self.assertFalse(_model_pulled(["qwen2.5:0.5b"], "llama3.1:8b"))
        self.assertFalse(_model_pulled([], "llama3.1:8b"))

    def test_ollama_unreachable_key_with_default_base_uses_openai(self):
        # A legacy key with no explicit base URL is an OpenAI key.
        self.reachable.return_value = False
        client = get_llm(self._config(llm_api_key="sk-test"))
        self.assertIsInstance(client, OpenAICompatibleLLM)
        self.assertEqual(client.base_url, OPENAI_BASE_URL)
        self.assertEqual(client.model, OPENAI_DEFAULT_MODEL)

    def test_explicit_provider_wins_over_running_ollama(self):
        self.reachable.return_value = True
        client = get_llm(
            self._config(
                llm_base_url=OPENAI_BASE_URL,
                llm_api_key="sk-test",
                llm_model="gpt-4o-mini",
            )
        )
        self.assertIsInstance(client, OpenAICompatibleLLM)
        self.assertEqual(client.base_url, OPENAI_BASE_URL)
        self.assertEqual(client.model, "gpt-4o-mini")

    def test_custom_ollama_host_probed_at_its_own_url(self):
        self.reachable.return_value = True
        client = get_llm(
            self._config(llm_base_url="http://192.168.1.5:11434/v1")
        )
        self.assertIsInstance(client, OpenAICompatibleLLM)
        self.assertEqual(client.base_url, "http://192.168.1.5:11434/v1")
        self.reachable.assert_called_with("http://192.168.1.5:11434/v1")


class TestOllamaHelpers(unittest.TestCase):
    def test_is_ollama_base_url(self):
        self.assertTrue(is_ollama_base_url("http://localhost:11434/v1"))
        self.assertTrue(is_ollama_base_url("http://192.168.1.5:11434/v1"))
        self.assertFalse(is_ollama_base_url("https://api.openai.com/v1"))
        self.assertFalse(is_ollama_base_url("not a url"))

    def _mock_client(self, handler):
        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        httpx.Client = lambda *a, **k: real_client(*a, transport=transport, **k)  # type: ignore
        return real_client

    def test_ollama_reachable_true_on_200(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "http://localhost:11434/v1/models")
            return httpx.Response(200, json={"data": []})

        real_client = self._mock_client(handler)
        try:
            self.assertTrue(ollama_reachable())
        finally:
            httpx.Client = real_client  # type: ignore

    def test_ollama_reachable_false_on_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        real_client = self._mock_client(handler)
        try:
            self.assertFalse(ollama_reachable())
        finally:
            httpx.Client = real_client  # type: ignore

    def test_ollama_models_lists_pulled_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).endswith("/api/tags"))
            return httpx.Response(
                200,
                json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:0.5b"}]},
            )

        real_client = self._mock_client(handler)
        try:
            self.assertEqual(
                ollama_models(), ["llama3.1:8b", "qwen2.5:0.5b"]
            )
        finally:
            httpx.Client = real_client  # type: ignore

    def test_ollama_models_none_when_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        real_client = self._mock_client(handler)
        try:
            self.assertIsNone(ollama_models())
        finally:
            httpx.Client = real_client  # type: ignore


class TestHttpClient(unittest.TestCase):
    """http_client: loopback never proxied, external targets use the
    environment's egress proxy, and a NO_PROXY carrying bracketed
    IPv6 entries ([::1]) must not break any call. httpx 0.28.1 raises
    InvalidURL parsing that entry, so the client never lets httpx
    read the proxy env vars itself."""

    def test_loopback_never_proxied(self):
        captured: dict = {}
        real_client = httpx.Client

        def fake_client(*args, **kwargs):
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        httpx.Client = fake_client  # type: ignore
        try:
            with mock.patch.dict(
                "os.environ",
                {"HTTPS_PROXY": "http://proxy.invalid:3128"},
            ):
                http_client(5.0, "http://localhost:11434/v1").close()
        finally:
            httpx.Client = real_client  # type: ignore
        self.assertFalse(captured.get("trust_env"))
        self.assertNotIn("proxy", captured)

    def test_external_uses_env_proxy(self):
        captured: dict = {}
        real_client = httpx.Client

        def fake_client(*args, **kwargs):
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        httpx.Client = fake_client  # type: ignore
        try:
            with mock.patch.dict(
                "os.environ",
                {"HTTPS_PROXY": "http://proxy.invalid:3128"},
            ):
                http_client(5.0, "https://api.openai.com/v1").close()
        finally:
            httpx.Client = real_client  # type: ignore
        self.assertFalse(captured.get("trust_env"))
        self.assertEqual(captured.get("proxy"), "http://proxy.invalid:3128")

    def test_bracketed_ipv6_no_proxy_does_not_break_localhost(self):
        # The regression: NO_PROXY with [::1] made httpx raise
        # InvalidURL before any request was made.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        httpx.Client = lambda *a, **k: real_client(*a, transport=transport, **k)  # type: ignore
        try:
            with mock.patch.dict(
                "os.environ",
                {
                    "NO_PROXY": "localhost,127.0.0.1,::1,[::1]",
                    "HTTPS_PROXY": "http://proxy.invalid:3128",
                },
            ):
                self.assertTrue(ollama_reachable())
        finally:
            httpx.Client = real_client  # type: ignore


class TestOpenAICompatibleLLM(unittest.TestCase):
    def test_complete_posts_correct_shape(self):
        # Patch the http_client seam (llm.py routes client
        # construction through it), not httpx.Client: http_client
        # passes a proxy kwarg for external URLs, which would fight
        # a MockTransport patched onto httpx.Client.
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
        fake_http_client = lambda timeout, url: httpx.Client(  # noqa: E731
            timeout=timeout, transport=transport
        )
        with mock.patch("harness.llm.http_client", fake_http_client):
            client = OpenAICompatibleLLM(
                api_key="sk-test",
                base_url="https://example.invalid/v1",
                model="test-model",
            )
            out = client.complete(
                "hi", system="be helpful", model="override-model"
            )
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
        fake_http_client = lambda timeout, url: httpx.Client(  # noqa: E731
            timeout=timeout, transport=transport
        )
        with mock.patch("harness.llm.http_client", fake_http_client):
            client = OpenAICompatibleLLM(api_key="sk-test")
            with self.assertRaises(RuntimeError):
                client.complete("hi")


if __name__ == "__main__":
    unittest.main()

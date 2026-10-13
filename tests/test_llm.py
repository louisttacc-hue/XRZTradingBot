"""Testet die Werkzeug-Schleifen der KI-Anbieter mit simulierten Antworten."""
import json
from types import SimpleNamespace

import httpx
import pytest

from xrz.ai.llm import AnthropicProvider, LLMRouter, OllamaProvider, ToolSpec
from xrz.config import Settings


class FakeSecrets:
    def __init__(self, **kw):
        self.d = kw

    def get(self, k):
        return self.d.get(k, "")


def _tools(log):
    async def price(a):
        log.append(a)
        return f"Preis {a['symbol']}: 100"

    return [ToolSpec("get_price", "Preis", {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}, price)]


async def test_anthropic_tool_loop():
    settings = Settings()
    prov = AnthropicProvider(lambda: settings, FakeSecrets(anthropic_api_key="sk-test"))
    calls = []

    class FakeMessages:
        async def create(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return SimpleNamespace(stop_reason="tool_use", content=[
                    SimpleNamespace(type="thinking", thinking=""),
                    SimpleNamespace(type="tool_use", id="tu_1", name="get_price", input={"symbol": "BTC"}),
                ])
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="BTC steht bei 100.")])

    prov._client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()), messages=FakeMessages())
    prov._client_key = "sk-test"
    tool_log = []
    out = await prov.agent("sys", [{"role": "assistant", "content": "alt"}, {"role": "user", "content": "hi"}], "Preis BTC?", _tools(tool_log))
    assert out == "BTC steht bei 100." and tool_log == [{"symbol": "BTC"}]
    first, second = calls
    # Standardmodell mit serverseitigem Fallback und Effort
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["output_config"] == {"effort": "medium"}
    assert first["messages"][0]["role"] == "user"  # führende Assistant-Nachricht entfernt
    assert "thinking" not in first
    # Zweiter Aufruf: Assistant-Inhalt unverändert angehängt + tool_result in EINER User-Nachricht
    msgs = second["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["content"][1].type == "tool_use"
    assert msgs[-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "Preis BTC: 100"}]}
    assert second["system"] == first["system"] and second["tools"] == first["tools"]


async def test_anthropic_refusal_is_handled():
    settings = Settings()
    prov = AnthropicProvider(lambda: settings, FakeSecrets(anthropic_api_key="k"))

    class FakeMessages:
        async def create(self, **kw):
            return SimpleNamespace(stop_reason="refusal", content=[])

    prov._client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    prov._client_key = "k"
    assert "nicht beantworten" in await prov.agent("s", [], "x", [])


@pytest.fixture
def mock_httpx(monkeypatch):
    handlers = {}
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(lambda req: handlers["fn"](req)), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return handlers


async def test_ollama_tool_loop(mock_httpx):
    settings = Settings()
    requests = []

    def fn(req):
        body = json.loads(req.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "get_price", "arguments": {"symbol": "ETH"}}}]}})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "<think>…</think>ETH: 100"}})

    mock_httpx["fn"] = fn
    prov = OllamaProvider(lambda: settings)
    log = []
    out = await prov.agent("sys", [], "ETH?", _tools(log))
    assert out == "ETH: 100" and log == [{"symbol": "ETH"}]
    assert requests[0]["model"] == "qwen3:8b" and requests[0]["tools"][0]["function"]["name"] == "get_price"
    assert requests[1]["messages"][-1]["role"] == "tool"


async def test_ollama_model_without_tools_falls_back(mock_httpx):
    settings = Settings()
    seen = []

    def fn(req):
        body = json.loads(req.content)
        seen.append("tools" in body)
        if "tools" in body:
            return httpx.Response(400, json={"error": "model does not support tools"})
        return httpx.Response(200, json={"message": {"content": "ohne Werkzeuge"}})

    mock_httpx["fn"] = fn
    out = await OllamaProvider(lambda: settings).agent("s", [], "x", _tools([]))
    assert out == "ohne Werkzeuge" and seen == [True, False]


async def test_router_falls_back_to_local(mock_httpx):
    settings = Settings()
    mock_httpx["fn"] = lambda req: httpx.Response(200, json={"message": {"content": "lokal"}})
    router = LLMRouter(lambda: settings, FakeSecrets(anthropic_api_key="k"))

    async def broken(*a, **kw):
        from xrz.ai.llm import LLMError
        raise LLMError("offline")

    router.anthropic.complete = broken
    assert await router.complete("s", "p") == "lokal"
    assert "Ollama" in router.last_used
    assert [p.name for p in router.order()] == ["anthropic", "ollama"]

"""Sprachmodelle: lokal (Ollama) oder – wenn API-Keys eingetragen sind – Claude
(Anthropic) bzw. OpenAI-kompatible Anbieter. Mit automatischem Ausweichen,
falls ein Anbieter nicht erreichbar ist."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler


class LLMError(RuntimeError):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean_text(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def extract_json(text: str) -> dict[str, Any] | None:
    """Holt das erste JSON-Objekt aus einer Modellantwort (auch aus ```json-Blöcken)."""
    text = clean_text(text)
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
        start = text.find("{", start + 1)
        if len(candidates) > 5:
            break
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


async def _run_tool(tools: dict[str, ToolSpec], name: str, args: Any) -> tuple[str, bool]:
    spec = tools.get(name)
    if spec is None:
        return f"Unbekanntes Werkzeug: {name}", True
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return "Ungültige JSON-Argumente", True
    if not isinstance(args, dict):
        args = {}
    try:
        out = await spec.handler(args)
        return (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str))[:12000], False
    except Exception as exc:
        log.warning("Werkzeug %s fehlgeschlagen: %s", name, exc)
        return f"Fehler in {name}: {exc}", True


# --------------------------------------------------------------------------- #
# Ollama (lokal)
# --------------------------------------------------------------------------- #
class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Callable[[], Settings]):
        self._settings = settings
        self._models_cache: tuple[float, list[str]] = (0.0, [])

    @property
    def s(self):
        return self._settings().ai

    def label(self) -> str:
        return f"Lokale KI (Ollama · {self.s.ollama_model})"

    def configured(self) -> bool:
        return bool(self.s.ollama_model)

    async def models(self) -> list[str]:
        ts, cached = self._models_cache
        if time.time() - ts < 60:
            return cached
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.s.ollama_url}/api/tags")
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            names = []
        self._models_cache = (time.time(), names)
        return names

    async def has_model(self, model: str) -> bool:
        names = await self.models()
        return any(n == model or n.split(":")[0] == model or n == f"{model}:latest" for n in names)

    async def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("stream", False)
        payload.setdefault("options", {"temperature": self._settings().ai.temperature, "num_ctx": self.s.ollama_num_ctx})
        payload.setdefault("think", bool(self.s.ollama_think))
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
            try:
                r = await c.post(f"{self.s.ollama_url}/api/chat", json=payload)
            except httpx.ConnectError as exc:
                raise LLMError("Ollama läuft nicht (bitte 'ollama serve' bzw. die Ollama-App starten)") from exc
            if r.status_code >= 400:
                body = r.text[:300]
                if "think" in body.lower() and "think" in payload:
                    payload.pop("think")
                    r = await c.post(f"{self.s.ollama_url}/api/chat", json=payload)
                    if r.status_code < 400:
                        return r.json()
                    body = r.text[:300]
                if "tools" in body.lower() and "tools" in payload:
                    raise LLMError("NO_TOOLS")
                if r.status_code == 404:
                    raise LLMError(f"Modell '{payload['model']}' fehlt – bitte 'ollama pull {payload['model']}' ausführen")
                raise LLMError(f"Ollama-Fehler {r.status_code}: {body}")
            return r.json()

    def _model_for(self, images: list[bytes] | None) -> str:
        if images:
            if not self.s.ollama_vision_model:
                raise LLMError("Kein lokales Bild-Modell eingestellt (ai.ollama_vision_model, z. B. qwen2.5vl:7b)")
            return self.s.ollama_vision_model
        return self.s.ollama_model

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None, max_tokens: int = 2000) -> str:
        msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [base64.b64encode(i).decode() for i in images]
        data = await self._chat({"model": self._model_for(images), "messages": [{"role": "system", "content": system}, msg]})
        return clean_text(data.get("message", {}).get("content", ""))

    async def agent(
        self, system: str, history: list[dict[str, str]], user_text: str, tools: list[ToolSpec],
        images: list[bytes] | None = None, max_steps: int = 6,
    ) -> str:
        model = self._model_for(images)
        user_msg: dict[str, Any] = {"role": "user", "content": user_text}
        if images:
            user_msg["images"] = [base64.b64encode(i).decode() for i in images]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history, user_msg]
        tool_map = {t.name: t for t in tools}
        tool_defs = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]
        use_tools = bool(tools) and not images
        for _ in range(max_steps):
            payload: dict[str, Any] = {"model": model, "messages": messages}
            if use_tools:
                payload["tools"] = tool_defs
            try:
                data = await self._chat(payload)
            except LLMError as exc:
                if str(exc) == "NO_TOOLS":
                    use_tools = False
                    continue
                raise
            msg = data.get("message", {})
            calls = msg.get("tool_calls") or []
            if not calls:
                return clean_text(msg.get("content", ""))
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                out, _ = await _run_tool(tool_map, fn.get("name", ""), fn.get("arguments", {}))
                messages.append({"role": "tool", "content": out, "tool_name": fn.get("name", "")})
        messages.append({"role": "user", "content": "Bitte fasse jetzt deine Antwort ohne weitere Werkzeuge zusammen."})
        data = await self._chat({"model": model, "messages": messages})
        return clean_text(data.get("message", {}).get("content", ""))

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        model = self.s.ollama_embed_model
        if not model:
            return None
        async with httpx.AsyncClient(timeout=120) as c:
            try:
                r = await c.post(f"{self.s.ollama_url}/api/embed", json={"model": model, "input": texts})
                if r.status_code == 200:
                    return r.json().get("embeddings")
            except httpx.HTTPError:
                return None
        return None


# --------------------------------------------------------------------------- #
# Anthropic (Claude)
# --------------------------------------------------------------------------- #
# Modelle, die serverseitige Fallbacks (bei Ablehnungen durch Sicherheitsfilter) unterstützen
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
_NO_EFFORT = ("claude-haiku-4", "claude-sonnet-4-5", "claude-3")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Callable[[], Settings], secrets: Any):
        self._settings = settings
        self.secrets = secrets
        self._client: Any = None
        self._client_key = ""
        self._disabled_params: set[str] = set()

    @property
    def s(self):
        return self._settings().ai

    def label(self) -> str:
        return f"Claude ({self.s.anthropic_model})"

    def configured(self) -> bool:
        return bool(self.secrets.get("anthropic_api_key"))

    def _get_client(self):
        key = self.secrets.get("anthropic_api_key")
        if not key:
            raise LLMError("Kein Anthropic API-Key eingetragen")
        if self._client is None or key != self._client_key:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=180.0)
            self._client_key = key
        return self._client

    async def _create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, max_tokens: int):
        import anthropic

        client = self._get_client()
        model = self.s.anthropic_model
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            params["tools"] = tools
        if "effort" not in self._disabled_params and not model.startswith(_NO_EFFORT):
            params["output_config"] = {"effort": self.s.anthropic_effort}
        use_fallbacks = model in _FALLBACK_MODELS and "fallbacks" not in self._disabled_params
        try:
            if use_fallbacks:
                return await client.beta.messages.create(
                    **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            return await client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic API-Key ungültig") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("Anthropic API-Key hat keine Berechtigung für dieses Modell") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Modell '{model}' nicht gefunden") from exc
        except anthropic.BadRequestError as exc:
            msg = str(exc).lower()
            if use_fallbacks and "fallback" in msg:
                self._disabled_params.add("fallbacks")
                return await self._create(system, messages, tools, max_tokens)
            if "output_config" in params and ("effort" in msg or "output_config" in msg):
                self._disabled_params.add("effort")
                return await self._create(system, messages, tools, max_tokens)
            raise LLMError(f"Anthropic-Anfrage abgelehnt: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic: Rate-Limit erreicht, bitte kurz warten") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Anthropic nicht erreichbar (Internet?)") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic-Fehler {exc.status_code}") from exc

    @staticmethod
    def _text(resp: Any) -> str:
        return "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    @staticmethod
    def _user_content(text: str, images: list[bytes] | None) -> Any:
        if not images:
            return text
        blocks: list[dict[str, Any]] = []
        for img in images:
            media = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                        "data": base64.standard_b64encode(img).decode()}})
        blocks.append({"type": "text", "text": text})
        return blocks

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None, max_tokens: int = 4000) -> str:
        resp = await self._create(system, [{"role": "user", "content": self._user_content(prompt, images)}], None, max(max_tokens, 16000))
        if resp.stop_reason == "refusal":
            raise LLMError("Claude hat die Anfrage abgelehnt")
        return self._text(resp)

    async def agent(
        self, system: str, history: list[dict[str, str]], user_text: str, tools: list[ToolSpec],
        images: list[bytes] | None = None, max_steps: int = 8,
    ) -> str:
        # Verlauf nur als Text (append-only innerhalb eines Durchlaufs; System & Tools bleiben gleich)
        messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in history if m.get("content")]
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        messages.append({"role": "user", "content": self._user_content(user_text, images)})
        tool_map = {t.name: t for t in tools}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        resp = None
        for _ in range(max_steps):
            resp = await self._create(system, messages, tool_defs or None, 16000)
            if resp.stop_reason == "refusal":
                return "Diese Anfrage kann ich so nicht beantworten."
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            if resp.stop_reason != "tool_use":
                return self._text(resp)
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    out, is_err = await _run_tool(tool_map, block.name, block.input)
                    item: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": out}
                    if is_err:
                        item["is_error"] = True
                    results.append(item)
            messages.append({"role": "user", "content": results})
        return self._text(resp) if resp is not None else ""

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None  # Anthropic bietet keine Embeddings → lokale/OpenAI-Embeddings oder Stichwortsuche


# --------------------------------------------------------------------------- #
# OpenAI-kompatibel (OpenAI, OpenRouter, Groq, DeepSeek, LM Studio, ...)
# --------------------------------------------------------------------------- #
class OpenAICompatProvider:
    name = "openai"

    def __init__(self, settings: Callable[[], Settings], secrets: Any):
        self._settings = settings
        self.secrets = secrets

    @property
    def s(self):
        return self._settings().ai

    def label(self) -> str:
        return f"OpenAI-kompatibel ({self.s.openai_model})"

    def configured(self) -> bool:
        return bool(self.secrets.get("openai_api_key"))

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        key = self.secrets.get("openai_api_key")
        if not key:
            raise LLMError("Kein OpenAI API-Key eingetragen")
        url = self.s.openai_base_url.rstrip("/") + path
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
            try:
                r = await c.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                raise LLMError(f"OpenAI-Anbieter nicht erreichbar: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"OpenAI-Fehler {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _user_content(text: str, images: list[bytes] | None) -> Any:
        if not images:
            return text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for img in images:
            media = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            parts.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{base64.b64encode(img).decode()}"}})
        return parts

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None, max_tokens: int = 2000) -> str:
        data = await self._post("/chat/completions", {
            "model": self.s.openai_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": self._user_content(prompt, images)}],
        })
        return clean_text(data["choices"][0]["message"].get("content") or "")

    async def agent(
        self, system: str, history: list[dict[str, str]], user_text: str, tools: list[ToolSpec],
        images: list[bytes] | None = None, max_steps: int = 8,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}, *history,
            {"role": "user", "content": self._user_content(user_text, images)},
        ]
        tool_map = {t.name: t for t in tools}
        tool_defs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        msg: dict[str, Any] = {}
        for _ in range(max_steps):
            body: dict[str, Any] = {"model": self.s.openai_model, "messages": messages}
            if tool_defs:
                body["tools"] = tool_defs
            data = await self._post("/chat/completions", body)
            msg = data["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return clean_text(msg.get("content") or "")
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for call in calls:
                out, _ = await _run_tool(tool_map, call["function"]["name"], call["function"].get("arguments", "{}"))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
        return clean_text(msg.get("content") or "")

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if "api.openai.com" not in self.s.openai_base_url:
            return None
        try:
            data = await self._post("/embeddings", {"model": "text-embedding-3-small", "input": texts})
            return [d["embedding"] for d in data["data"]]
        except LLMError:
            return None


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class LLMRouter:
    def __init__(self, settings: Callable[[], Settings], secrets: Any):
        self._settings = settings
        self.ollama = OllamaProvider(settings)
        self.anthropic = AnthropicProvider(settings, secrets)
        self.openai = OpenAICompatProvider(settings, secrets)
        self.last_used = ""
        self.last_error = ""

    def order(self, need_vision: bool = False) -> list[Any]:
        pref = self._settings().ai.provider
        cloud = [p for p in (self.anthropic, self.openai) if p.configured()]
        if pref == "auto":
            chain = cloud + [self.ollama]
        else:
            first = {"ollama": self.ollama, "anthropic": self.anthropic, "openai": self.openai}[pref]
            chain = [first] + [p for p in cloud + [self.ollama] if p is not first]
        if need_vision:
            chain = [p for p in chain if p is not self.ollama or self._settings().ai.ollama_vision_model]
        return chain

    def describe(self) -> str:
        chain = self.order()
        return " → ".join(p.label() for p in chain)

    async def _try(self, fn_name: str, need_vision: bool, *args, **kwargs) -> str:
        errors = []
        for p in self.order(need_vision):
            try:
                out = await getattr(p, fn_name)(*args, **kwargs)
                self.last_used = p.label()
                self.last_error = ""
                return out
            except LLMError as exc:
                errors.append(f"{p.label()}: {exc}")
            except Exception as exc:  # pragma: no cover - unerwartete Fehler
                log.exception("LLM-Anbieter %s", p.name)
                errors.append(f"{p.label()}: {exc}")
        self.last_error = " | ".join(errors) or "Kein KI-Anbieter verfügbar"
        raise LLMError(self.last_error)

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None, max_tokens: int = 2000) -> str:
        return await self._try("complete", bool(images), system, prompt, images=images, max_tokens=max_tokens)

    async def agent(self, system: str, history: list[dict[str, str]], user_text: str, tools: list[ToolSpec],
                    images: list[bytes] | None = None) -> str:
        return await self._try("agent", bool(images), system, history, user_text, tools, images=images)

    async def embed(self, texts: list[str]) -> tuple[str, list[list[float]]] | None:
        for p in (self.ollama, self.openai):
            try:
                vecs = await p.embed(texts)
            except Exception:
                vecs = None
            if vecs:
                model = self._settings().ai.ollama_embed_model if p is self.ollama else "text-embedding-3-small"
                return f"{p.name}:{model}", vecs
        return None

    async def health(self) -> dict[str, Any]:
        s = self._settings().ai
        models = await self.ollama.models()
        return {
            "chain": self.describe(),
            "ollama_running": bool(models) or await self._ollama_up(),
            "ollama_models": models,
            "ollama_model_ready": await self.ollama.has_model(s.ollama_model) if models else False,
            "anthropic_key": self.anthropic.configured(),
            "openai_key": self.openai.configured(),
            "last_used": self.last_used,
            "last_error": self.last_error,
        }

    async def _ollama_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self._settings().ai.ollama_url}/api/version")
                return r.status_code == 200
        except Exception:
            return False

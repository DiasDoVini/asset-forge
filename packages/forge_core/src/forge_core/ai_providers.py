"""IA do Asset Forge (assistente de jobs e chat): provedor local (Ollama) ou Claude (API da Anthropic), escolhidos nas
Opções, com um laço de agente que chama ferramentas do Asset Forge.

Formato neutro das mensagens (gravado no histórico do chat; convertido para cada provedor a cada chamada):
    {"role": "user", "text": str, "images": [caminho...]}
    {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "input"}], "raw": {...}}   # raw: conteúdo do provedor
    {"role": "tool", "tool_call_id": str, "name": str, "result": str, "images": [caminho...], "is_error": bool}
`raw` guarda o conteúdo original da resposta do Claude (blocos de raciocínio incluídos) para reenviar sem alteração
enquanto o provedor/modelo for o mesmo; com outro modelo a conversa é reconstruída do formato neutro.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from . import user_settings

CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_FALLBACK_OK = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}
MAX_IMAGE_SIDE = 768


class AIError(RuntimeError):
    pass


# ---------------------------------------------------------------- configuração

def settings(role: str) -> dict[str, Any]:
    """Provedor/modelo de um papel ("assistant" = diagnóstico de jobs, "chat" = chat do projeto)."""
    cfg = user_settings.get("ai", role, None) or {}
    provider = cfg.get("provider") or "auto"
    if provider == "auto":
        provider = "claude" if claude_available()[0] else "local"
    return {"provider": provider, "model": cfg.get("model") or "", "effort": cfg.get("effort") or "medium",
            "configured": cfg.get("provider") or "auto"}


def save_settings(role: str, **values: Any) -> dict[str, Any]:
    cur = user_settings.get("ai", role, None) or {}
    cur.update({k: v for k, v in values.items() if v is not None})
    for k, v in values.items():
        if v == "":
            cur.pop(k, None)
    user_settings.update("ai", **{role: cur})
    return settings(role)


def claude_key() -> str | None:
    return user_settings.get("ai", "claude_api_key") or None


def claude_available() -> tuple[bool, str]:
    if claude_key():
        return True, "chave salva nas Opções"
    from .critic import api_available

    return api_available()


def _client():
    import anthropic

    key = claude_key()
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=600) if key else anthropic.Anthropic(max_retries=3, timeout=600)


def claude_models() -> list[str]:
    """Modelos da conta (Models API); sem credencial, a lista conhecida."""
    ok, _ = claude_available()
    if not ok:
        return CLAUDE_MODELS
    try:
        ids = [m.id for m in _client().models.list()]
        return sorted(ids, key=lambda m: (m not in CLAUDE_MODELS, CLAUDE_MODELS.index(m) if m in CLAUDE_MODELS else 0, m))
    except Exception:  # noqa: BLE001 - lista indisponível não impede o uso
        return CLAUDE_MODELS


def describe() -> dict[str, Any]:
    from . import local_llm

    ok, how = claude_available()
    try:
        local = [m["name"] for m in local_llm.installed()]
        local_ok = True
    except local_llm.LocalLLMError:
        local, local_ok = [], False
    return {
        "assistant": settings("assistant"), "chat": settings("chat"),
        "claude": {"available": ok, "detail": how, "models": claude_models() if ok else CLAUDE_MODELS,
                   "key_saved": bool(claude_key()), "efforts": EFFORTS},
        "local": {"available": local_ok, "url": local_llm.URL, "models": local,
                  "default": (local_llm.pick_model() if local else None) if local_ok else None},
    }


# ---------------------------------------------------------------- ferramentas

@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Callable[[dict[str, Any]], "ToolResult"]


@dataclass
class ToolResult:
    text: str
    images: list[str] = field(default_factory=list)   # caminhos de imagem para o modelo ver
    is_error: bool = False
    data: dict[str, Any] = field(default_factory=dict)  # para a UI (ex.: asset criado)


def _image_b64(path: str) -> tuple[str, str] | None:
    from PIL import Image

    try:
        im = Image.open(path)
        im.load()
    except Exception:  # noqa: BLE001
        return None
    im = im.convert("RGBA")
    if max(im.size) > MAX_IMAGE_SIDE:
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    bg = Image.new("RGBA", im.size, (40, 40, 46, 255))   # transparência sobre cinza (o modelo vê o recorte)
    bg.alpha_composite(im)
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, "JPEG", quality=88)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- conversão por provedor

def _to_claude(history: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for m in history:
        if m["role"] == "tool":
            content: list[dict[str, Any]] = [{"type": "text", "text": m.get("result") or "(sem saída)"}]
            for p in m.get("images") or []:
                img = _image_b64(p)
                if img:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": img[0], "data": img[1]}})
            pending.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": content,
                            **({"is_error": True} if m.get("is_error") else {})})
            continue
        flush()
        if m["role"] == "user":
            content = []
            for p in m.get("images") or []:
                img = _image_b64(p)
                if img:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": img[0], "data": img[1]}})
            content.append({"type": "text", "text": m.get("text") or "…"})
            out.append({"role": "user", "content": content})
        elif m["role"] == "assistant":
            raw = m.get("raw") or {}
            if raw.get("provider") == "claude" and raw.get("model") == model and raw.get("content"):
                out.append({"role": "assistant", "content": raw["content"]})   # sem alterar (raciocínio preservado)
            else:
                content = []
                if m.get("text"):
                    content.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls") or []:
                    content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("input") or {}})
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": "…"}]})
    flush()
    return out


_VISION: dict[str, bool] = {}


def _ollama_vision(model: str) -> bool:
    """O modelo do Ollama aceita imagens? (capabilities de /api/show; guardado por modelo)"""
    if model not in _VISION:
        from . import local_llm

        try:
            caps = httpx.post(f"{local_llm.URL}/api/show", json={"model": model}, timeout=10).json().get("capabilities") or []
        except (httpx.HTTPError, ValueError):
            caps = []
        _VISION[model] = "vision" in caps
    return _VISION[model]


def _to_ollama(history: list[dict[str, Any]], system: str, vision: bool = True) -> list[dict[str, Any]]:
    if not vision:   # modelo sem visão: o Ollama recusa a requisição inteira se houver imagem
        history = [{**m, "images": [], **({"result": (m.get("result") or "") + "\n(prévia não enviada: este modelo não vê "
                                                     "imagens; não descreva detalhes visuais que você não viu)"}
                                          if m.get("role") == "tool" and m.get("images") else {})} for m in history]
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in history:
        if m["role"] == "user":
            msg: dict[str, Any] = {"role": "user", "content": m.get("text") or ""}
            imgs = [i for i in (_image_b64(p) for p in m.get("images") or []) if i]
            if imgs:
                msg["images"] = [i[1] for i in imgs]
            out.append(msg)
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("text") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"function": {"name": tc["name"], "arguments": tc.get("input") or {}}} for tc in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            msg = {"role": "tool", "content": m.get("result") or "", "tool_name": m.get("name")}
            imgs = [i for i in (_image_b64(p) for p in m.get("images") or []) if i]
            if imgs:   # modelos com visão (ex.: Qwen3.8) olham o resultado
                msg["images"] = [i[1] for i in imgs]
            out.append(msg)
    return out


# ---------------------------------------------------------------- uma chamada ao modelo

def _call_claude(model: str, effort: str, system: str, messages: list[dict[str, Any]], tools: list[Tool],
                 max_tokens: int = 16000, json_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    import anthropic

    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages,
                              "output_config": {"effort": effort if effort in EFFORTS else "medium"}}
    if tools:
        kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
    if json_schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": json_schema}
    client = _client()
    try:
        if model in _FALLBACK_OK:
            try:   # recusa de segurança: o servidor tenta outro modelo na mesma chamada
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                resp = client.messages.create(**kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise AIError("credencial do Claude recusada: confira a chave em Opções > Modelos e LLMs") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError(f"a conta não tem acesso a {model}") from e
    except anthropic.NotFoundError as e:
        raise AIError(f"modelo {model} não encontrado") from e
    except anthropic.RateLimitError as e:
        raise AIError("limite de uso da API do Claude atingido; tente daqui a pouco") from e
    except anthropic.APIConnectionError as e:
        raise AIError("sem conexão com a API do Claude") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"erro da API do Claude ({e.status_code}): {e.message}") from e
    text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    calls = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
    if resp.stop_reason == "refusal":
        text = (text + "\n\n" if text else "") + "(o modelo recusou este pedido)"
    elif resp.stop_reason == "max_tokens" and not calls:
        text = (text + "\n\n" if text else "") + "(resposta cortada no limite de tokens)"
    raw = {"provider": "claude", "model": model, "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content]}
    return {"text": text, "tool_calls": calls, "raw": raw, "stop": resp.stop_reason}


def _call_ollama(model: str, system: str, history: list[dict[str, Any]], tools: list[Tool],
                 json_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import local_llm

    body: dict[str, Any] = {"model": model, "stream": False, "messages": _to_ollama(history, system, _ollama_vision(model)),
                            "options": {"temperature": 0.6, "num_ctx": 32768}}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}}
                         for t in tools]
    if json_schema:
        body["format"] = json_schema
        body["think"] = False
    try:
        r = httpx.post(f"{local_llm.URL}/api/chat", json=body, timeout=600)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AIError(f"Ollama recusou ({e.response.status_code}): {e.response.text[:300]}") from None
    except httpx.HTTPError as e:
        raise AIError(f"Ollama indisponível em {local_llm.URL} ({e.__class__.__name__}): abra o Ollama") from None
    msg = r.json().get("message", {})
    import re
    import uuid

    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"id": "call_" + uuid.uuid4().hex[:10], "name": fn.get("name", ""), "input": args})
    return {"text": text, "tool_calls": calls, "raw": {"provider": "local", "model": model}, "stop": "tool_use" if calls else "end_turn"}


def resolve_model(cfg: dict[str, Any]) -> str:
    if cfg["provider"] == "claude":
        return cfg.get("model") or "claude-opus-5-5"
    from . import local_llm

    return cfg.get("model") or local_llm.pick_model()


def complete(cfg: dict[str, Any], system: str, history: list[dict[str, Any]], tools: list[Tool] | None = None,
             json_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Uma rodada do modelo (sem executar ferramentas)."""
    model = resolve_model(cfg)
    if cfg["provider"] == "claude":
        ok, how = claude_available()
        if not ok:
            raise AIError(f"Claude indisponível: {how}. Salve uma chave em Opções > Modelos e LLMs ou use o provedor local.")
        return _call_claude(model, cfg.get("effort") or "medium", system, _to_claude(history, model), tools or [],
                            json_schema=json_schema) | {"model": model}
    return _call_ollama(model, system, history, tools or [], json_schema=json_schema) | {"model": model}


def run_agent(cfg: dict[str, Any], system: str, history: list[dict[str, Any]], tools: list[Tool], *,
              on_message: Callable[[dict[str, Any]], None], cancel: Callable[[], bool] = lambda: False,
              max_steps: int = 12) -> None:
    """Laço do agente: chama o modelo, executa as ferramentas pedidas, devolve os resultados e repete até ele
    responder sem ferramentas. Cada mensagem nova vai para `on_message` (e é acrescentada a `history`)."""
    by_name = {t.name: t for t in tools}
    for _ in range(max_steps):
        if cancel():
            on_message({"role": "assistant", "text": "(interrompido)", "tool_calls": []})
            return
        out = complete(cfg, system, history, tools)
        msg = {"role": "assistant", "text": out["text"], "tool_calls": out["tool_calls"], "raw": out["raw"],
               "model": out["model"], "provider": cfg["provider"]}
        history.append(msg)
        on_message(msg)
        if not out["tool_calls"]:
            return
        for tc in out["tool_calls"]:
            tool = by_name.get(tc["name"])
            if tool is None:
                res = ToolResult(f"ferramenta desconhecida: {tc['name']}", is_error=True)
            else:
                try:
                    res = tool.run(tc.get("input") or {})
                except Exception as e:  # noqa: BLE001 - o erro volta para o modelo corrigir
                    res = ToolResult(f"erro: {type(e).__name__}: {e}", is_error=True)
            tmsg = {"role": "tool", "tool_call_id": tc["id"], "name": tc["name"], "result": res.text,
                    "images": res.images, "is_error": res.is_error, "data": res.data}
            history.append(tmsg)
            on_message(tmsg)
    on_message({"role": "assistant", "text": "(parei após muitas etapas seguidas; peça para continuar se quiser)", "tool_calls": []})


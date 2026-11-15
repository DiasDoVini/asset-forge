"""LLM local (Ollama) para escrever/expandir prompts — funciona offline e em projetos +18.

Escolha do modelo, nesta ordem: FORGE_LOCAL_LLM; o escolhido na tela de Opções (settings.json, seção local_llm);
senão, entre os instalados, um "abliterated"/sem censura em projetos +18 e, nos demais, o primeiro do catálogo
(Qwen3.8 primeiro); por fim, o primeiro instalado. FORGE_OLLAMA_URL muda o endereço (padrão http://127.0.0.1:11434).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator

import httpx

from . import user_settings
from .content import ADULT_SYSTEM_RULE, check_texts

URL = os.environ.get("FORGE_OLLAMA_URL", "http://127.0.0.1:11434")
PREFERRED_NSFW = ("abliterated", "uncensored")

# Modelos sugeridos na tela de Opções/Instalação (tags da biblioteca oficial do Ollama ou de autores conhecidos).
CATALOG: list[dict[str, Any]] = [
    {"id": "qwen3.8:27b", "title": "Qwen3.8 27B", "size_gb": 18, "recommended": True,
     "capabilities": ["vision", "tools", "thinking"], "released": "2026-08",
     "description": "O Qwen mais recente que roda localmente (Alibaba, ago/2026, Apache-2.0): denso, 256K de contexto, "
                    "entende imagem e vídeo. Com 16 GB de VRAM parte fica na RAM (mais lento, mas cabe)."},
    {"id": "qwen3:8b", "title": "Qwen3 8B", "size_gb": 5.2, "capabilities": ["tools", "thinking"], "released": "2025-04",
     "description": "Leve e rápido: cabe inteiro em GPUs de 8 GB. Bom para reescrever prompts curtos."},
    {"id": "huihui_ai/qwen3-abliterated:8b", "title": "Qwen3 8B abliterated", "size_gb": 5.0, "nsfw": True,
     "capabilities": ["tools", "thinking"], "released": "2025-05",
     "description": "Sem recusas (abliterated): usado em projetos +18, onde o modelo comum se recusa a descrever."},
]
_ORDER = [m["id"] for m in CATALOG]


class LocalLLMError(RuntimeError):
    pass


def _get(path: str, timeout: float = 3) -> dict[str, Any]:
    try:
        r = httpx.get(f"{URL}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise LocalLLMError(f"Ollama indisponível em {URL} ({e.__class__.__name__}). Abra o Ollama.") from None


def installed() -> list[dict[str, Any]]:
    """Modelos baixados no Ollama: nome, tamanho em bytes, família, quantização."""
    return [{"name": m["name"], "size": m.get("size", 0), "modified": m.get("modified_at"),
             "family": (m.get("details") or {}).get("family"), "params": (m.get("details") or {}).get("parameter_size"),
             "quant": (m.get("details") or {}).get("quantization_level")} for m in _get("/api/tags").get("models", [])]


def models() -> list[str]:
    return [m["name"] for m in installed()]


def _same(a: str, b: str) -> bool:
    return a == b or a == f"{b}:latest" or b == f"{a}:latest"


def chosen() -> str | None:
    """O modelo fixado (variável de ambiente ou tela de Opções), se houver."""
    return os.environ.get("FORGE_LOCAL_LLM") or user_settings.get("local_llm", "model")


def pick_model(nsfw: bool = False) -> str:
    ms = models()
    want = chosen()
    if want:
        hit = next((m for m in ms if _same(m, want)), None)
        if hit is None:
            raise LocalLLMError(f"o modelo escolhido ({want}) não está baixado no Ollama (tem: {ms}). "
                                "Baixe em Opções > Modelos ou escolha outro.")
        return hit
    if not ms:
        raise LocalLLMError("nenhum modelo no Ollama: baixe um em Opções > Modelos (sugestão: qwen3.8:27b)")
    if nsfw:
        hit = next((m for m in ms if any(k in m for k in PREFERRED_NSFW)), None)
        if hit:
            return hit
    for cid in _ORDER:
        hit = next((m for m in ms if _same(m, cid) and not (not nsfw and any(k in m for k in PREFERRED_NSFW))), None)
        if hit:
            return hit
    return next((m for m in ms if not any(k in m for k in PREFERRED_NSFW)), ms[0])


def status() -> dict[str, Any]:
    try:
        return {"available": True, "model": pick_model(), "url": URL}
    except LocalLLMError as e:
        return {"available": False, "detail": str(e), "url": URL}


def details() -> dict[str, Any]:
    """Tudo para a tela de Opções: versão do Ollama, baixados, carregados na memória, catálogo e escolha atual."""
    out: dict[str, Any] = {"url": URL, "env_model": os.environ.get("FORGE_LOCAL_LLM"),
                           "chosen": user_settings.get("local_llm", "model"), "catalog": CATALOG}
    try:
        out["version"] = _get("/api/version").get("version")
        out["installed"] = installed()
        out["running"] = [{"name": m["name"], "size": m.get("size", 0), "vram": m.get("size_vram", 0),
                           "expires": m.get("expires_at")} for m in _get("/api/ps").get("models", [])]
        out["online"] = True
        try:
            out["active"] = pick_model()
        except LocalLLMError as e:
            out["active"], out["detail"] = None, str(e)
    except LocalLLMError as e:
        out.update(online=False, detail=str(e), installed=[], running=[])
    return out


def pull(model: str) -> Iterator[dict[str, Any]]:
    """Baixa um modelo pelo Ollama, devolvendo o progresso ({status, total, completed}) linha a linha."""
    try:
        with httpx.stream("POST", f"{URL}/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.strip():
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise LocalLLMError(msg["error"])
                    yield msg
    except httpx.HTTPError as e:
        raise LocalLLMError(f"falha ao baixar {model}: {e}") from None


def delete(model: str) -> None:
    try:
        r = httpx.request("DELETE", f"{URL}/api/delete", json={"model": model}, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LocalLLMError(f"falha ao apagar {model}: {e}") from None


def unload(model: str) -> None:
    """Tira o modelo da memória (libera a VRAM para o ComfyUI)."""
    try:
        httpx.post(f"{URL}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30).raise_for_status()
    except httpx.HTTPError as e:
        raise LocalLLMError(f"falha ao descarregar {model}: {e}") from None


TARGETS = {
    "image": "a text-to-image prompt for a game asset (subject, pose, outfit, materials, lighting, framing); one paragraph, comma-separated details",
    "video": "a text-to-video prompt: subject, action and motion over time, camera movement, mood; one paragraph",
    "sfx": "a sound-effect prompt: the sound source, material, action, acoustic space, length; one sentence",
    "music": "music style tags: genre, instruments, mood, tempo; comma-separated",
    "edit": "a short, precise image-editing instruction (what to change, keep the rest)",
    "tags": "a concise English comma-separated visual description of the single object: what it is, shape, "
            "orientation, materials and colors (no style, quality or art-medium words, max 25 words)",
}


def assist(prompt: str, *, target: str = "image", nsfw: bool = False, style: str = "", language: str = "en") -> str:
    """Reescreve/expande `prompt` para o gerador. Em projetos +18 permite conteúdo adulto entre adultos."""
    if target not in TARGETS:
        target = "image"
    rules = [f"You write prompts for a game-asset generator. Rewrite the user's idea as {TARGETS[target]}.",
             f"Write in {'English' if language == 'en' else language}. Output ONLY the prompt text, no preamble, no quotes."]
    if style:
        rules.append(f"The project's global style is applied automatically; do not repeat it: {style[:300]}")
    if nsfw:
        rules.append("Adult content between consenting adults is allowed in this project; be explicit only if the user asks.")
        rules.append(ADULT_SYSTEM_RULE)
    else:
        rules.append("Keep it suitable for a general audience (no sexual content).")
    check_texts([prompt], nsfw)
    model = pick_model(nsfw)
    try:
        # think=False: Qwen3/3.8 respondem direto (sem a fase de raciocínio), como pede o modo não-pensante
        r = httpx.post(f"{URL}/api/chat", timeout=300, json={
            "model": model, "stream": False, "think": False,
            "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 400},
            "messages": [{"role": "system", "content": "\n".join(rules)}, {"role": "user", "content": prompt}],
        })
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LocalLLMError(f"falha no Ollama: {e}") from None
    text = r.json().get("message", {}).get("content", "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"').strip()
    check_texts([text], nsfw)  # a saída também passa pela política (menores: sempre recusado)
    return text

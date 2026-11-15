"""Crítico automático da geração avançada: Claude avalia as prévias de cada iteração e pede ajustes.

Credenciais: as do SDK (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN ou perfil do `ant auth login`).
Sem credenciais o motor cai para revisão manual (agente via MCP ou pessoa na UI).
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from .refine import Action, Refiner, RefineSession, Review, session_dir, summarize_history

if TYPE_CHECKING:
    from .project import Project

MODEL = os.environ.get("FORGE_CRITIC_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("FORGE_CRITIC_EFFORT", "high")
MAX_IMAGES = 10

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "0 a 10: quão bem o resultado atende ao brief e aos critérios"},
        "verdict": {"type": "string", "enum": ["accept", "revise", "restart"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "next_action": {
            "type": "object",
            "properties": {
                "pipeline": {"type": "string", "description": "id de uma ação permitida; vazio se verdict=accept"},
                "params_json": {"type": "string", "description": "objeto JSON com os parâmetros da ação"},
                "note": {"type": "string", "description": "por que esta ação resolve os problemas"},
            },
            "required": ["pipeline", "params_json", "note"],
            "additionalProperties": False,
        },
    },
    "required": ["score", "verdict", "summary", "issues", "next_action"],
    "additionalProperties": False,
}

SYSTEM = """Você é diretor de arte e especialista técnico em assets para jogos. Você conduz um laço de geração \
iterativa: a cada iteração recebe o brief, o histórico, as imagens de referência e prévias do resultado atual \
(para modelos 3D, renders de vários ângulos), avalia e decide a próxima ação.

Como avaliar:
- Compare o resultado com o brief, as referências e cada critério de aceitação. Seja específico e visual \
(o que está errado, onde, em quais vistas).
- score de 0 a 10. Aceite (verdict="accept") quando o resultado atende o brief de forma utilizável num jogo; \
não persiga perfeição que as ferramentas não alcançam.
- verdict="revise": ajuste a partir do resultado atual (use "$current" como entrada). verdict="restart": o \
resultado atual é um beco sem saída; gere de novo a partir das referências ("$reference"/"$references") com \
parâmetros ou prompt diferentes.
- Escolha UMA próxima ação do catálogo, a que mais provavelmente corrige os problemas mais graves. Não repita \
uma ação que já falhou do mesmo jeito; mude parâmetros, seed ou estratégia.
- params_json deve seguir o schema da ação. Placeholders aceitos: "$current", "$best", "$reference", "$references".
- Prompts e instruções dentro dos params em inglês (os modelos de geração entendem melhor); summary e issues em \
português, curtos e acionáveis."""


class CriticError(RuntimeError):
    pass


def api_available() -> tuple[bool, str]:
    """Heurística rápida (sem rede) para a UI: há alguma credencial configurada?"""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True, "variável de ambiente"
    if os.environ.get("ANTHROPIC_PROFILE") or (Path.home() / ".config" / "anthropic").exists():
        return True, "perfil do ant auth login"
    return False, "sem ANTHROPIC_API_KEY nem perfil `ant auth login` — use o modo manual (Claude Code via MCP ou você na UI)"


def _image_block(path: Path, max_side: int = 1024) -> dict[str, Any]:
    im = Image.open(path)
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (128, 128, 128, 255))  # cinza neutro: transparência visível
        bg.alpha_composite(im)
        im = bg
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    elif max(im.size) < 256:  # pixel art: amplia sem suavizar
        k = max(1, 256 // max(im.size))
        im = im.resize((im.width * k, im.height * k), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def _resolve_image(project: "Project", ref: str) -> Path | None:
    p = Path(ref)
    if p.suffix and p.exists():
        return p
    try:
        av = project.store.get(ref)
    except (FileNotFoundError, ValueError):
        return None
    files = project.store.files_of(av, "preview") or project.store.files_of(av, "primary")
    return next((f for f in files if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")), None)


def _catalog(refiner: Refiner) -> str:
    out = []
    for a in refiner.describe()["actions"]:
        if not a["available"]:
            continue
        props = (a["params_schema"] or {}).get("properties", {})
        brief_props = {k: {kk: v.get(kk) for kk in ("type", "description", "enum", "default") if v.get(kk) is not None}
                       for k, v in props.items()}
        out.append(json.dumps({"pipeline": a["pipeline"], "quando_usar": a["when"], "dicas": a["hints"],
                               "params": brief_props}, ensure_ascii=False))
    return "\n".join(out)


def build_messages(project: "Project", s: RefineSession, refiner: Refiner) -> list[dict[str, Any]]:
    it = s.iterations[-1]
    criteria = [*refiner.default_criteria, *s.brief.criteria]
    head = (
        f"BRIEF: {s.brief.prompt}\n"
        f"CRITÉRIOS DE ACEITAÇÃO:\n" + "\n".join(f"- {c}" for c in criteria) + "\n"
        f"NOTA-ALVO: {s.brief.target_score} | ITERAÇÃO {it.n} de no máximo {s.brief.max_iterations}\n"
        f"OPÇÕES DO REFINADOR: {json.dumps(s.brief.params, ensure_ascii=False)}\n\n"
        f"NOTAS DO DOMÍNIO ({refiner.title or refiner.id}):\n{refiner.critic_guidance()}\n\n"
        f"AÇÕES PERMITIDAS (uma por iteração):\n{_catalog(refiner)}\n\n"
        f"HISTÓRICO:\n{summarize_history(s)}\n\n"
        f"ITERAÇÃO ATUAL: ação {it.action.pipeline} com {json.dumps(it.resolved_params, ensure_ascii=False, default=str)}"
        + (f"\nERRO DA ITERAÇÃO: {it.error}" if it.error else "")
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": head}]
    budget = MAX_IMAGES
    for i, ref in enumerate(s.brief.references[:3]):
        path = _resolve_image(project, ref)
        if path and budget > 0:
            content += [{"type": "text", "text": f"Referência {i + 1} ({ref}):"}, _image_block(path, 768)]
            budget -= 1
    base = session_dir(project, s.id)
    for pv in it.previews[:budget]:
        content += [{"type": "text", "text": f"Prévia do resultado — {pv.label or pv.path}:"}, _image_block(base / pv.path)]
    content.append({"type": "text", "text": "Avalie a iteração atual e decida a próxima ação (JSON no formato pedido)."})
    return [{"role": "user", "content": content}]


def _call(client: Any, messages: list[dict[str, Any]]) -> Any:
    import anthropic

    kwargs: dict[str, Any] = dict(
        model=MODEL, max_tokens=16000, system=SYSTEM, messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
    )
    try:
        # fallback do lado do servidor: se o modelo recusar, outro modelo responde na mesma chamada
        return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        return client.messages.create(**kwargs)


def review_iteration(project: "Project", s: RefineSession, refiner: Refiner) -> Review:
    import anthropic

    from .content import is_nsfw

    if is_nsfw(project):  # imagens +18 não vão para a API: revisão manual na UI
        raise CriticError("projeto +18: o crítico automático (Claude API) fica desativado; revise manualmente na UI")

    from .pipeline import get_registry
    from .runner import validate_params

    try:
        client = anthropic.Anthropic(max_retries=3, timeout=600)
    except Exception as e:  # sem credencial resolvível
        raise CriticError(f"Claude API indisponível: {e}") from e
    messages = build_messages(project, s, refiner)
    allowed = {a.pipeline for a in refiner.actions()}
    for attempt in range(3):
        try:
            resp = _call(client, messages)
        except anthropic.AuthenticationError as e:
            raise CriticError("credencial da Claude API inválida") from e
        except anthropic.PermissionDeniedError as e:
            raise CriticError("credencial sem permissão para a Claude API") from e
        except anthropic.APIConnectionError as e:
            raise CriticError(f"sem conexão com a Claude API: {e}") from e
        except anthropic.APIStatusError as e:
            raise CriticError(f"Claude API respondeu {e.status_code}: {e.message}") from e
        if resp.stop_reason == "refusal":
            raise CriticError("o modelo recusou avaliar esta iteração")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
            na = data["next_action"]
            action = None
            if data["verdict"] != "accept":
                if na["pipeline"] not in allowed:
                    raise ValueError(f"pipeline {na['pipeline']!r} não está entre as ações permitidas {sorted(allowed)}")
                params = json.loads(na["params_json"] or "{}")
                if not isinstance(params, dict):
                    raise ValueError("params_json precisa ser um objeto JSON")
                probe = {k: ("x@v001" if isinstance(v, str) and v.startswith("$") else v) for k, v in params.items()}
                validate_params(get_registry().get(na["pipeline"]), probe)
                action = Action(pipeline=na["pipeline"], params=params, note=na["note"])
            return Review(score=float(data["score"]), verdict=data["verdict"], summary=data["summary"],
                          issues=list(data["issues"]), next_action=action, reviewer=f"claude-api:{resp.model}")
        except Exception as e:  # noqa: BLE001 - devolve o erro ao modelo e tenta de novo
            if attempt == 2:
                raise CriticError(f"resposta do crítico inválida: {e}") from e
            messages = [*messages, {"role": "assistant", "content": resp.content},
                        {"role": "user", "content": f"A próxima ação é inválida: {e}. Corrija e responda o JSON de novo."}]
    raise CriticError("crítico não produziu revisão válida")

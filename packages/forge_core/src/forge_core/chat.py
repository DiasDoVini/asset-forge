"""Chat do projeto: conversa com um modelo (local ou Claude) que controla o Asset Forge — consulta pipelines, gera e
edita assets pela fila de jobs normal e mostra os resultados na própria conversa.

As conversas ficam em `<projeto>/.forge/chats/<id>.json`; os assets gerados vão para o projeto como qualquer outro
(com versão, pasta e categoria). Projetos +18 não mandam prévias para o modelo (como no MCP).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import ai_providers as ai
from .ai_providers import Tool, ToolResult

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

SYSTEM = """Você é o assistente de criação do Asset Forge dentro do projeto "{name}". Você gera e edita assets de \
jogo (2D, pixel art, 3D, áudio, VFX, vídeo) chamando as ferramentas — elas rodam os pipelines de verdade e gravam \
cada resultado como asset do projeto.

Pipelines mais usados (veja todos com list_pipelines):
- imagem/sprite/ícone/objeto a partir de texto: 2d.text2d (params: prompt, asset_type; engine "qwen-image-2.1" quando \
houver texto escrito na imagem); já pixelizado: 2d.text2pixel
- editar uma imagem existente por instrução: 2d.edit (params: input_asset, instruction)
- pixelizar uma imagem existente: 2d.pixelize (params: input_asset, size); remover fundo: 2d.remove_bg; ampliar: 2d.enhance
- animar um sprite: 2d.animate; 3D a partir de imagem: 3d.image_to_3d; a partir de texto: 3d.text_to_3d
- personagem 3D animável (T-pose sem armas/capas, props à parte, esqueleto e animações da biblioteca): 3d.character (de uma imagem ou malha); só riggar uma malha em pé: 3d.character_rig; mais animações num personagem riggado: 3d.retarget
- efeito sonoro: audio.sfx; música: audio.music; som retrô: audio.retro; vídeo: video.generate

Como trabalhar:
- Antes de usar um pipeline pela primeira vez na conversa, chame pipeline_params para saber os nomes exatos dos \
parâmetros; depois rode com run_pipeline. Não invente ids de pipeline nem parâmetros.
- Quando o pedido é sobre um asset que já existe ("deixe o baú em pixel art", "tire o fundo da poção", "anime o \
herói"), use um pipeline que recebe esse asset em params.input_asset (2d.pixelize, 2d.remove_bg, 2d.edit, 2d.animate…) \
em vez de gerar outro do zero.
- Prompts de geração em inglês, descrevendo só o conteúdo: o estilo do projeto é aplicado automaticamente.
- Para editar ou derivar um asset existente, passe params.input_asset = "slug" ou "slug@v003" (list_assets/view_asset \
ajudam a achar). Edições gravam nova versão do mesmo asset; para uma variação separada passe `asset` com um slug novo.
- Depois de gerar, olhe o resultado (você recebe a prévia) e diga em 1–2 frases o que saiu; ofereça ajustes.
- Pedidos com várias peças: gere uma de cada vez e mostre todas.
- Se um job falhar, explique o erro em linguagem simples e tente corrigir (parâmetro, outro pipeline) no máximo uma vez.
- Respostas curtas, em português do Brasil.

Estilo do projeto: {style}
{system_prompt}"""


def _dir(project) -> Path:
    d = project.root / ".forge" / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(project, cid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8,32}", cid):
        raise KeyError(cid)
    return _dir(project) / f"{cid}.json"


def list_chats(project) -> list[dict[str, Any]]:
    out = []
    for f in sorted(_dir(project).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: c.get(k) for k in ("id", "title", "created", "updated", "provider", "model")}
                   | {"messages": len(c.get("messages", [])), "assets": sum(1 for m in c.get("messages", []) if (m.get("data") or {}).get("asset"))})
    return out


def load(project, cid: str) -> dict[str, Any]:
    p = _path(project, cid)
    if not p.exists():
        raise KeyError(cid)
    return json.loads(p.read_text(encoding="utf-8"))


def save(project, chat: dict[str, Any]) -> None:
    chat["updated"] = time.time()
    p = _path(project, chat["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def create(project, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    cfg = ai.settings("chat")
    chat = {"id": uuid.uuid4().hex[:12], "title": "Nova conversa", "created": time.time(),
            "provider": provider or cfg["provider"], "model": model if model is not None else cfg["model"],
            "effort": cfg["effort"], "messages": []}
    save(project, chat)
    return chat


def delete(project, cid: str) -> None:
    _path(project, cid).unlink(missing_ok=True)


# ---------------------------------------------------------------- ferramentas

def _file_url(project, av, path: str) -> str:
    return f"/files/{project.slug}/assets/{av.slug}/v{av.version:03d}/{path}"


def _asset_card(project, av) -> dict[str, Any]:
    pv = av.preview() or av.primary()
    prim = av.primary()
    return {"ref": av.ref, "slug": av.slug, "version": av.version, "name": av.name or av.slug, "kind": av.kind,
            "modality": av.modality, "project": project.slug,
            "preview": _file_url(project, av, pv.path) if pv else None, "preview_mime": pv.mime if pv else None,
            "primary": _file_url(project, av, prim.path) if prim else None, "primary_mime": prim.mime if prim else None,
            "folder": project.store.get_meta(av.slug).folder}


def _image_for_model(project, av) -> list[str]:
    from .content import is_nsfw

    if is_nsfw(project):
        return []
    for f in [av.preview(), av.primary()]:
        if f and f.path.lower().endswith(IMAGE_EXT):
            return [str(project.store.file_path(av, f))]
    return []


def _summary(project, av) -> str:
    d = {"ref": av.ref, "name": av.name, "kind": av.kind, "modality": av.modality, "pipeline": av.pipeline,
         "prompt": av.prompt, "files": [f.path for f in av.files], "folder": project.store.get_meta(av.slug).folder}
    for k in ("frames", "size", "duration", "model"):
        if (av.extra or {}).get(k) is not None:
            d[k] = av.extra[k]
    return json.dumps(d, ensure_ascii=False)


def tools_for(project, submit_and_wait: Callable[[dict[str, Any]], dict[str, Any]]) -> list[Tool]:
    from .pipeline import get_registry

    reg = get_registry()

    def project_info(_: dict[str, Any]) -> ToolResult:
        s = project.summary()
        st = s.get("style") or {}
        info = {"name": s.get("name"), "style_prompt": st.get("prompt"), "palette": st.get("palette"),
                "system_prompt": (st.get("system_prompt") or "")[:1500], "folders": s.get("folders"),
                "categories": [c.get("id") for c in s.get("categories") or []], "souls": st.get("souls"),
                "nsfw": s.get("nsfw")}
        return ToolResult(json.dumps(info, ensure_ascii=False))

    def list_pipelines(a: dict[str, Any]) -> ToolResult:
        mod = (a.get("modality") or "").strip()
        rows = []
        for p in reg.pipelines.values():
            if mod and p.modality != mod:
                continue
            rows.append({"id": p.id, "title": p.title, "modality": p.modality, "requires_input": p.requires_input,
                         "accepts_input": p.accepts_input, "description": (p.description or "")[:220]})
        return ToolResult(json.dumps(rows, ensure_ascii=False))

    def pipeline_params(a: dict[str, Any]) -> ToolResult:
        p = reg.get(a["pipeline"])
        schema = p.Params.model_json_schema()
        props = {}
        for k, v in (schema.get("properties") or {}).items():
            props[k] = {kk: v[kk] for kk in ("type", "description", "default", "enum", "minimum", "maximum", "anyOf") if kk in v}
        return ToolResult(json.dumps({"pipeline": p.id, "required": schema.get("required", []), "params": props}, ensure_ascii=False)[:12000])

    def run_pipeline(a: dict[str, Any]) -> ToolResult:
        params = dict(a.get("params") or {})
        for k in ("input_asset", "prompt", "instruction"):     # modelos menores às vezes põem fora de params
            if k in a and k not in params:
                params[k] = a[k]
        pipe = reg.get(a["pipeline"])
        allowed = set((pipe.Params.model_json_schema().get("properties") or {}).keys())
        unknown = sorted(set(params) - allowed)
        if unknown:
            return ToolResult(f"parâmetros que {pipe.id} não aceita: {', '.join(unknown)}. Aceitos: {', '.join(sorted(allowed))}. "
                              "Chame pipeline_params para os detalhes.", is_error=True)
        req = {"project": project.slug, "pipeline": pipe.id, "params": params,
               "asset": a.get("asset") or a.get("output_asset") or None, "name": a.get("name") or None,
               "folder": a.get("folder") or None}
        try:
            job = submit_and_wait(req)
        except Exception as e:  # noqa: BLE001 - validação/política: volta para o modelo corrigir
            return ToolResult(f"{type(e).__name__}: {e}. Parâmetros aceitos por {pipe.id}: {', '.join(sorted(allowed))}.", is_error=True)
        if job.get("status") != "done":
            return ToolResult(f"job {job.get('id')} terminou com {job.get('status')}: {job.get('error')}", is_error=True,
                              data={"job": job.get("id")})
        av = project.store.get(job["result"])
        return ToolResult(_summary(project, av), images=_image_for_model(project, av),
                          data={"asset": _asset_card(project, av), "job": job.get("id")})

    def list_assets(a: dict[str, Any]) -> ToolResult:
        rows = project.store.search(q=a.get("query") or "", kind=a.get("kind") or None, folder=a.get("folder") or None,
                                    recursive=True)[: int(a.get("limit") or 20)]
        out = [{"slug": r["slug"], "version": r["version"], "name": r.get("name"), "kind": r.get("kind"),
                "modality": r.get("modality"), "folder": r.get("folder"), "prompt": (r.get("prompt") or "")[:120]} for r in rows]
        return ToolResult(json.dumps(out, ensure_ascii=False))

    def view_asset(a: dict[str, Any]) -> ToolResult:
        av = project.store.get(a["ref"])
        return ToolResult(_summary(project, av), images=_image_for_model(project, av), data={"asset": _asset_card(project, av)})

    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    return [
        Tool("project_info", "Estilo, paleta, pastas e categorias do projeto.", obj({}), project_info),
        Tool("list_pipelines", "Lista os pipelines (id, título, modalidade, se precisam de asset de entrada).",
             obj({"modality": {"type": "string", "description": "2d, 3d, audio, vfx, video ou pixel (vazio = todos)"}}), list_pipelines),
        Tool("pipeline_params", "Parâmetros de um pipeline (tipos, padrões, opções).",
             obj({"pipeline": {"type": "string"}}, ["pipeline"]), pipeline_params),
        Tool("run_pipeline", "Roda um pipeline (gera ou edita) e espera o resultado; devolve o asset criado com a prévia. "
             "params.input_asset = 'slug' ou 'slug@v002' para editar/derivar de um asset.",
             obj({"pipeline": {"type": "string"}, "params": {"type": "object", "description": "parâmetros do pipeline"},
                  "asset": {"type": "string", "description": "slug do asset de saída (opcional)"},
                  "name": {"type": "string", "description": "nome legível (opcional)"},
                  "folder": {"type": "string", "description": "pasta (opcional, ex.: Personagens/Herói)"}}, ["pipeline", "params"]), run_pipeline),
        Tool("list_assets", "Busca assets do projeto por texto/tipo/pasta.",
             obj({"query": {"type": "string"}, "kind": {"type": "string"}, "folder": {"type": "string"},
                  "limit": {"type": "integer"}}), list_assets),
        Tool("view_asset", "Detalhes e prévia de um asset ('slug' ou 'slug@v003').", obj({"ref": {"type": "string"}}, ["ref"]), view_asset),
    ]


# ---------------------------------------------------------------- execução

class ChatRunner:
    """Uma rodada por conversa por vez, em thread; avisa a UI pelo WebSocket a cada mensagem."""

    def __init__(self, submit_and_wait_factory: Callable[[threading.Event], Callable[[dict[str, Any]], dict[str, Any]]],
                 notify: Callable[[dict[str, Any]], None]):
        self.factory = submit_and_wait_factory
        self.notify = notify
        self.running: dict[str, threading.Event] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def status(self, cid: str) -> dict[str, Any]:
        return {"running": cid in self.running, "error": self.errors.get(cid)}

    def cancel(self, cid: str) -> bool:
        ev = self.running.get(cid)
        if ev:
            ev.set()
        return bool(ev)

    def send(self, project, cid: str, text: str, *, provider: str | None = None, model: str | None = None,
             effort: str | None = None, images: list[str] | None = None) -> None:
        with self._lock:
            if cid in self.running:
                raise RuntimeError("esta conversa ainda está respondendo")
            cancel = threading.Event()
            self.running[cid] = cancel
        self.errors.pop(cid, None)
        chat = load(project, cid)
        if provider:
            chat["provider"] = provider
        if model is not None:
            chat["model"] = model
        if effort:
            chat["effort"] = effort
        if chat["title"] == "Nova conversa":
            chat["title"] = (text.strip().splitlines() or ["Conversa"])[0][:60]
        chat["messages"].append({"role": "user", "text": text, "images": images or [], "at": time.time()})
        save(project, chat)
        self.notify({"type": "chat", "project": project.slug, "chat": cid})
        threading.Thread(target=self._run, args=(project, cid, chat, cancel), name=f"chat-{cid}", daemon=True).start()

    def _run(self, project, cid: str, chat: dict[str, Any], cancel: threading.Event) -> None:
        try:
            cfg = {"provider": chat["provider"], "model": chat.get("model") or "", "effort": chat.get("effort") or "medium"}
            s = project.summary()
            st = s.get("style") or {}
            system = SYSTEM.format(name=s.get("name") or project.slug, style=(st.get("prompt") or "(sem estilo global)")[:600],
                                   system_prompt=("Orientações do projeto: " + st["system_prompt"][:2000]) if st.get("system_prompt") else "")
            history = [m for m in chat["messages"] if m.get("role") in ("user", "assistant", "tool")]
            tools = tools_for(project, self.factory(cancel))

            def on_message(m: dict[str, Any]) -> None:
                m = {**m, "at": time.time()}
                chat["messages"].append(m)
                if m.get("model"):
                    chat["model_used"] = m["model"]
                save(project, chat)
                self.notify({"type": "chat", "project": project.slug, "chat": cid})

            # history é a mesma lista que o agente estende; on_message grava a cópia com horário
            ai.run_agent(cfg, system, [dict(m) for m in history], tools, on_message=on_message, cancel=cancel.is_set)
        except Exception as e:  # noqa: BLE001 - o erro aparece na conversa
            self.errors[cid] = f"{type(e).__name__}: {e}"
            chat["messages"].append({"role": "error", "text": str(e), "at": time.time()})
            save(project, chat)
        finally:
            self.running.pop(cid, None)
            self.notify({"type": "chat", "project": project.slug, "chat": cid})

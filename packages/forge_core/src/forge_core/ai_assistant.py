"""AI Assistant: diagnostica um job que falhou (causa, passos para resolver e uma correção que a tela aplica).

Junta o contexto que um humano olharia: o pedido e o erro do job, o fim do log, o workflow que o ComfyUI rejeitou (com
os erros por nó), o estado das instâncias do ComfyUI, o que falta na Instalação e o schema dos parâmetros do pipeline.
Usa o provedor/modelo do papel "assistant" (Opções > Modelos e LLMs): local (Ollama) ou Claude.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from . import ai_providers as ai

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "uma frase: o que aconteceu, em português"},
        "cause": {"type": "string", "description": "a causa provável, explicada sem jargão (até 4 frases)"},
        "steps": {"type": "array", "items": {"type": "string"}, "description": "passos concretos para resolver, em ordem"},
        "fix": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["retry", "retry_with_params", "install", "start_instance", "restart_instance", "none"]},
                "label": {"type": "string", "description": "texto curto do botão, ex.: 'Repetir com 20 passos'"},
                "params": {"type": "array", "description": "só os parâmetros a trocar (retry_with_params)",
                           "items": {"type": "object", "properties": {"name": {"type": "string"},
                                                                      "value_json": {"type": "string", "description": "valor em JSON, ex.: 20, \"euler\", true"}},
                                     "required": ["name", "value_json"], "additionalProperties": False}},
                "item": {"type": "string", "description": "id do item da Instalação (install), ou vazio"},
                "instance": {"type": "string", "description": "desktop | 3d | trellis (start/restart_instance), ou vazio"},
            },
            "required": ["kind", "label", "params", "item", "instance"],
            "additionalProperties": False,
        },
        "confidence": {"type": "string", "enum": ["alta", "média", "baixa"]},
    },
    "required": ["summary", "cause", "steps", "fix", "confidence"],
    "additionalProperties": False,
}

SYSTEM = """Você é o assistente de diagnóstico do Asset Forge, uma ferramenta local que gera assets de jogos (2D, 3D, \
áudio, VFX, vídeo) com pipelines Python que mandam workflows para instâncias do ComfyUI (desktop :8188 para 2D/vídeo/\
áudio, 3d :8189 Hunyuan3D, trellis :8190 TRELLIS.2 — o Asset Forge usa outra instância no ar se a configurada não \
enxergar os modelos), LLMs locais pelo Ollama e o Claude pela API.

Diagnostique o job que falhou a partir do contexto. Seja específico: cite o nó, o arquivo de modelo ou o parâmetro \
culpado quando o contexto mostrar. Erros comuns: modelo ausente (value_not_in_list num loader: baixar em Instalação), \
instância parada ou sem a pasta de modelos, falta de VRAM (reduzir resolução/passos, liberar VRAM, parar outra \
instância), parâmetro fora da faixa, entrada ausente, política de conteúdo, dependência não instalada (ffmpeg etc.).

Proponha em `fix` UMA ação que a tela consegue fazer:
- retry: repetir igual (falha passageira);
- retry_with_params: repetir trocando só os parâmetros em `params` (nomes do schema do pipeline);
- install: abrir/instalar o item `item` da Instalação (use um id da lista dada);
- start_instance / restart_instance: subir ou reiniciar a instância `instance`;
- none: quando nada disso resolve (explique nos passos).
Responda em português do Brasil."""


def _job_context(job: dict[str, Any]) -> dict[str, Any]:
    from . import comfy_supervisor as cs
    from . import comfy_workflows as cw
    from .pipeline import get_registry

    req = job.get("request") or {}
    ctx: dict[str, Any] = {
        "job": {"id": job.get("id"), "pipeline": req.get("pipeline"), "project": req.get("project"),
                "params": req.get("params"), "status": job.get("status"), "error": job.get("error"),
                "created": job.get("created"), "source": job.get("source")},
        "log_tail": (job.get("logs") or job.get("log_tail") or [])[-25:],
    }
    try:
        p = get_registry().get(req.get("pipeline", ""))
        ctx["pipeline"] = {"title": p.title, "description": p.description,
                           "params_schema": p.Params.model_json_schema() if getattr(p, "Params", None) else {}}
    except Exception:  # noqa: BLE001
        pass
    # workflows deste job (mesmo projeto/pipeline, perto do horário do job)
    try:
        created = datetime.fromisoformat(str(job.get("created")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        created = time.time()
    finished = created + 3600
    wfs = []
    for item in cw.listing(150):
        m = item.get("meta") or {}
        if m.get("project") == req.get("project") and m.get("pipeline") == req.get("pipeline") \
                and item.get("created") and created - 5 <= item["created"] <= finished:
            wfs.append({"status": item["status"], "instance": item["instance"], "error": (item.get("error") or "")[:3000],
                        "models": item["models"], "nodes": item["summary"]})
    ctx["comfy_workflows"] = wfs[:4]
    # cada modelo citado: existe no disco? quais instâncias enxergam? (separa "falta baixar" de "instância sem a pasta")
    import re

    from_error = re.findall(r'received_value"?:\s*"([^"]+\.(?:safetensors|ckpt|pt|pth|gguf|bin))"', str(job.get("error") or ""))
    models = sorted({m for w in wfs for m in w["models"]} | set(from_error))
    if models:
        from .comfy import ComfyClient
        from .setup_catalog import models_dir

        root = models_dir()
        on_disk = {p.name for p in root.rglob("*") if p.is_file()} if root.exists() else set()
        seen: dict[str, list[str]] = {m: [] for m in models}
        for i in cs.INSTANCES:
            if not cs.port_open(i.port, 0.3):
                continue
            c = ComfyClient(i.url)
            listed = set()
            for folder in ("diffusion_models", "checkpoints", "text_encoders", "vae", "loras", "background_removal", "upscale_models"):
                listed |= set(c.models(folder))
            for m in models:
                if m in listed:
                    seen[m].append(i.name)
        ctx["models_check"] = [{"file": m, "on_disk_in_models_dir": m in on_disk, "visible_to_instances": seen[m]} for m in models]
        ctx["models_check_hint"] = ("on_disk true e a instância que rodou não está em visible_to_instances = a instância não "
                                    "enxerga a pasta de modelos (não é para baixar de novo)")
    insts = []
    for i in cs.INSTANCES:
        d = cs.details(i)
        insts.append({k: d.get(k) for k in ("name", "port", "installed", "listening", "ready", "autostart")}
                     | {"vram_free_gb": round(d["devices"][0]["vram_free"] / 1e9, 1) if d.get("devices") else None,
                        "queue": d.get("queue")})
    ctx["comfy_instances"] = insts
    try:
        from .setup_catalog import overview

        ov = overview()
        ctx["setup_not_ready"] = [{"id": it["id"], "title": it["title"], "state": it["state"], "detail": it["detail"],
                                   "needed_for": it["needed_for"]} for it in ov["items"] if it["state"] != "ok"]
        ctx["models_dir"] = ov["models_dir"]
    except Exception:  # noqa: BLE001
        pass
    return ctx


def diagnose(job: dict[str, Any], *, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    cfg = ai.settings("assistant")
    if provider:
        cfg["provider"] = provider
        cfg["model"] = model or ""
    elif model:
        cfg["model"] = model
    ctx = _job_context(job)
    prompt = "Contexto do job que falhou (JSON):\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1, default=str)[:60000] + "\n```"
    t = time.time()
    out = ai.complete(cfg, SYSTEM, [{"role": "user", "text": prompt}], json_schema=SCHEMA)
    text = out["text"].strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        result = json.loads(text)
    except ValueError:
        result = {"summary": "O modelo não devolveu um diagnóstico estruturado.", "cause": text[:2000], "steps": [],
                  "fix": {"kind": "none", "label": ""}, "confidence": "baixa"}
    fix = result.get("fix") or {"kind": "none", "label": ""}
    params: dict[str, Any] = {}
    for kv in fix.get("params") or []:
        if isinstance(kv, dict) and kv.get("name"):
            try:
                params[kv["name"]] = json.loads(kv.get("value_json", "null"))
            except ValueError:
                params[kv["name"]] = kv.get("value_json")
    fix["params"] = params
    if fix.get("kind") == "retry_with_params" and not params:
        fix["kind"] = "retry"
    if fix.get("kind") in ("start_instance", "restart_instance"):
        from . import comfy_supervisor as cs

        names = {i.name for i in cs.INSTANCES}
        inst = fix.get("instance") if fix.get("instance") in names else (fix.get("item") if fix.get("item") in names else "desktop")
        fix["instance"] = inst
        # já no ar: "iniciar" não muda nada; o que resolve é reiniciar (ex.: recarregar a pasta de modelos)
        if fix["kind"] == "start_instance" and cs.port_open(cs.get(inst).port, 0.3):
            fix["kind"] = "restart_instance"
    if fix.get("kind") == "install":
        from .setup_catalog import items

        ids = {i.id for i in items()}
        if fix.get("item") not in ids:
            fix["item"] = ""
    result["fix"] = fix
    result["provider"] = cfg["provider"]
    result["model"] = out["model"]
    result["seconds"] = round(time.time() - t, 1)
    return result

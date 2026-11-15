"""Rotas da tela de Opções: informações do sistema e controle das instâncias do ComfyUI (status, iniciar, parar,
reiniciar, liberar VRAM, interromper, fila, logs e "manter no ar" pelo supervisor do `forge up`)."""
from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import comfy_supervisor as cs
from .config import get_settings

START_HOLD = 300.0  # segundos em que o supervisor não mexe numa instância que o servidor está (re)iniciando


class ModelBody(BaseModel):
    model: str | None = None


class AutostartBody(BaseModel):
    value: bool


class FoldersBody(BaseModel):
    folders: list[str]


class StopBody(BaseModel):
    force: bool = False   # parar mesmo com a fila do ComfyUI rodando


def _inst(name: str) -> cs.Instance:
    try:
        return cs.get(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


async def _comfy_post(inst: cs.Instance, path: str, body: dict[str, Any] | None = None) -> None:
    if not cs.port_open(inst.port, 0.5):
        raise HTTPException(409, f"{inst.name} está parada")
    try:
        async with httpx.AsyncClient(base_url=inst.url, timeout=15) as c:
            r = await c.post(path, json=body or {})
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{inst.name}: {e}") from None


def build_router(started: str, boot_id: str, active_jobs: Callable[[], list[str]], pipelines: Callable[[], int],
                 errors: Callable[[], dict]) -> APIRouter:
    r = APIRouter()
    settings = get_settings()

    @r.get("/api/system")
    async def system(request: Request) -> dict[str, Any]:
        sup = cs.supervisor_info()
        return {
            "boot_id": boot_id, "started": started, "supervised": os.environ.get("FORGE_SUPERVISED") == "1",
            "comfy_supervisor": sup, "host": settings.host, "port": request.url.port or settings.port, "comfy_url": settings.comfy_url,
            "paths": {"projects": str(settings.projects_dir), "souls": str(settings.souls_dir), "root": str(cs.ROOT),
                      "logs": str(cs.ROOT / ".logs"), "web": str(settings.web_dist) if settings.web_dist else None},
            "env": {k: v for k, v in os.environ.items() if k.startswith("FORGE_")},
            "python": sys.version.split()[0], "platform": platform.platform(),
            "pipelines": pipelines(), "modality_errors": errors(), "active_jobs": active_jobs(),
        }

    @r.get("/api/comfy/instances")
    async def instances() -> list[dict[str, Any]]:
        return list(await asyncio.gather(*(asyncio.to_thread(cs.details, i) for i in cs.INSTANCES)))

    @r.get("/api/comfy/instances/{name}")
    async def instance(name: str) -> dict[str, Any]:
        return await asyncio.to_thread(cs.details, _inst(name))

    @r.post("/api/comfy/instances/{name}/start")
    async def start(name: str) -> dict[str, Any]:
        inst = _inst(name)
        if not inst.installed:
            raise HTTPException(400, f"{inst.title} não está instalada. {inst.install_hint}")
        cs.update_control(name, autostart=True, hold_until=time.time() + START_HOLD)
        if cs.port_open(inst.port, 0.5):
            return {"started": False, "detail": "já está no ar"}
        await asyncio.to_thread(cs.start, inst, True)
        return {"started": True}

    @r.post("/api/comfy/instances/{name}/stop")
    async def stop(name: str, body: StopBody | None = None) -> dict[str, Any]:
        inst = _inst(name)
        body = body or StopBody()
        d = await asyncio.to_thread(cs.details, inst)
        if not body.force and d.get("queue", {}).get("running"):
            raise HTTPException(409, f"{inst.name} está gerando agora ({d['queue']['running']} na fila); use force")
        cs.update_control(name, autostart=False, hold_until=0)   # o supervisor não sobe de novo
        ok = await asyncio.to_thread(cs.stop, inst)
        if not ok:
            raise HTTPException(500, f"não consegui encerrar {inst.name} (porta {inst.port} ainda aberta)")
        return {"stopped": True}

    @r.post("/api/comfy/instances/{name}/restart")
    async def restart(name: str, body: StopBody | None = None) -> dict[str, Any]:
        inst = _inst(name)
        body = body or StopBody()
        if not inst.installed:
            raise HTTPException(400, f"{inst.title} não está instalada. {inst.install_hint}")
        d = await asyncio.to_thread(cs.details, inst)
        if not body.force and d.get("queue", {}).get("running"):
            raise HTTPException(409, f"{inst.name} está gerando agora ({d['queue']['running']} na fila); use force")
        cs.update_control(name, autostart=True, hold_until=time.time() + START_HOLD)
        if not await asyncio.to_thread(cs.stop, inst):
            raise HTTPException(500, f"não consegui encerrar {inst.name} (porta {inst.port} ainda aberta)")
        await asyncio.to_thread(cs.start, inst, True)
        return {"restarted": True}

    @r.put("/api/comfy/instances/{name}/autostart")
    async def set_autostart(name: str, body: AutostartBody) -> dict[str, Any]:
        _inst(name)
        return cs.update_control(name, autostart=body.value)

    @r.post("/api/comfy/instances/{name}/free")
    async def free(name: str) -> dict[str, Any]:
        await _comfy_post(_inst(name), "/free", {"unload_models": True, "free_memory": True})
        return {"freed": True}

    @r.post("/api/comfy/instances/{name}/interrupt")
    async def interrupt(name: str) -> dict[str, Any]:
        inst = _inst(name)
        await _comfy_post(inst, "/interrupt")
        await _comfy_post(inst, "/queue", {"clear": True})
        return {"interrupted": True}

    @r.get("/api/comfy/instances/{name}/log")
    async def log(name: str, lines: int = 200) -> dict[str, Any]:
        inst = _inst(name)
        return {"path": str(cs._log_path(inst)), "text": await asyncio.to_thread(cs.log_tail, inst, max(1, min(lines, 5000)))}

    # ---- LLM local (Ollama): fora do ComfyUI ------------------------------
    from . import local_llm

    def _others() -> list[dict[str, Any]]:
        """Outros modelos fora do ComfyUI: o de visão (verificação +18 e análise das Souls) e o Claude (API)."""
        from . import critic, safety

        try:
            vision, vdetail = safety._vision_model(), ""
        except safety.VisionGuardError as e:
            vision, vdetail = None, str(e)
        ok, how = critic.api_available()
        return [
            {"id": "vision", "title": "Visão local (Ollama)", "backend": "Ollama", "model": vision, "available": bool(vision),
             "env": "FORGE_VISION_MODEL", "detail": vdetail or ("fixado por FORGE_VISION_MODEL" if os.environ.get("FORGE_VISION_MODEL")
                                                                  else "primeiro modelo do Ollama com visão"),
             "used_by": "verificação visual obrigatória dos projetos +18; descrição de estilo e legendas das Creator Souls"},
            {"id": "claude", "title": "Claude (API da Anthropic)", "backend": "API", "model": critic.MODEL, "available": ok,
             "env": "ANTHROPIC_API_KEY, FORGE_CRITIC_MODEL, FORGE_CRITIC_EFFORT", "detail": how,
             "used_by": "crítico da geração avançada, shaders e partículas escritos pela IA, planejador do 3d.animate"},
        ]

    @r.get("/api/llm")
    async def llm() -> dict[str, Any]:
        d = await asyncio.to_thread(local_llm.details)
        d["others"] = await asyncio.to_thread(_others)
        return d

    @r.put("/api/llm/model")
    async def llm_choose(body: ModelBody) -> dict[str, Any]:
        """Fixa o modelo usado pelo assistente de prompt (None = automático)."""
        from . import user_settings

        user_settings.update("local_llm", model=body.model or None)
        return await asyncio.to_thread(local_llm.details)

    @r.post("/api/llm/unload")
    async def llm_unload(body: ModelBody) -> dict[str, Any]:
        if not body.model:
            raise HTTPException(400, "informe o modelo")
        try:
            await asyncio.to_thread(local_llm.unload, body.model)
        except local_llm.LocalLLMError as e:
            raise HTTPException(502, str(e)) from None
        return {"unloaded": body.model}

    @r.delete("/api/llm/models/{name:path}")
    async def llm_delete(name: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(local_llm.delete, name)
        except local_llm.LocalLLMError as e:
            raise HTTPException(502, str(e)) from None
        return {"deleted": name}

    # ---- biblioteca de animações (3D): pastas com GLBs de clipes (Universal Animation Library...) -------------
    def _animlib():
        try:
            from forge_3d import animlib
        except ImportError:
            raise HTTPException(404, "modalidade 3D não instalada") from None
        return animlib

    def _library_state() -> dict[str, Any]:
        from . import user_settings

        lib = _animlib()
        files = lib.index()
        return {"folders": user_settings.get("3d", "animation_libraries") or [],
                "searched": [{"path": str(f), "exists": f.is_dir()} for f in lib.folders()],
                "files": [f.describe() for f in files],
                "sets": lib.SETS}

    @r.get("/api/3d/animation-library")
    async def animation_library() -> dict[str, Any]:
        return await asyncio.to_thread(_library_state)

    @r.put("/api/3d/animation-library")
    async def animation_library_folders(body: FoldersBody) -> dict[str, Any]:
        from pathlib import Path

        from . import user_settings

        folders = [f.strip().strip('"') for f in body.folders if f.strip()]
        missing = [f for f in folders if not Path(f).is_dir()]
        if missing:
            raise HTTPException(400, f"pasta não encontrada: {', '.join(missing)}")
        user_settings.update("3d", animation_libraries=folders or None)
        return await asyncio.to_thread(_library_state)

    return r

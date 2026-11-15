"""Rotas da tela de Instalação: catálogo com o estado de cada dependência, instalar/baixar em segundo plano,
acompanhar e cancelar tarefas, e configurar o token do Hugging Face."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import setup_catalog
from .setup_tasks import TaskManager


class TokenBody(BaseModel):
    token: str


def build_router(tm: TaskManager | None = None) -> APIRouter:
    r = APIRouter()
    tm = tm or TaskManager()

    @r.get("/api/setup")
    async def overview() -> dict[str, Any]:
        data = await asyncio.to_thread(setup_catalog.overview)
        active = {t["item"]: t for t in tm.list() if t["status"] in ("queued", "running")}
        for it in data["items"]:
            it["task"] = active.get(it["id"])
        return data

    @r.post("/api/setup/items/{item:path}/install")
    async def install(item: str) -> dict[str, Any]:
        try:
            task = await asyncio.to_thread(setup_catalog.install, tm, item)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from None
        return task.as_dict()

    @r.get("/api/setup/tasks")
    async def tasks() -> list[dict[str, Any]]:
        return tm.list()

    @r.delete("/api/setup/tasks")
    async def clear() -> dict[str, Any]:
        return {"cleared": tm.clear()}

    @r.get("/api/setup/tasks/{tid}")
    async def task(tid: str) -> dict[str, Any]:
        t = tm.get(tid)
        if not t:
            raise HTTPException(404, "tarefa não encontrada")
        return t.as_dict(with_log=True)

    @r.post("/api/setup/tasks/{tid}/cancel")
    async def cancel(tid: str) -> dict[str, Any]:
        return {"cancelled": tm.cancel(tid)}

    @r.post("/api/setup/hf-token")
    async def hf_token(body: TokenBody) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(setup_catalog.save_hf_token, body.token)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    return r

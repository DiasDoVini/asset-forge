"""Rotas do menu Workflows: o que o Asset Forge mandou para o ComfyUI, estado ao vivo, abrir no ComfyUI e cancelar."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from . import comfy_workflows as cw


def _rec(rid: str) -> dict[str, Any]:
    try:
        return cw.load(rid)
    except KeyError:
        raise HTTPException(404, "workflow não encontrado") from None


def build_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/workflows")
    async def listing(limit: int = 100) -> dict[str, Any]:
        items = await asyncio.to_thread(cw.listing, max(1, min(limit, 400)))
        node = await asyncio.to_thread(cw.custom_nodes_dirs)
        installed = {k: (v / cw.MODULE).exists() for k, v in node.items()}
        return {"items": items, "node_installed": installed}

    @r.get("/api/workflows/{rid}")
    async def detail(rid: str) -> dict[str, Any]:
        return _rec(rid)

    @r.get("/api/workflows/{rid}/api.json")
    async def api_json(rid: str) -> JSONResponse:
        rec = _rec(rid)
        return JSONResponse(rec["graph"], headers={"Content-Disposition": f'attachment; filename="{rid}_api.json"'})

    @r.get("/api/workflows/{rid}/workflow.json")
    async def ui_json(rid: str) -> JSONResponse:
        """Workflow no formato de UI (arraste para a janela do ComfyUI)."""
        rec = _rec(rid)
        try:
            async with httpx.AsyncClient(base_url=rec["url"], timeout=20) as c:
                info = (await c.get("/object_info")).json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "a instância do ComfyUI deste workflow está fora do ar (baixe o formato API)") from None
        ui = cw.to_ui(rec["graph"], info)
        return JSONResponse(ui, headers={"Content-Disposition": f'attachment; filename="{rid}.json"'})

    @r.post("/api/workflows/{rid}/open")
    async def open_in_comfy(rid: str) -> dict[str, Any]:
        _rec(rid)
        try:
            out = await asyncio.to_thread(cw.publish, rid)
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"a instância do ComfyUI deste workflow está fora do ar: {e}") from None
        out.pop("workflow", None)
        return out

    @r.post("/api/workflows/{rid}/cancel")
    async def cancel(rid: str) -> dict[str, Any]:
        """Tira da fila (se esperando) ou interrompe (se rodando) na instância."""
        rec = _rec(rid)
        pid = rec.get("prompt_id")
        if not pid:
            raise HTTPException(400, "este workflow não chegou a entrar na fila")
        try:
            async with httpx.AsyncClient(base_url=rec["url"], timeout=10) as c:
                q = (await c.get("/queue")).json()
                if any(e[1] == pid for e in q.get("queue_running", [])):
                    await c.post("/interrupt", json={"prompt_id": pid})
                    return {"interrupted": pid}
                await c.post("/queue", json={"delete": [pid]})
                return {"removed": pid}
        except httpx.HTTPError as e:
            raise HTTPException(502, f"instância fora do ar: {e}") from None

    @r.post("/api/workflows/install-node")
    async def install_node() -> dict[str, Any]:
        try:
            done = await asyncio.to_thread(cw.install_node)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"não consegui instalar o nó: {e}") from None
        return {"installed": done, "restart_needed": bool(done)}

    return r

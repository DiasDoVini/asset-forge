"""Rotas da IA: configuração (local x Claude, modelo, esforço, chave), AI Assistant dos jobs e chat do projeto."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import ai_providers as ai
from . import chat as chatmod
from .jobs import JobManager, JobRequest


class RoleBody(BaseModel):
    provider: str | None = None     # auto | local | claude
    model: str | None = None        # "" = padrão do provedor
    effort: str | None = None


class KeyBody(BaseModel):
    key: str


class DiagnoseBody(BaseModel):
    provider: str | None = None
    model: str | None = None


class NewChatBody(BaseModel):
    provider: str | None = None
    model: str | None = None


class SendBody(BaseModel):
    text: str
    provider: str | None = None
    model: str | None = None
    effort: str | None = None


def build_router(projects, jobs: JobManager) -> APIRouter:
    r = APIRouter()
    state: dict[str, Any] = {"loop": None}

    def notify(ev: dict[str, Any]) -> None:
        loop = state["loop"]
        if loop:
            loop.call_soon_threadsafe(jobs.emit, ev)

    def submit_factory(cancel: threading.Event):
        """Para as ferramentas do chat: põe o job na fila normal (aparece em Jobs/Workflows) e espera terminar."""
        def run(req: dict[str, Any]) -> dict[str, Any]:
            loop = state["loop"]

            async def _submit() -> str:
                return jobs.submit(JobRequest.model_validate(req), source="chat").id

            jid = asyncio.run_coroutine_threadsafe(_submit(), loop).result(30)
            while True:
                j = jobs.jobs.get(jid)
                if j is None:
                    return jobs.detail(jid) or {"id": jid, "status": "error", "error": "job sumiu"}
                if j.status in ("done", "error", "cancelled"):
                    return j.public()
                if cancel.is_set():
                    loop.call_soon_threadsafe(jobs.cancel, jid)
                time.sleep(1)
        return run

    runner = chatmod.ChatRunner(submit_factory, notify)

    def proj(slug: str):
        try:
            return projects.resolve(slug)
        except KeyError:
            raise HTTPException(404, f"projeto não encontrado: {slug}") from None

    # ---- configuração ------------------------------------------------------
    @r.get("/api/ai")
    async def describe() -> dict[str, Any]:
        return await asyncio.to_thread(ai.describe)

    @r.put("/api/ai/{role}")
    async def set_role(role: str, body: RoleBody) -> dict[str, Any]:
        if role not in ("assistant", "chat"):
            raise HTTPException(404)
        if body.provider not in (None, "auto", "local", "claude"):
            raise HTTPException(400, "provider: auto, local ou claude")
        ai.save_settings(role, provider=body.provider, model=body.model, effort=body.effort)
        return await asyncio.to_thread(ai.describe)

    @r.put("/api/ai-claude-key")
    async def set_key(body: KeyBody) -> dict[str, Any]:
        import anthropic

        key = body.key.strip()

        def check() -> None:
            anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.list(limit=1)

        try:
            await asyncio.to_thread(check)
        except anthropic.AuthenticationError:
            raise HTTPException(400, "chave recusada pela Anthropic") from None
        except anthropic.APIConnectionError:
            raise HTTPException(502, "sem conexão com a API da Anthropic") from None
        from . import user_settings

        user_settings.update("ai", claude_api_key=key)
        return await asyncio.to_thread(ai.describe)

    @r.delete("/api/ai-claude-key")
    async def del_key() -> dict[str, Any]:
        from . import user_settings

        user_settings.update("ai", claude_api_key=None)
        return await asyncio.to_thread(ai.describe)

    # ---- AI Assistant dos jobs -------------------------------------------------
    @r.post("/api/jobs/{job_id}/diagnose")
    async def diagnose(job_id: str, body: DiagnoseBody | None = None) -> dict[str, Any]:
        from .ai_assistant import diagnose as run

        job = jobs.detail(job_id)
        if job is None:
            raise HTTPException(404, "job não encontrado")
        body = body or DiagnoseBody()
        try:
            return await asyncio.to_thread(run, job, provider=body.provider, model=body.model)
        except ai.AIError as e:
            raise HTTPException(502, str(e)) from None

    # ---- chat ---------------------------------------------------------------
    @r.get("/api/projects/{slug}/chats")
    async def chats(slug: str) -> list[dict[str, Any]]:
        p = proj(slug)
        return [c | runner.status(c["id"]) for c in chatmod.list_chats(p)]

    @r.post("/api/projects/{slug}/chats")
    async def new_chat(slug: str, body: NewChatBody | None = None) -> dict[str, Any]:
        body = body or NewChatBody()
        return chatmod.create(proj(slug), body.provider, body.model)

    @r.get("/api/projects/{slug}/chats/{cid}")
    async def get_chat(slug: str, cid: str) -> dict[str, Any]:
        try:
            c = chatmod.load(proj(slug), cid)
        except KeyError:
            raise HTTPException(404, "conversa não encontrada") from None
        for m in c["messages"]:
            m.pop("raw", None)          # conteúdo do provedor: só para o modelo
            m.pop("images", None) if m.get("role") == "tool" else None
        return c | runner.status(cid)

    @r.delete("/api/projects/{slug}/chats/{cid}")
    async def del_chat(slug: str, cid: str) -> dict[str, Any]:
        if runner.status(cid)["running"]:
            raise HTTPException(409, "a conversa está respondendo; cancele antes")
        chatmod.delete(proj(slug), cid)
        return {"deleted": cid}

    @r.post("/api/projects/{slug}/chats/{cid}/messages", status_code=202)
    async def send(slug: str, cid: str, body: SendBody) -> dict[str, Any]:
        state["loop"] = asyncio.get_running_loop()
        p = proj(slug)
        if not body.text.strip():
            raise HTTPException(400, "mensagem vazia")
        try:
            runner.send(p, cid, body.text.strip(), provider=body.provider, model=body.model, effort=body.effort)
        except KeyError:
            raise HTTPException(404, "conversa não encontrada") from None
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    @r.post("/api/projects/{slug}/chats/{cid}/cancel")
    async def cancel(slug: str, cid: str) -> dict[str, Any]:
        return {"cancelled": runner.cancel(cid)}

    return r

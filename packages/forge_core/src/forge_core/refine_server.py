"""Rotas e executor da geração avançada no servidor (ações rodam como jobs da fila)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .jobs import JobManager, JobRequest
from .project import ProjectRegistry
from .refine import (Action, Brief, RefineEngine, RefineSession, Review, get_refiner, list_sessions, load_session,
                     save_session, session_view)

log = logging.getLogger("forge")


class StartBody(BaseModel):
    refiner: str
    brief: Brief
    critic: Literal["api", "manual"] = "manual"
    title: str = ""
    asset: str | None = None
    folder: str | None = None
    category: str | None = None


class ReviewBody(BaseModel):
    score: float = Field(..., ge=0, le=10)
    verdict: Literal["accept", "revise", "restart"]
    summary: str = ""
    issues: list[str] = Field(default_factory=list)
    next_action: Action | None = None
    reviewer: str = "human"


class ResumeBody(BaseModel):
    critic: Literal["api", "manual"] | None = None


class RefineManager:
    def __init__(self, projects: ProjectRegistry, jobs: JobManager):
        self.projects = projects
        self.jobs = jobs
        self.tasks: dict[str, asyncio.Task] = {}

    def engine(self, project_slug: str, loop: asyncio.AbstractEventLoop) -> RefineEngine:
        project = self.projects.resolve(project_slug)

        def run_action(s: RefineSession, action: Action, params: dict[str, Any]):
            async def go():
                job = self.jobs.submit(JobRequest(project=project.slug, pipeline=action.pipeline, params=params,
                                                  asset=s.asset, tags=["refine", s.id]))
                task = self.jobs._tasks.get(job.id)
                if task:
                    await task
                return self.jobs.jobs.get(job.id, job)

            try:
                job = asyncio.run_coroutine_threadsafe(go(), loop).result()
            except Exception as e:  # noqa: BLE001
                return None, None, f"{type(e).__name__}: {e}"
            if job.status == "done":
                return job.result, job.id, None
            return None, job.id, job.error or f"job {job.status}"

        return RefineEngine(project, run_action=run_action)

    def emit(self, project: str, s: RefineSession) -> None:
        self.jobs.emit({"type": "refine", "project": project, "id": s.id, "status": s.status, "message": s.message})

    def drive(self, project: str, sid: str) -> None:
        """Roda a sessão em segundo plano até precisar de revisão (manual) ou terminar."""
        if sid in self.tasks and not self.tasks[sid].done():
            return
        loop = asyncio.get_running_loop()
        eng = self.engine(project, loop)

        def progress(msg: str) -> None:
            loop.call_soon_threadsafe(self.jobs.emit, {"type": "refine", "project": project, "id": sid,
                                                      "status": "running", "message": msg})

        async def run() -> None:
            try:
                s = await asyncio.to_thread(eng.run_until_pause, sid, progress)
            except Exception as e:  # noqa: BLE001
                log.exception("refine %s falhou", sid)
                p = self.projects.resolve(project)
                s = load_session(p, sid)
                s.status, s.error = "failed", f"{type(e).__name__}: {e}"
                save_session(p, s)
            self.emit(project, s)

        self.tasks[sid] = asyncio.create_task(run())

    def resume_interrupted(self) -> None:
        for p in self.projects.list():
            for s in list_sessions(p):
                if s.driver == "server" and s.status in ("ready", "running"):
                    if s.status == "running" and s.iterations and s.iterations[-1].finished is None:
                        it = s.iterations[-1]
                        it.error = it.error or "interrompido (servidor reiniciado)"
                        s.pending_action = s.pending_action or it.action
                    s.status = "ready"
                    save_session(p, s)
                    self.drive(p.slug, s.id)


def build_router(projects: ProjectRegistry, mgr: RefineManager) -> APIRouter:
    r = APIRouter()

    def proj(slug: str):
        try:
            return projects.resolve(slug)
        except FileNotFoundError:
            raise HTTPException(404, f"projeto não encontrado: {slug}")

    def view(p, s: RefineSession) -> dict[str, Any]:
        return session_view(p, s, url_prefix=f"/files/{p.slug}")

    @r.get("/api/refiners")
    async def refiners() -> dict[str, Any]:
        from .critic import api_available
        from .pipeline import get_registry

        ok, why = api_available()
        return {"refiners": [x.describe() for x in get_registry().refiners.values()],
                "api_critic": {"available": ok, "detail": why}}

    @r.get("/api/projects/{slug}/refine")
    async def sessions(slug: str) -> list[dict[str, Any]]:
        p = proj(slug)
        out = []
        for s in list_sessions(p):
            v = view(p, s)
            v["iteration_count"] = len(s.iterations)
            last = next((it for it in reversed(v["iterations"]) if it["previews"]), None)
            v["thumb"] = last["previews"][0]["url"] if last else None
            v["last_score"] = next((it["review"]["score"] for it in reversed(v["iterations"]) if it["review"]), None)
            v.pop("iterations")
            out.append(v)
        return out

    @r.post("/api/projects/{slug}/refine", status_code=201)
    async def start(slug: str, body: StartBody) -> dict[str, Any]:
        p = proj(slug)
        try:
            get_refiner(body.refiner)
        except KeyError as e:
            raise HTTPException(400, str(e).strip("'\""))
        eng = RefineEngine(p)
        s = eng.create(body.refiner, body.brief, critic=body.critic, title=body.title, asset=body.asset,
                       driver="server", folder=body.folder, category=body.category)
        mgr.drive(p.slug, s.id)
        return view(p, s)

    @r.get("/api/projects/{slug}/refine/{sid}")
    async def get(slug: str, sid: str) -> dict[str, Any]:
        p = proj(slug)
        try:
            return view(p, load_session(p, sid))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @r.post("/api/projects/{slug}/refine/{sid}/review")
    async def review(slug: str, sid: str, body: ReviewBody) -> dict[str, Any]:
        p = proj(slug)
        try:
            s = RefineEngine(p).review(sid, Review(**body.model_dump()))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:  # noqa: BLE001 - ação inválida
            raise HTTPException(400, str(e))
        if s.status == "ready":
            mgr.drive(p.slug, s.id)
        mgr.emit(p.slug, s)
        return view(p, s)

    @r.post("/api/projects/{slug}/refine/{sid}/resume")
    async def resume(slug: str, sid: str, body: ResumeBody | None = None) -> dict[str, Any]:
        """Continua uma sessão (ex.: trocar para crítico automático e pedir avaliação da última iteração)."""
        p = proj(slug)
        s = load_session(p, sid)
        if body and body.critic:
            s.critic = body.critic
        if s.status in ("failed", "cancelled"):
            s.status = "ready" if s.pending_action else "awaiting_review"
        save_session(p, s)
        if s.status == "awaiting_review" and s.critic == "api" and s.iterations:
            loop = asyncio.get_running_loop()
            eng = mgr.engine(p.slug, loop)

            async def crit() -> None:
                from .refine import get_refiner as gr

                try:
                    rv = await asyncio.to_thread(eng.critic, load_session(p, sid), gr(s.refiner))
                    s2 = await asyncio.to_thread(eng.review, sid, rv)
                    if s2.status == "ready":
                        mgr.drive(p.slug, sid)
                    mgr.emit(p.slug, s2)
                except Exception as e:  # noqa: BLE001
                    s3 = load_session(p, sid)
                    s3.message = f"crítico automático falhou: {type(e).__name__}: {e}"
                    save_session(p, s3)
                    mgr.emit(p.slug, s3)

            mgr.tasks[sid] = asyncio.create_task(crit())
        elif s.status == "ready":
            mgr.drive(p.slug, sid)
        return view(p, load_session(p, sid))

    @r.post("/api/projects/{slug}/refine/{sid}/cancel")
    async def cancel(slug: str, sid: str) -> dict[str, Any]:
        p = proj(slug)
        s = RefineEngine(p).cancel(sid)
        mgr.emit(p.slug, s)
        return view(p, s)

    return r

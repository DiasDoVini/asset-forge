"""Fila de jobs assíncrona do servidor: 1 slot de GPU compartilhado, jobs só-CPU em paralelo.

Eventos (dicts) são enviados a todos os assinantes (websocket da UI):
    {"type": "job", "job": {...}}         mudança de estado/progresso
    {"type": "log", "job_id": ..., "line": ...}
    {"type": "asset", "project": ..., "ref": ...}
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from .pipeline import Cancelled, get_registry
from .jobstore import JobStore
from .project import ProjectRegistry
from .runner import run_pipeline, validate_params
from .worker import WorkerRequest, run_isolated

Status = Literal["queued", "running", "done", "error", "cancelled"]


class JobRequest(BaseModel):
    project: str
    pipeline: str
    params: dict[str, Any] = Field(default_factory=dict)
    asset: str | None = None
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    folder: str | None = None
    category: str | None = None


class Job(BaseModel):
    id: str
    request: JobRequest
    status: Status = "queued"
    progress: float = 0.0
    message: str | None = None
    logs: list[str] = Field(default_factory=list)
    result: str | None = None  # ref da versão criada
    error: str | None = None
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started: datetime | None = None
    finished: datetime | None = None
    source: str = "server"
    retry_of: str | None = None

    def public(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude={"logs"})
        d["log_tail"] = self.logs[-20:]
        return d


class JobManager:
    def __init__(self, projects: ProjectRegistry, comfy_url: str | None = None, max_history: int = 200,
                 isolate: bool = True, store: "JobStore | None" = None):
        # isolate: cada job roda num subprocesso (crash nativo não derruba o servidor)
        self.isolate = isolate
        # store: histórico persistente (sobrevive a reinícios); jobs órfãos viram erro
        self.store = store
        if store is not None:
            store.mark_interrupted()
        self.projects = projects
        self.comfy_url = comfy_url
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._gpu = asyncio.Semaphore(1)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.max_history = max_history

    # ---- eventos -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def emit(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _emit_threadsafe(self, event: dict[str, Any]) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self.emit, event)

    def _job_event(self, job: Job) -> dict[str, Any]:
        return {"type": "job", "job": job.public()}

    def _persist(self, job: Job) -> None:
        if self.store is not None:
            try:
                self.store.save(job.model_dump(mode="json"))
            except Exception:  # histórico nunca derruba a fila
                pass

    # ---- API -----------------------------------------------------------
    def submit(self, req: JobRequest, retry_of: str | None = None, source: str = "server") -> Job:
        from .content import check_params

        p = validate_params(get_registry().get(req.pipeline), req.params)  # valida id e params cedo
        check_params(self.projects.resolve(req.project), p.model_dump())  # política de conteúdo do projeto
        self._loop = asyncio.get_running_loop()
        job = Job(id=uuid.uuid4().hex[:12], request=req, retry_of=retry_of, source=source)
        self._persist(job)
        self.jobs[job.id] = job
        self._cancel[job.id] = threading.Event()
        self._tasks[job.id] = asyncio.create_task(self._run(job))
        self._trim()
        self.emit(self._job_event(job))
        return job

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def history(self, project: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Histórico persistente com o estado ao vivo dos jobs em memória por cima."""
        live = {j.id: j for j in self.jobs.values()}
        if self.store is None:
            rows = [j.public() for j in self.list()]
        else:
            rows = []
            for r in self.store.list(project=project, status=status, limit=limit):
                if r["id"] in live:
                    rows.append(live[r["id"]].public())
                else:
                    r["log_tail"] = r.pop("logs")[-20:]
                    rows.append(r)
        if project:
            rows = [r for r in rows if r["request"]["project"] == project]
        if status:
            rows = [r for r in rows if r["status"] in status.split(",")]
        return rows[:limit]

    def detail(self, job_id: str) -> dict[str, Any] | None:
        if job_id in self.jobs:
            j = self.jobs[job_id]
            return {**j.public(), "logs": j.logs}
        return self.store.get(job_id) if self.store else None

    def retry(self, job_id: str, params: dict[str, Any] | None = None, new_seed: bool = False) -> Job:
        """Reenvia um job (mesmos parâmetros; params sobrescreve campos; new_seed remove a seed)."""
        old = self.detail(job_id)
        if old is None:
            raise KeyError(f"job não encontrado: {job_id}")
        req = JobRequest.model_validate(old["request"])
        merged = {**req.params, **(params or {})}
        if new_seed:
            merged.pop("seed", None)
        return self.submit(req.model_copy(update={"params": merged}), retry_of=job_id)

    def forget(self, job_id: str) -> None:
        if job_id in self.jobs and self.jobs[job_id].status in ("queued", "running"):
            raise ValueError("job em andamento; cancele antes")
        self.jobs.pop(job_id, None)
        if self.store:
            self.store.delete(job_id)

    def cancel(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        if job.status in ("queued", "running"):
            self._cancel[job_id].set()
            if job.status == "queued":
                self._tasks[job_id].cancel()
        return job

    # ---- execução ------------------------------------------------------
    async def _run(self, job: Job) -> None:
        pipeline = get_registry().get(job.request.pipeline)
        needs_gpu = "gpu" in pipeline.resources
        try:
            if needs_gpu:
                async with self._gpu:
                    await self._execute(job)
            else:
                await self._execute(job)
        except asyncio.CancelledError:
            job.status, job.finished = "cancelled", datetime.now(timezone.utc)
            self._persist(job)
            self.emit(self._job_event(job))

    async def _execute(self, job: Job) -> None:
        cancel = self._cancel[job.id]
        if cancel.is_set():
            raise asyncio.CancelledError
        job.status, job.started = "running", datetime.now(timezone.utc)
        self._persist(job)
        self.emit(self._job_event(job))

        def on_log(line: str) -> None:
            job.logs.append(line)
            self._emit_threadsafe({"type": "log", "job_id": job.id, "line": line})

        def on_progress(value: float, msg: str | None) -> None:
            job.progress, job.message = value, msg
            self._emit_threadsafe(self._job_event(job))

        req = job.request
        try:
            project = self.projects.resolve(req.project)
            if self.isolate:
                res = await asyncio.to_thread(
                    run_isolated,
                    WorkerRequest(str(project.root), req.pipeline, req.params, req.asset, req.name, req.tags, self.comfy_url,
                                  req.folder, req.category),
                    on_log=on_log, on_progress=on_progress, cancel=cancel,
                )
                if res.ref is None:
                    if res.reserved:  # worker morreu no meio: recolhe a versão incompleta
                        project.store.sweep_incomplete(only=res.reserved)
                    if res.cancelled:
                        raise Cancelled()
                    raise RuntimeError(res.error or "falha desconhecida no worker")
                ref = res.ref
            else:
                av = await asyncio.to_thread(
                    run_pipeline, project, req.pipeline, req.params, asset=req.asset, name=req.name,
                    tags=req.tags, comfy_url=self.comfy_url, on_log=on_log, on_progress=on_progress, cancel=cancel,
                    folder=req.folder, category=req.category,
                )
                ref = av.ref
            job.status, job.result, job.progress = "done", ref, 1.0
            self.emit({"type": "asset", "project": project.slug, "ref": ref})
        except Cancelled:
            job.status = "cancelled"
        except Exception as e:
            job.status = "cancelled" if cancel.is_set() else "error"
            job.error = f"{type(e).__name__}: {e}"
        job.finished = datetime.now(timezone.utc)
        self._persist(job)
        self.emit(self._job_event(job))

    def _trim(self) -> None:
        done = [j for j in self.list() if j.status in ("done", "error", "cancelled")]
        for j in done[self.max_history:]:
            self.jobs.pop(j.id, None)
            self._tasks.pop(j.id, None)
            self._cancel.pop(j.id, None)

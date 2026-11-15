"""API HTTP/WebSocket do Asset Forge (porta padrão 8765) + UI estática (web/dist)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid as _uuid
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .comfy import ComfyClient
from .config import Settings, get_settings
from .jobs import JobManager, JobRequest
from .jobstore import JobStore
from .pipeline import get_registry
from .project import Category, Project, ProjectRegistry, Style, normalize_folder
from .content import ContentPolicyError
from .runner import PipelineError


class ProjectCreate(BaseModel):
    slug: str
    name: str | None = None
    description: str = ""
    style: Style | None = None
    modalities: dict[str, dict[str, Any]] = {}


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    style: Style | None = None
    modalities: dict[str, dict[str, Any]] | None = None  # substitui blocos inteiros
    categories: list[Category] | None = None


class AssistBody(BaseModel):
    prompt: str
    target: str = "image"


class NsfwBody(BaseModel):
    enabled: bool
    confirm_adult: bool = False


class OrganizeBody(BaseModel):
    slugs: list[str]
    folder: str | None = None          # None = não mexe; "" = raiz
    category: str | None = None        # None = não mexe
    clear_category: bool = False


class FolderBody(BaseModel):
    path: str


class FolderRename(BaseModel):
    old: str
    new: str


class ReloadBody(BaseModel):
    force: bool = False
    timeout: float = 900


class RetryBody(BaseModel):
    params: dict[str, Any] | None = None
    new_seed: bool = False


class AssetPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


BOOT_ID = _uuid.uuid4().hex[:8]          # muda a cada processo (o `forge reload` usa para saber que voltou)
STARTED = time.strftime("%Y-%m-%dT%H:%M:%S")


def _safe_join(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise HTTPException(400, "caminho inválido")
    return p


def create_app(settings: Settings | None = None, isolate_jobs: bool = True, persist_jobs: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    projects = ProjectRegistry(settings.projects_dir)
    store = JobStore(settings.projects_dir / ".forge" / "jobs.db") if persist_jobs else None
    jobs = JobManager(projects, comfy_url=settings.comfy_url, isolate=isolate_jobs, store=store)
    from .setup_tasks import TaskManager

    setup_tasks = TaskManager()   # downloads/instalações da tela de Instalação
    # versões órfãs (job que morreu no meio) de mais de 1h vão para .forge/trash/incomplete
    for proj in projects.list():
        for moved in proj.store.sweep_incomplete(min_age=3600):
            logging.getLogger("forge").warning("versão incompleta recolhida: %s/%s", proj.slug, moved)
    app = FastAPI(title="Asset Forge", version="0.1.0")
    app.state.jobs = jobs
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    def project_or_404(slug: str) -> Project:
        try:
            return projects.resolve(slug)
        except FileNotFoundError:
            raise HTTPException(404, f"projeto não encontrado: {slug}")

    def asset_dict(project: Project, av) -> dict[str, Any]:
        d = av.model_dump(mode="json")
        d["ref"] = av.ref
        meta = project.store.get_meta(av.slug)
        d["folder"], d["category"] = meta.folder, meta.category
        base = f"/files/{project.slug}/assets/{av.slug}/v{av.version:03d}"
        for f in d["files"]:
            f["url"] = f"{base}/{f['path']}"
        return d

    # ---- sistema -------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        c = ComfyClient(settings.comfy_url)
        ok = await asyncio.to_thread(c.ping)
        c.close()
        return {"ok": True, "comfy": {"url": settings.comfy_url, "online": ok},
                "projects_dir": str(settings.projects_dir), "modality_errors": get_registry().errors,
                "boot_id": BOOT_ID, "started": STARTED, "supervised": os.environ.get("FORGE_SUPERVISED") == "1",
                "pipelines": len(get_registry().pipelines)}

    @app.post("/api/admin/reload")
    async def admin_reload(body: ReloadBody | None = None) -> dict[str, Any]:
        """Reinicia o servidor com o código atual (só sob `forge serve` supervisionado).
        Espera jobs e sessões de refinamento ativos terminarem, salvo force=true."""
        body = body or ReloadBody()
        if os.environ.get("FORGE_SUPERVISED") != "1":
            raise HTTPException(400, "servidor sem supervisor (rodando com --no-supervise): reinicie manualmente")

        def busy() -> list[str]:
            act = [f"job {j.id} ({j.request.pipeline})" for j in jobs.list() if j.status in ("queued", "running")]
            refine = getattr(app.state, "refine", None)
            if refine:
                act += [f"refinamento {sid}" for sid, t in refine.tasks.items() if not t.done()]
            act += [f"download {t['title']}" for t in setup_tasks.list() if t["status"] in ("queued", "running")]
            return act

        deadline = asyncio.get_running_loop().time() + body.timeout
        while not body.force and busy():
            if asyncio.get_running_loop().time() > deadline:
                raise HTTPException(409, {"busy": busy(), "detail": "ainda há trabalho em andamento; use force"})
            await asyncio.sleep(max(0.05, min(2.0, deadline - asyncio.get_running_loop().time())))
        jobs.emit({"type": "server_reloading"})
        asyncio.get_running_loop().call_later(0.5, os._exit, 75)
        return {"reloading": True, "boot_id": BOOT_ID, "interrupted": busy() if body.force else []}

    @app.get("/api/modalities")
    async def modalities() -> list[dict[str, Any]]:
        return get_registry().describe()

    @app.get("/api/comfy/models/{folder}")
    async def comfy_models(folder: str) -> list[str]:
        c = ComfyClient(settings.comfy_url)
        try:
            return await asyncio.to_thread(c.models, folder)
        finally:
            c.close()

    # ---- projetos ------------------------------------------------------
    @app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        return [p.summary() for p in projects.list()]

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectCreate) -> dict[str, Any]:
        fields: dict[str, Any] = {"description": body.description, **body.modalities}
        if body.style:
            fields["style"] = body.style
        try:
            p = projects.create(body.slug, name=body.name, **fields)
        except FileExistsError as e:
            raise HTTPException(409, str(e))
        return p.summary()

    @app.get("/api/projects/{slug}")
    async def get_project(slug: str) -> dict[str, Any]:
        return project_or_404(slug).summary()

    @app.patch("/api/projects/{slug}")
    async def patch_project(slug: str, body: ProjectPatch) -> dict[str, Any]:
        p = project_or_404(slug)
        if body.name is not None:
            p.config.name = body.name
        if body.description is not None:
            p.config.description = body.description
        if body.style is not None:
            p.config.style = body.style
        for mod, block in (body.modalities or {}).items():
            p.config.set_modality(mod, block)
        if body.categories is not None:
            p.config.categories = body.categories
        p.save()
        return p.summary()

    @app.get("/api/local-llm")
    async def local_llm_status() -> dict[str, Any]:
        from . import local_llm

        return await asyncio.to_thread(local_llm.status)

    @app.post("/api/projects/{slug}/prompt-assist")
    async def prompt_assist(slug: str, body: AssistBody) -> dict[str, Any]:
        """Reescreve o prompt com o LLM local (Ollama). Respeita a política de conteúdo do projeto."""
        from . import local_llm
        from .content import is_nsfw

        p = project_or_404(slug)
        try:
            text = await asyncio.to_thread(local_llm.assist, body.prompt, target=body.target, nsfw=is_nsfw(p),
                                           style=p.config.style.prompt)
        except ContentPolicyError as e:
            raise HTTPException(400, str(e)) from None
        except local_llm.LocalLLMError as e:
            raise HTTPException(503, str(e)) from None
        return {"prompt": text}

    @app.post("/api/projects/{slug}/nsfw")
    async def set_nsfw(slug: str, body: NsfwBody) -> dict[str, Any]:
        p = project_or_404(slug)
        try:
            p.set_nsfw(body.enabled, confirm_adult=body.confirm_adult)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        return p.summary()

    @app.post("/api/projects/{slug}/refs", status_code=201)
    async def upload_ref(slug: str, file: UploadFile = File(...)) -> dict[str, Any]:
        p = project_or_404(slug)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / Path(file.filename or "ref.png").name
            with tmp.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            name = p.add_ref(tmp)
        return {"name": name, "url": f"/files/{p.slug}/refs/{name}"}

    @app.delete("/api/projects/{slug}/refs/{name}")
    async def delete_ref(slug: str, name: str) -> dict[str, str]:
        project_or_404(slug).remove_ref(name)
        return {"deleted": name}

    # ---- assets --------------------------------------------------------
    @app.get("/api/projects/{slug}/assets")
    async def list_assets(slug: str, q: str = "", modality: str | None = None, kind: str | None = None,
                          tag: str | None = None, base: str | None = None, folder: str | None = None,
                          recursive: bool = False, category: str | None = None) -> list[dict[str, Any]]:
        """folder: omitido = todas; "" = raiz; "A/B" = pasta (recursive inclui subpastas). category=__none__ = sem categoria."""
        p = project_or_404(slug)
        rows = p.store.search(q=q, modality=modality, kind=kind, tag=tag, base=base,
                              folder=normalize_folder(folder) if folder is not None else None,
                              recursive=recursive, category=category)
        for r in rows:
            base = f"/files/{p.slug}/assets/{r['slug']}/v{r['version']:03d}"
            r["primary_url"] = f"{base}/{r['primary_file']}" if r.get("primary_file") else None
            r["preview_url"] = f"{base}/{r['preview_file']}" if r.get("preview_file") else None
        return rows

    @app.post("/api/projects/{slug}/reindex")
    async def reindex(slug: str) -> dict[str, int]:
        return {"indexed": project_or_404(slug).store.reindex()}

    @app.get("/api/projects/{slug}/assets/{asset}")
    async def asset_versions(slug: str, asset: str) -> list[dict[str, Any]]:
        p = project_or_404(slug)
        vs = p.store.versions(asset)
        if not vs:
            raise HTTPException(404, f"asset não encontrado: {asset}")
        return [asset_dict(p, v) for v in vs]

    @app.get("/api/projects/{slug}/assets/{asset}/{version}")
    async def asset_version(slug: str, asset: str, version: int) -> dict[str, Any]:
        p = project_or_404(slug)
        try:
            return asset_dict(p, p.store.get(f"{asset}@v{version}"))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/projects/{slug}/assets/{asset}/{version}/exporters")
    async def asset_exporters(slug: str, asset: str, version: int) -> list[dict[str, Any]]:
        p = project_or_404(slug)
        try:
            av = p.store.get(f"{asset}@v{version}")
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        return [e.describe() for e in get_registry().exporters_for(av)]

    @app.post("/api/projects/{slug}/assets/{asset}/{version}/export/{exporter_id}")
    async def export_asset(slug: str, asset: str, version: int, exporter_id: str,
                           params: dict[str, Any] | None = None) -> FileResponse:
        """Roda um exportador e devolve o arquivo (ou um .zip se houver vários)."""
        from .export import run_export, zip_files

        p = project_or_404(slug)
        try:
            files = await asyncio.to_thread(run_export, p, f"{asset}@v{version}", exporter_id, params or {})
        except (KeyError, FileNotFoundError, PipelineError) as e:
            raise HTTPException(400, str(e).strip("'\""))
        if len(files) == 1:
            return FileResponse(files[0], filename=files[0].name)
        z = zip_files(files, Path(tempfile.mkdtemp(prefix="forge-zip-")) / f"{asset}_v{version}_{exporter_id.split('.')[-1]}.zip")
        return FileResponse(z, filename=z.name, media_type="application/zip")

    @app.patch("/api/projects/{slug}/assets/{asset}/{version}")
    async def patch_asset(slug: str, asset: str, version: int, body: AssetPatch) -> dict[str, Any]:
        p = project_or_404(slug)
        av = p.store.update_meta(f"{asset}@v{version}", **body.model_dump(exclude_none=True))
        return asset_dict(p, av)

    @app.delete("/api/projects/{slug}/assets/{asset}")
    async def delete_asset(slug: str, asset: str, version: int | None = None) -> dict[str, str]:
        p = project_or_404(slug)
        ref = f"{asset}@v{version}" if version else asset
        p.store.delete(ref)
        jobs.emit({"type": "asset_deleted", "project": p.slug, "ref": ref})
        return {"deleted": ref}

    @app.post("/api/projects/{slug}/assets", status_code=201)
    async def upload_asset(slug: str, file: UploadFile = File(...), asset: str | None = Form(None),
                           parent: str | None = Form(None), modality: str | None = Form(None),
                           kind: str | None = Form(None), name: str = Form(""), tags: str = Form(""),
                           folder: str | None = Form(None), category: str | None = Form(None),
                           options: str = Form("")) -> dict[str, Any]:
        """Importa arquivo (upload ou resultado de edição manual na UI). Com `parent`, vira nova versão dele.
        Sem modality/kind/parent, o importer do formato converte o arquivo (`options`: JSON com as opções dele)."""
        p = project_or_404(slug)
        try:
            opts = json.loads(options) if options.strip() else {}
        except ValueError:
            raise HTTPException(400, "options não é JSON válido") from None
        if parent and not asset:
            asset = parent.split("@")[0]
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / Path(file.filename or "upload.png").name
            with tmp.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            try:
                av = p.store.import_file(tmp, slug=asset, modality=modality, kind=kind, parent=parent, name=name,
                                         tags=[t.strip() for t in tags.split(",") if t.strip()],
                                         folder=folder, category=category, options=opts)
            except (ValueError, FileNotFoundError) as e:   # formato inválido / opções erradas (pydantic também)
                raise HTTPException(400, str(e)) from None
        jobs.emit({"type": "asset", "project": p.slug, "ref": av.ref})
        return asset_dict(p, av)

    @app.get("/api/import/formats")
    async def import_formats() -> list[dict[str, Any]]:
        """Importers registrados (extensões, modalidade, opções) — a UI monta o diálogo de importação com isto."""
        return [i.describe() for i in get_registry().importers.values()]

    # ---- organização: pastas e categorias --------------------------
    @app.get("/api/projects/{slug}/folders")
    async def list_folders(slug: str) -> dict[str, Any]:
        """Árvore achatada: cada pasta com nº de assets diretos e total (com subpastas)."""
        p = project_or_404(slug)
        counts = p.store.folder_counts()
        paths = {normalize_folder(f) for f in p.config.folders} | {f for f in counts if f}
        for path in list(paths):  # ancestrais existem implicitamente
            parts = path.split("/")
            paths.update("/".join(parts[:i]) for i in range(1, len(parts)))
        out = []
        for path in sorted(paths, key=str.lower):
            total = sum(n for f, n in counts.items() if f == path or f.startswith(path + "/"))
            out.append({"path": path, "name": path.rsplit("/", 1)[-1], "depth": path.count("/"),
                        "count": counts.get(path, 0), "total": total})
        return {"folders": out, "root_count": counts.get("", 0), "total": sum(counts.values())}

    @app.post("/api/projects/{slug}/folders", status_code=201)
    async def create_folder(slug: str, body: FolderBody) -> dict[str, str]:
        p = project_or_404(slug)
        path = normalize_folder(body.path)
        if not path:
            raise HTTPException(400, "nome de pasta vazio")
        if path not in p.config.folders:
            p.config.folders.append(path)
            p.save()
        return {"path": path}

    @app.post("/api/projects/{slug}/folders/rename")
    async def rename_folder(slug: str, body: FolderRename) -> dict[str, Any]:
        p = project_or_404(slug)
        old, new = normalize_folder(body.old), normalize_folder(body.new)
        try:
            moved = p.store.move_folder(old, new)
        except ValueError as e:
            raise HTTPException(400, str(e))
        p.config.folders = sorted({(new + f[len(old):]) if (f == old or f.startswith(old + "/")) else f
                                   for f in p.config.folders} | {new})
        p.save()
        jobs.emit({"type": "asset", "project": p.slug, "ref": ""})
        return {"moved": moved, "path": new}

    @app.delete("/api/projects/{slug}/folders")
    async def delete_folder(slug: str, path: str) -> dict[str, Any]:
        """Remove a pasta; os assets dela (e subpastas) sobem para a pasta-mãe. Nada é apagado."""
        p = project_or_404(slug)
        path = normalize_folder(path)
        if not path:
            raise HTTPException(400, "a raiz não pode ser removida")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        moved = 0
        for s in p.store.slugs():
            f = p.store.get_meta(s).folder
            if f == path or f.startswith(path + "/"):
                p.store.set_meta(s, folder=parent)
                moved += 1
        p.config.folders = [f for f in p.config.folders if not (f == path or f.startswith(path + "/"))]
        p.save()
        jobs.emit({"type": "asset", "project": p.slug, "ref": ""})
        return {"moved_to": parent, "moved": moved}

    @app.post("/api/projects/{slug}/assets/organize")
    async def organize_assets(slug: str, body: OrganizeBody) -> dict[str, Any]:
        """Move vários assets para uma pasta e/ou define a categoria."""
        p = project_or_404(slug)
        done = []
        for s in body.slugs:
            try:
                p.store.set_meta(s.split("@")[0], folder=body.folder, category=body.category,
                                 clear_category=body.clear_category)
                done.append(s)
            except FileNotFoundError:
                continue
        if body.folder:
            f = normalize_folder(body.folder)
            if f and f not in p.config.folders:
                p.config.folders.append(f)
                p.save()
        jobs.emit({"type": "asset", "project": p.slug, "ref": ""})
        return {"updated": done}

    @app.post("/api/projects/{slug}/uploads", status_code=201)
    async def upload_temp(slug: str, file: UploadFile = File(...)) -> dict[str, str]:
        """Arquivo avulso (máscara, imagem de entrada) para usar como param de pipeline. Devolve caminho absoluto."""
        import uuid

        p = project_or_404(slug)
        d = p.forge_dir / "uploads"
        d.mkdir(exist_ok=True)
        # subpasta única preserva o nome original (pipelines derivam o slug do nome do arquivo)
        original = Path(file.filename or "upload.png").name
        dest = d / uuid.uuid4().hex[:12] / original
        dest.parent.mkdir()
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"path": str(dest), "url": f"/files/{p.slug}/.forge/uploads/{dest.parent.name}/{dest.name}"}

    @app.get("/files/{slug}/{path:path}")
    async def serve_file(slug: str, path: str) -> FileResponse:
        p = project_or_404(slug)
        f = _safe_join(p.root, path)
        if not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    # ---- jobs ----------------------------------------------------------
    @app.post("/api/jobs", status_code=202)
    async def submit_job(req: JobRequest) -> dict[str, Any]:
        try:
            return jobs.submit(req).public()
        except (KeyError, FileNotFoundError, PipelineError, ContentPolicyError) as e:
            raise HTTPException(400, str(e).strip("'\""))

    @app.get("/api/jobs")
    async def list_jobs(project: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Histórico persistente (status pode ser lista: error,cancelled)."""
        return jobs.history(project=project, status=status, limit=limit)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        d = jobs.detail(job_id)
        if d is None:
            raise HTTPException(404)
        return d

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str, body: RetryBody | None = None) -> dict[str, Any]:
        """Reenvia com os mesmos parâmetros (params sobrescreve campos; new_seed gera variação)."""
        body = body or RetryBody()
        try:
            return jobs.retry(job_id, body.params, body.new_seed).public()
        except KeyError:
            raise HTTPException(404, f"job não encontrado: {job_id}")
        except (FileNotFoundError, PipelineError, ContentPolicyError) as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict[str, str]:
        try:
            jobs.forget(job_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"deleted": job_id}

    @app.delete("/api/jobs")
    async def clear_jobs(status: str | None = None, project: str | None = None) -> dict[str, int]:
        """Limpa o histórico (nunca apaga jobs em andamento)."""
        n = store.clear(status=status, project=project) if store else 0
        for jid in [j.id for j in jobs.list() if j.status not in ("queued", "running")
                    and (not status or j.status in status.split(",")) and (not project or j.request.project == project)]:
            jobs.jobs.pop(jid, None)
        return {"deleted": n}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        if job_id not in jobs.jobs:
            raise HTTPException(404)
        return jobs.cancel(job_id).public()

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        q = jobs.subscribe()
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            jobs.unsubscribe(q)

    # ---- geração avançada (laço com crítico) ---------------------------
    from .refine_server import RefineManager, build_router

    refine_mgr = RefineManager(projects, jobs)
    app.state.refine = refine_mgr
    app.include_router(build_router(projects, refine_mgr))
    from .souls_server import build_router as souls_router

    app.include_router(souls_router(projects, jobs))
    from .pixel_server import build_router as pixel_router

    app.include_router(pixel_router(projects))
    from .ai_server import build_router as ai_router

    app.include_router(ai_router(projects, jobs))
    from .workflows_server import build_router as workflows_router

    app.include_router(workflows_router())
    from .setup_server import build_router as setup_router

    app.include_router(setup_router(setup_tasks))
    from .options_server import build_router as options_router

    app.include_router(options_router(
        STARTED, BOOT_ID,
        active_jobs=lambda: [f"{j.id} ({j.request.pipeline})" for j in jobs.list() if j.status in ("queued", "running")],
        pipelines=lambda: len(get_registry().pipelines), errors=lambda: get_registry().errors))

    @app.on_event("startup")
    async def _resume_refine() -> None:
        refine_mgr.resume_interrupted()

    # ---- UI ------------------------------------------------------------
    if settings.web_dist and settings.web_dist.is_dir():
        index = settings.web_dist / "index.html"
        app.mount("/assets", StaticFiles(directory=settings.web_dist / "assets"), name="web-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            f = _safe_join(settings.web_dist, full_path) if full_path else index
            return FileResponse(f if f.is_file() else index)

    return app


def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(create_app(s), host=host or s.host, port=port or s.port, log_level="info")

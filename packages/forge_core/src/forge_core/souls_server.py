"""Rotas HTTP das Creator Souls: biblioteca, construção em segundo plano (com progresso via WS), busca,
export/import e anexos aos projetos."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .souls import get_library
from .souls.library import Soul

log = logging.getLogger("forge.souls")


class SoulCreate(BaseModel):
    name: str
    folder: str
    description: str = ""
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    catalog: str | None = None
    recursive: bool = True
    license: str = ""
    nsfw: bool = False
    describe: bool = True


class SoulPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    style: dict[str, str] | None = None
    pinned: list[str] | None = None
    license: str | None = None
    tags: list[str] | None = None


class AttachBody(BaseModel):
    id: str
    strength: float = Field(0.8, ge=0, le=1)
    enabled: bool = True
    modalities: list[str] = Field(default_factory=list)


def build_router(projects, jobs) -> APIRouter:
    r = APIRouter()
    building: dict[str, dict[str, Any]] = {}

    def lib():
        return get_library()

    def soul_or_404(sid: str) -> Soul:
        try:
            return lib().get(sid)
        except KeyError:
            raise HTTPException(404, f"soul não encontrada: {sid}") from None

    def view(s: Soul) -> dict[str, Any]:
        return {**s.summary(), "progress": building.get(s.id)}

    def run_in_thread(sid: str, fn) -> None:
        loop = asyncio.get_running_loop()

        def progress(v: float, msg: str) -> None:
            building[sid] = {"value": round(v, 3), "message": msg}
            loop.call_soon_threadsafe(jobs.emit, {"type": "soul", "id": sid, "progress": building[sid]})

        def work() -> None:
            try:
                fn(progress)
                evt = {"type": "soul", "id": sid, "status": "ready"}
            except Exception as e:  # erro fica no manifesto (status=error)
                log.exception("falha na tarefa da soul %s", sid)
                evt = {"type": "soul", "id": sid, "status": "error", "error": str(e)}
            building.pop(sid, None)
            loop.call_soon_threadsafe(jobs.emit, evt)

        building[sid] = {"value": 0.0, "message": "iniciando"}
        threading.Thread(target=work, name=f"soul-{sid}", daemon=True).start()

    @r.get("/api/souls")
    async def list_souls() -> list[dict[str, Any]]:
        return [view(s) for s in lib().list()]

    @r.post("/api/souls", status_code=202)
    async def create_soul(body: SoulCreate) -> dict[str, Any]:
        from .souls.ingest import build

        folder = Path(body.folder)
        if not folder.is_dir():
            raise HTTPException(400, f"pasta não encontrada: {folder}")
        s = lib().create(body.name, description=body.description, license=body.license, nsfw=body.nsfw)
        run_in_thread(s.id, lambda prog: build(s, folder, include=body.include or None, exclude=body.exclude or None,
                                                catalog=body.catalog, recursive=body.recursive, describe=body.describe,
                                                on_progress=prog))
        return view(s)

    @r.get("/api/souls/{sid}")
    async def get_soul(sid: str) -> dict[str, Any]:
        return view(soul_or_404(sid))

    @r.patch("/api/souls/{sid}")
    async def patch_soul(sid: str, body: SoulPatch) -> dict[str, Any]:
        s = soul_or_404(sid)
        m = s.manifest
        for k in ("name", "description", "pinned", "license", "tags"):
            v = getattr(body, k)
            if v is not None:
                setattr(m, k, v)
        if body.style is not None:
            m.style = m.style.model_copy(update=body.style)
            m.source["style_auto"] = False  # editado à mão: rebuild não sobrescreve
        s.save()
        return view(s)

    @r.post("/api/souls/{sid}/rebuild", status_code=202)
    async def rebuild(sid: str) -> dict[str, Any]:
        from .souls.ingest import build

        s = soul_or_404(sid)
        src = s.manifest.source
        if not src.get("folder"):
            raise HTTPException(400, "soul importada sem pasta de origem")
        run_in_thread(sid, lambda prog: build(s, Path(src["folder"]), include=src.get("include") or None,
                                              exclude=src.get("exclude") or None, catalog=src.get("catalog"),
                                              recursive=src.get("recursive", True), on_progress=prog))
        return view(s)

    @r.post("/api/souls/{sid}/captions", status_code=202)
    async def captions(sid: str, limit: int | None = None) -> dict[str, Any]:
        from .souls.captions import caption_entries

        s = soul_or_404(sid)
        run_in_thread(sid, lambda prog: caption_entries(s, limit=limit, on_progress=prog))
        return view(s)

    @r.delete("/api/souls/{sid}")
    async def delete_soul(sid: str) -> dict[str, str]:
        soul_or_404(sid)
        for p in projects.list():
            if any(x.id == sid for x in p.config.souls):
                p.detach_soul(sid)
        lib().delete(sid)
        return {"deleted": sid}

    @r.get("/api/souls/{sid}/search")
    async def search(sid: str, q: str = "", k: int = 24, category: str | None = None) -> list[dict[str, Any]]:
        s = soul_or_404(sid)
        if not q.strip():
            ents = s.entries()
            if category:
                ents = [e for e in ents if e["category"].lower().startswith(category.lower())]
            pinned = set(s.manifest.pinned)
            ents.sort(key=lambda e: (e["id"] not in pinned, e["id"]))
            return ents[:k]
        return s.retrieve(q, k, categories=[category] if category else None)

    @r.get("/api/souls/{sid}/refs/{rid}.png")
    async def ref_image(sid: str, rid: str) -> FileResponse:
        s = soul_or_404(sid)
        f = s.ref_path(rid)
        if not f.exists() or f.parent != s.refs_dir:
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "max-age=86400"})

    @r.get("/api/souls/{sid}/cover.png")
    async def cover(sid: str) -> FileResponse:
        s = soul_or_404(sid)
        if not s.cover.exists():
            raise HTTPException(404)
        return FileResponse(s.cover, headers={"Cache-Control": "no-cache"})

    @r.get("/api/souls/{sid}/export")
    async def export(sid: str) -> FileResponse:
        from .souls.package import export_soul

        s = soul_or_404(sid)
        out = Path(tempfile.mkdtemp()) / f"{sid}.soul"
        await asyncio.to_thread(export_soul, s, out)
        return FileResponse(out, filename=f"{sid}.soul", media_type="application/zip")

    @r.post("/api/souls/import", status_code=201)
    async def import_(file: UploadFile = File(...), name: str | None = Form(None)) -> dict[str, Any]:
        from .souls.package import import_soul

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (file.filename or "x.soul")
            p.write_bytes(await file.read())
            try:
                s = await asyncio.to_thread(import_soul, lib(), p, name)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
        return view(s)

    # ---- anexos aos projetos ------------------------------------------
    def project_or_404(slug: str):
        try:
            return projects.resolve(slug)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, f"projeto não encontrado: {slug}") from None

    @r.post("/api/projects/{slug}/souls")
    async def attach(slug: str, body: AttachBody) -> dict[str, Any]:
        p = project_or_404(slug)
        try:
            if any(s.id == body.id for s in p.config.souls):
                p.update_soul(body.id, strength=body.strength, enabled=body.enabled, modalities=body.modalities)
            else:
                p.attach_soul(body.id, body.strength, body.modalities)
                if not body.enabled:
                    p.update_soul(body.id, enabled=False)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        return p.summary()

    @r.delete("/api/projects/{slug}/souls/{sid}")
    async def detach(slug: str, sid: str) -> dict[str, Any]:
        p = project_or_404(slug)
        p.detach_soul(sid)
        return p.summary()

    @r.get("/api/projects/{slug}/soul-refs")
    async def project_refs(slug: str, q: str, k: int = 2, modality: str = "2d") -> list[dict[str, Any]]:
        """Referências que as Souls do projeto dariam para este prompt (prévia do RAG na UI)."""
        from .souls import soul_context

        p = project_or_404(slug)
        out = []
        for soul, strength in soul_context(p, modality):
            for e in soul.retrieve(q, k):
                out.append({**e, "soul": soul.id, "ref": f"soul:{soul.id}/{e['id']}",
                            "url": f"/api/souls/{soul.id}/refs/{e['id']}.png"})
        return out

    return r

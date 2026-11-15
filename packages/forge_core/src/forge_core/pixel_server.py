"""Rotas HTTP do Pixel Studio (pacote forge_pixel, importado sob demanda): configuração de paleta/specs do
projeto e validação de um pixel map sem salvar (usado pelo editor ao vivo)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ValidateBody(BaseModel):
    map: dict[str, Any] | None = None
    asset: str | None = None             # alternativa: validar um asset salvo (slug ou slug@v003)
    spec: str | dict[str, Any] | None = None
    palette_group: str | None = None
    name: str | None = None
    references: list[str] = []
    material: str | None = None
    tags: list[str] = []
    autotile: bool = False               # asset é um conjunto do pixel.autotile (valida máscaras/emendas)


def _pixel():
    try:
        import forge_pixel  # noqa: F401
        from forge_pixel import config, pixelmap, spec, validators
    except ImportError:
        raise HTTPException(503, "pacote forge_pixel não instalado") from None
    return config, pixelmap, spec, validators


def build_router(projects) -> APIRouter:
    r = APIRouter()

    def project_or_404(slug: str):
        try:
            return projects.resolve(slug)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, f"projeto não encontrado: {slug}") from None

    @r.get("/api/projects/{slug}/pixel/config")
    async def pixel_config(slug: str) -> dict[str, Any]:
        config, _, _, _ = _pixel()
        p = project_or_404(slug)
        pal = config.load_palette(p)
        specs = config.load_specs(p)
        return {"palette": {"core": pal.core, "groups": {k: {"cores": v} for k, v in pal.groups.items()},
                            "fallback": pal.fallback},
                "specs": {k: s.model_dump(mode="json") for k, s in specs.items()}}

    @r.post("/api/projects/{slug}/pixel/validate")
    async def pixel_validate(slug: str, body: ValidateBody) -> dict[str, Any]:
        config, pixelmap, spec_mod, validators = _pixel()
        p = project_or_404(slug)
        if body.asset and body.autotile:
            from forge_pixel.api import validate_autotile_asset

            try:
                return validate_autotile_asset(p, body.asset).model_dump(mode="json")
            except (KeyError, FileNotFoundError, ValueError) as e:
                raise HTTPException(400, str(e)) from None
        if body.asset:
            from forge_pixel.api import validate_asset

            try:
                rep = validate_asset(p, body.asset, body.spec, body.references or None,
                                     palette_group=body.palette_group, name=body.name,
                                     material=body.material, tags=body.tags or None)
            except (KeyError, FileNotFoundError, ValueError) as e:
                raise HTTPException(400, str(e)) from None
            return rep.model_dump(mode="json")
        if body.map is None:
            raise HTTPException(400, "informe map ou asset")
        try:
            pm = pixelmap.parse(body.map)
            img = pixelmap.render(pm)
        except pixelmap.PixelMapError as e:
            raise HTTPException(400, f"pixel map inválido: {e}") from None
        specs = config.load_specs(p)
        try:
            sp = spec_mod.resolve_spec(body.spec, specs, (pm.width, pm.height))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from None
        rep = validators.validate(img, sp, config.load_palette(p), name=body.name, side=pm.side,
                                  palette_group=body.palette_group or sp.palette_group,
                                  material=body.material, tags=body.tags or None)
        return rep.model_dump(mode="json")

    return r

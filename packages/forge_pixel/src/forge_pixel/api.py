"""API para o servidor/MCP: validar um asset existente (ou arquivo solto) sem criar versão.

    from forge_pixel.api import validate_asset
    report = validate_asset(project, "torso-coat@v002", spec="torso")
    report.ok, report.to_markdown(), report.model_dump()
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from PIL import Image

from .config import load_palette, load_specs
from .spec import AssetSpec, palette_group_for_file, resolve_spec, side_of_file, spec_name_for_file
from .validators import Report, validate

if TYPE_CHECKING:
    from forge_core.assets import AssetVersion
    from forge_core.project import Project


def _resolve_file(project: "Project", ref: str) -> tuple["AssetVersion | None", Path]:
    from forge_core.runner import resolve_input

    av, path = resolve_input(project, ref)
    if av is not None:
        files = project.store.files_of(av, "primary") or project.store.files_of(av)
        pngs = [f for f in files if f.suffix.lower() == ".png"] or files
        if not pngs:
            raise FileNotFoundError(f"asset sem arquivo: {ref}")
        return av, pngs[0]
    if path is None:
        raise FileNotFoundError(f"referência vazia: {ref!r}")
    return None, path


def validate_asset(project: "Project", ref: str, spec: str | dict[str, Any] | AssetSpec | None = None,
                   references: Sequence[str] | None = None, *, palette_group: str | None = None,
                   name: str | None = None, material: str | None = None,
                   tags: Sequence[str] | None = None) -> Report:
    """Valida o PNG primary de `ref` ("slug", "slug@v003" ou caminho de arquivo).

    spec: preset/dict/AssetSpec; vazio = a spec gravada no asset (extra.spec), senão deduzida do nome
          do arquivo no padrão do jogo (part_..._R_v01.png -> limb), senão genérica do tamanho da imagem.
    references: outros assets/arquivos da mesma categoria. Se o asset é um lado _L e uma referência é
          o _R correspondente, roda o cheque do par (mesma forma, 78%); as demais conferem o canvas.
    name: nome final do arquivo no jogo (cheque de nome); vazio = extra.pixel.file_name ou nome do arquivo solto.
    material: copper/iron/soul/wood/leather (cor por material); vazio = extra.pixel.material ou o da spec.
    tags: tags de cor (carmim/ouro); vazio (None) = extra.pixel.tags.
    """
    av, path = _resolve_file(project, ref)
    extra = dict(av.extra) if av is not None else {}
    pix = extra.get("pixel") or {}
    if name is None:
        name = pix.get("file_name") or (path.name if av is None else None)
    with Image.open(path) as im:
        img = im.convert("RGBA")
    specs = load_specs(project)
    if spec is None:
        if isinstance(extra.get("spec"), dict):
            spec = extra["spec"]
        else:
            guess = spec_name_for_file(name or path.name, canvas=img.size)
            spec = guess if guess in specs else None
    sp = resolve_spec(spec, specs, canvas=img.size)
    side = side_of_file(name) if name else None
    if side is None and pix.get("side") in ("L", "R"):
        side = pix["side"]
    palette = load_palette(project)
    group = palette_group or pix.get("palette_group") or sp.palette_group
    if group is None and name:
        g = palette_group_for_file(name)
        group = g if g in palette.groups else None

    pair = None
    refs: list[Image.Image] = []
    for r in references or []:
        rav, rpath = _resolve_file(project, r)
        with Image.open(rpath) as rim:
            rimg = rim.convert("RGBA")
        rname = ((rav.extra.get("pixel") or {}).get("file_name") if rav is not None else None) or rpath.name
        rside = side_of_file(rname) or ((rav.extra.get("pixel") or {}).get("side") if rav is not None else None)
        if side == "L" and rside == "R" and pair is None:
            pair = rimg
        else:
            refs.append(rimg)
    if material is None:
        material = pix.get("material")
    if tags is None:
        tags = pix.get("tags") or []
    return validate(img, sp, palette, name=name, side=side, pair=pair, palette_group=group, references=refs or None,
                    material=material, tags=list(tags))


def validate_autotile_asset(project: "Project", ref: str) -> Report:
    """Valida um conjunto gravado pelo pixel.autotile ("slug" ou "slug@v002"): lê os arquivos
    `*_mNN_vNN.png` da versão e roda `check_autotile` (Report com spec "autotile")."""
    import re

    from forge_core.runner import resolve_input

    from .validators import autotile_report

    av, _ = resolve_input(project, ref)
    if av is None:
        raise FileNotFoundError(f"asset não encontrado: {ref}")
    tiles: dict[int, Image.Image] = {}
    for f in project.store.files_of(av, "part"):
        m = re.search(r"_m(\d{2})_v\d{2}\.png$", f.name)
        if m:
            with Image.open(f) as im:
                tiles[int(m.group(1))] = im.convert("RGBA")
    if not tiles:
        raise ValueError(f"{ref}: nenhum arquivo _mNN (o asset veio do pixel.autotile com set full16/both?)")
    return autotile_report(tiles, name=av.ref)     # tamanho do tile (16 ou 11) vem do próprio base (15)

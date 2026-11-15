"""Pipelines da modalidade Pixel art (CPU, sem ComfyUI, determinísticos).

- pixel.map  "Pixel map → sprite": desenha um pixel map (JSON) no canvas exato da spec.
- pixel.fit  "Encaixar imagem na spec": encaixa qualquer imagem (render de IA, rascunho) na spec.
- pixel.autotile "Autotile do tile base": as 16 variantes de borda (máscara cardinal, ver autotile.py)
  e/ou a lista mínima do jogo, atlas + JSON, mapa de teste e relatório (ver AutotileTiles).
- pixel.poses "Poses de inimigo (folha)": N pixel maps -> quadros, silhuetas de dano (_hit), folha + JSON
  e relatório de coerência por classe de pose (ver PoseSet).
- pixel.slice "Fatiar imagem em peças": recorta peças de uma imagem inteira (ex.: personagem gerado por IA)
  com um JSON de fatiamento e encaixa cada uma no canvas da spec (ver slice.py e SliceParts).
- pixel.parallax "Camada de parallax": publica uma camada de fundo 640x360 (pixel map ou PNG) com a
  paleta emprestada, a emenda horizontal e a pilha contra os tiles sólidos (ver parallax.py e ParallaxLayer).

Cada versão grava:
    sprite.png        primary  — canvas exato, NÃO ampliado (16 PPU no Unity)
    sprite_L.png      part     — lado de trás quando side="both" (mesma forma, 22% mais escura)
    pixelmap.json     source   — o mapa editável (no fit, gerado a partir do PNG encaixado)
    preview.png       preview  — ampliação com grade, área segura e pivô
    report.json/.md   report   — resultado dos validadores (aceite-assetforge.md)
extra.validation = resumo do relatório; extra.spec = a spec usada; extra.pixel = lado, grupo, material, tags, cores.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from forge_core import OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context

from . import pixelmap as pmod
from .config import load_palette, load_specs
from .fit import fit_to_spec
from .palette import nearest
from .preview import zoom_preview
from .raster import save_png
from .spec import AssetSpec, PaletteSet, resolve_spec, side_of_file
from .validators import Report, autotile_report, validate

Side = Literal["R", "L", "both"]


def _preview_scale(spec: AssetSpec) -> int:
    return max(4, min(16, 320 // max(spec.canvas)))


def _side_name(file_name: str | None, side: str) -> str | None:
    """part_x_arm_up_R_v01.png -> part_x_arm_up_L_v01.png (ou acrescenta o lado antes da versão)."""
    if not file_name:
        return None
    cur = side_of_file(file_name)
    if cur:
        return file_name.replace(f"_{cur}_v", f"_{side}_v", 1)
    import re

    return re.sub(r"(_v\d{2})", rf"_{side}\1", file_name, count=1)


def _input_meta(ctx: Context) -> dict[str, Any]:
    return dict(ctx.input.extra) if ctx.input is not None else {}


def _resolve(ctx: Context, spec: str | dict[str, Any] | None, canvas: tuple[int, int] | None) -> tuple[AssetSpec, PaletteSet]:
    specs = load_specs(ctx.project)
    if spec is None:
        prev = _input_meta(ctx).get("spec")
        if isinstance(prev, dict):
            spec = prev
    return resolve_spec(spec, specs, canvas=canvas), load_palette(ctx.project)


def write_outputs(ctx: Context, spec: AssetSpec, palette: PaletteSet, primary: Image.Image, pm: pmod.PixelMap, *,
                  side: Side, group: str | None, file_name: str | None, right: Image.Image | None = None,
                  left: Image.Image | None = None, extra: dict[str, Any] | None = None, material: str | None = None,
                  tags: list[str] | None = None) -> list[OutputFile]:
    """Grava sprite(s), mapa, prévia e relatórios; preenche ctx.meta. `right`/`left` só quando side='both'.
    material/tags: cor por material e tags de cor (validadores do §7), gravados em extra.pixel."""
    d = ctx.version_dir
    files: list[OutputFile] = []
    prim_side = "L" if side == "L" else ("R" if side == "both" else (side_of_file(file_name or "") or None))
    prim_name = _side_name(file_name, "L") if side == "L" else file_name
    save_png(primary, d / "sprite.png")
    files.append(OutputFile(d / "sprite.png", "primary", "image/png"))
    tags = list(tags or [])
    report = validate(primary, spec, palette, name=prim_name, side=prim_side, palette_group=group,
                      material=material, tags=tags)
    reports: dict[str, Report] = {"primary": report}
    if side == "both" and left is not None:
        save_png(left, d / "sprite_L.png")
        files.append(OutputFile(d / "sprite_L.png", "part", "image/png"))
        reports["L"] = validate(left, spec, palette, name=_side_name(file_name, "L"), side="L", pair=right,
                                palette_group=group, material=material, tags=tags)
    (d / "pixelmap.json").write_text(pm.to_json(), encoding="utf-8")
    files.append(OutputFile(d / "pixelmap.json", "source", "application/json"))
    scale = _preview_scale(spec)
    prev = zoom_preview(primary, spec, scale=scale)
    if side == "both" and left is not None:
        prev_l = zoom_preview(left, spec, scale=scale)
        both = Image.new("RGB", (prev.width + prev_l.width + 8, max(prev.height, prev_l.height)), (0x1a, 0x18, 0x30))
        both.paste(prev, (0, 0))
        both.paste(prev_l, (prev.width + 8, 0))
        prev = both
    prev.save(d / "preview.png", format="PNG")
    files.append(OutputFile(d / "preview.png", "preview", "image/png"))
    rep_json = {"ok": all(r.ok for r in reports.values()), **{k: r.model_dump(mode="json") for k, r in reports.items()}}
    (d / "report.json").write_text(json.dumps(rep_json, ensure_ascii=False, indent=1), encoding="utf-8")
    md = "# Relatório de validação (Asset Forge Pixel)\n\n" + "\n".join(r.to_markdown() for r in reports.values())
    (d / "report.md").write_text(md, encoding="utf-8")
    files += [OutputFile(d / "report.json", "report", "application/json"),
              OutputFile(d / "report.md", "report", "text/markdown")]
    ctx.meta["kind"] = spec.kind
    ex = ctx.meta.setdefault("extra", {})
    ex["validation"] = report.summary()
    if "L" in reports:
        ex["validation_L"] = reports["L"].summary()
        ex["validation"]["ok_all"] = rep_json["ok"]
    ex["spec"] = spec.model_dump(mode="json")
    ex["pixel"] = {"side": side, "palette_group": group, "file_name": file_name, "material": material, "tags": tags,
                   "palette": "fallback" if palette.fallback else "project", **(extra or {})}
    for r in reports.values():
        ctx.log(("validadores OK" if r.ok else "validadores REPROVARAM: " + ", ".join(c.id for c in r.failed if c.elim))
                + (f" ({r.side})" if r.side else ""))
    return files


def quantize_map(pm: pmod.PixelMap, palette: PaletteSet, group: str | None,
                 outline_color: str) -> tuple[pmod.PixelMap, dict[str, str]]:
    """Troca as cores da legenda fora da paleta (núcleo + grupo) pela permitida mais próxima (OKLab).
    Devolve o mapa (o mesmo, se nada mudou) e {cor_antiga: cor_nova}. Paleta de fallback: não mexe."""
    if palette.fallback:
        return pm, {}
    allowed = palette.allowed(group)
    interior = sorted(allowed - {outline_color}) or sorted(allowed)
    legend, changes = {}, {}
    for ch, col in pm.legend.items():
        rgb, a = pmod.color_alpha(col)            # "#rrggbbaa": a paleta vale para o RGB, o alfa fica
        if rgb in allowed:
            legend[ch] = col
        else:
            new = nearest(rgb, interior)
            changes[col] = legend[ch] = new if a == 255 else f"{new}{a:02x}"
    return (pm.model_copy(update={"legend": legend}) if changes else pm), changes


# ---------------------------------------------------------------- pixel.map

class MapParams(PipelineParams):
    map: dict[str, Any] | None = Field(None, description='pixel map JSON ({"format": "asset-forge-pixelmap/1", "width", '
                                                         '"height", "legend", "rows", "outline", ...}); vazio = usa o '
                                                         'pixelmap.json do input_asset')
    spec: str | dict[str, Any] | None = Field(None, description="preset (head, torso, limb, icon, tile...) ou spec inline; "
                                                                "vazio = spec do input_asset ou genérica do tamanho do mapa")
    palette_group: str | None = Field(None, description="grupo da paleta do projeto (personagem/chest, tile/stone, icons...)")
    side: Side = Field("R", description='"R" frente; "L" lado de trás (22% mais escuro); "both" grava os dois')
    quantize: bool = Field(True, description="trocar cores da legenda fora da paleta pela permitida mais próxima")
    file_name: str | None = Field(None, description="nome final no jogo (ex.: part_body_arm_up_R_v01.png) para o validador de nome")
    material: str | None = Field(None, description="material para a regra de cor (copper, iron, soul, wood, leather); "
                                                   "vazio = o do input_asset/spec")
    tags: list[str] | None = Field(None, description="tags de cor: danger/blood/castle/vania/life liberam carmim; "
                                                     "soul/reward/noble/fire/light liberam ouro; vazio = as do input_asset")


class PixelMapToSprite(Pipeline):
    id = "pixel.map"
    title = "Pixel map → sprite"
    description = ("Desenha um sprite a partir de um pixel map (texto + legenda): canvas, pivô e área segura exatos, "
                   "paleta travada, contorno automático de 1 px e validadores. Determinístico, sem IA.")
    resources = {"cpu"}
    kind = "sprite"
    Params = MapParams
    accepts_input = True
    same_asset = True

    def default_slug(self, params: MapParams) -> str:  # type: ignore[override]
        if params.file_name:
            return Path(params.file_name).stem
        if isinstance(params.spec, str):
            return f"pixel {params.spec}"
        return "pixel sprite"

    def _load_map(self, ctx: Context, p: MapParams) -> pmod.PixelMap:
        if p.map is not None:
            return pmod.parse(p.map)
        src = ctx.input_files("source")
        js = [f for f in src if f.suffix == ".json"]
        if js:
            return pmod.parse(js[0].read_text(encoding="utf-8"))
        prim = ctx.input_files("primary")
        if prim:
            return pmod.from_image(Image.open(prim[0]))
        raise ValueError("pixel.map precisa de `map` ou de um input_asset com pixelmap.json/PNG")

    def run(self, ctx: Context, p: MapParams) -> list[OutputFile]:
        pm = self._load_map(ctx, p)
        spec, palette = _resolve(ctx, p.spec, (pm.width, pm.height))
        if (pm.width, pm.height) != tuple(spec.canvas):
            raise ValueError(f"o mapa tem {pm.width}x{pm.height}, mas a spec {spec.name!r} exige "
                             f"{spec.width}x{spec.height}")
        prev = _input_meta(ctx).get("pixel") or {}     # edição: herda grupo e nome do asset de entrada
        group = p.palette_group or prev.get("palette_group") or spec.palette_group
        file_name = p.file_name or prev.get("file_name")
        material = p.material or prev.get("material")
        tags = p.tags if p.tags is not None else prev.get("tags")
        changes: dict[str, str] = {}
        if p.quantize:
            pm, changes = quantize_map(pm, palette, group, spec.outline_color)
            if changes:
                ctx.log("cores fora da paleta quantizadas: " + ", ".join(f"{a}->{b}" for a, b in changes.items()))
        right = pmod.render(pm, side="R")
        left = pmod.render(pm, side="L") if p.side in ("L", "both") else None
        primary = left if p.side == "L" else right
        stored = pm.model_copy(update={"side": "L" if p.side == "L" else ("R" if p.side == "both" else pm.side)})
        ctx.progress(0.6, "validando")
        return write_outputs(ctx, spec, palette, primary, stored, side=p.side, group=group, file_name=file_name,
                             right=right, left=left, extra={"quantized": changes}, material=material, tags=tags)


# ---------------------------------------------------------------- pixel.fit

class FitParams(PipelineParams):
    spec: str | dict[str, Any] = Field(..., description="preset (head, torso, limb, hand, boot, cape, back, icon, tile...) ou spec inline")
    palette_group: str | None = Field(None, description="grupo da paleta do projeto (vazio = o da spec)")
    side: Side = Field("R", description='"R", "L" (22% mais escuro) ou "both"')
    upscale: bool = Field(False, description="ampliar (vizinho mais próximo, fator inteiro) imagens menores que a área segura")
    key_background: bool = Field(True, description="sem alfa: remover o fundo de cor sólida ligado às bordas")
    file_name: str | None = Field(None, description="nome final no jogo, para o validador de nome")
    material: str | None = Field(None, description="material para a regra de cor (copper, iron, soul, wood, leather)")
    tags: list[str] | None = Field(None, description="tags de cor: danger/blood/castle/vania/life liberam carmim; "
                                                     "soul/reward/noble/fire/light liberam ouro")


class FitToSpec(Pipeline):
    id = "pixel.fit"
    title = "Encaixar imagem na spec"
    description = ("Encaixa qualquer imagem (render de IA sem fundo, rascunho) no canvas exato da spec: recorta, reduz "
                   "para a área segura, ancora no pivô, alfa binário, quantiza na paleta, remove ilhas e redesenha o "
                   "contorno de 1 px. Grava também o pixel map para edição.")
    resources = {"cpu"}
    kind = "sprite"
    Params = FitParams
    requires_input = True

    def default_slug(self, params: FitParams) -> str:  # type: ignore[override]
        if params.file_name:
            return Path(params.file_name).stem
        return f"pixel {params.spec}" if isinstance(params.spec, str) else "pixel fit"

    def run(self, ctx: Context, p: FitParams) -> list[OutputFile]:
        files = ctx.input_files("primary") or ctx.input_files()
        imgs = [f for f in files if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tga")]
        if not imgs:
            raise ValueError("pixel.fit precisa de um input_asset de imagem")
        spec, palette = _resolve(ctx, p.spec, None)
        group = p.palette_group or spec.palette_group
        with Image.open(imgs[0]) as src:
            src.load()
            ctx.progress(0.2, "encaixando")
            right = fit_to_spec(src, spec, palette, palette_group=group, upscale=p.upscale, key_bg=p.key_background)
        left = pmod.shade_side(right, 0.78, keep=spec.outline_color) if p.side in ("L", "both") else None
        primary = left if p.side == "L" else right
        pm = pmod.from_image(primary, outline_color=spec.outline_color)
        if p.side == "L":
            pm = pm.model_copy(update={"side": None})
        ctx.progress(0.7, "validando")
        return write_outputs(ctx, spec, palette, primary, pm, side=p.side, group=group, file_name=p.file_name,
                             right=right, left=left, extra={"source": imgs[0].name}, material=p.material, tags=p.tags)


# ---------------------------------------------------------------- pixel.autotile

class AutotileParams(PipelineParams):
    edge_style: str | dict[str, Any] = Field("grass", description='preset ("grass", "stone", "brick") ou dict do '
                                                                    'EdgeStyle (aceita {"preset": "grass", ...})')
    material: str | None = Field(None, description="material dos nomes mínimos (dirt -> dirt_top...); vazio = "
                                                   "o fim do prefixo")
    palette_group: str | None = Field(None, description="grupo da paleta (tile/dirt...); vazio = o do input_asset")
    set: Literal["full16", "minimal", "both"] = Field("both", description="full16 = as 16 máscaras; minimal = "
                                                                         "top, top_l, top_r, top_b; both = as duas")
    prefix: str | None = Field(None, description="prefixo dos arquivos (tile_catacumba_dirt); vazio = nome do "
                                                 "arquivo do input_asset sem _vNN")
    version: int = Field(1, ge=1, le=99, description="sufixo _vNN dos arquivos")
    seed: int = Field(1, description="semente dos detalhes (pontas, tufos); mesma semente = mesmos PNGs")
    columns: int = Field(4, ge=1, le=32, description="colunas do atlas")


class AutotileTiles(Pipeline):
    """Grava, por versão:
        <prefixo>_m00_vNN.png .. _m15_vNN.png   part    — as 16 máscaras (set full16/both)
        <prefixo>_top[_l|_r|_b]_vNN.png         part    — lista mínima do jogo (set minimal/both)
        atlas.png                               primary — todos os tiles exportados numa grade (16 PPU)
        atlas.json                              source  — retângulos [x, y, l, a] (topo-esquerda), máscara, nome
        test_map.png / test_map_4x.png          preview — mapa 12x8 com buracos (1x e ampliado 4x)
        report.json / report.md                 report  — check_autotile + validação de cada arquivo
    """

    id = "pixel.autotile"
    title = "Autotile do tile base"
    description = ("A partir de um tile base aprovado (16x16 ou 11x11, tileável), desenha as 16 variantes de borda da máscara "
                   "cardinal (cima=1, direita=2, baixo=4, esquerda=8; 15 = base) e/ou a lista mínima do jogo "
                   "(_top, _top_l, _top_r, _top_b), com atlas + JSON, mapa de teste e validadores. Determinístico.")
    resources = {"cpu"}
    kind = "tileset"
    Params = AutotileParams
    requires_input = True

    def default_slug(self, params: AutotileParams) -> str:  # type: ignore[override]
        return f"{params.prefix or 'tile'} autotile"

    def run(self, ctx: Context, p: AutotileParams) -> list[OutputFile]:
        import re

        from . import autotile as at
        from .spec import spec_name_for_file

        files_in = ctx.input_files("primary") or ctx.input_files()
        pngs = [f for f in files_in if f.suffix.lower() == ".png"]
        if not pngs:
            raise ValueError("pixel.autotile precisa de um input_asset com o tile base (PNG 16x16 ou 11x11)")
        with Image.open(pngs[0]) as im:
            base = im.convert("RGBA")
        if base.size not in ((at.TILE, at.TILE), (at.TILE11, at.TILE11)):
            raise ValueError(f"o tile base deve ter 16x16 ou 11x11 (veio {base.size[0]}x{base.size[1]})")
        size = base.size[0]
        prev = _input_meta(ctx).get("pixel") or {}
        palette = load_palette(ctx.project)
        specs = load_specs(ctx.project)
        group = p.palette_group or prev.get("palette_group")
        prefix = p.prefix or re.sub(r"_v\d{2}$", "", Path(prev.get("file_name") or pngs[0].name).stem)
        material = p.material or prefix.rsplit("_", 1)[-1]
        allowed = sorted(palette.allowed(group)) if not palette.fallback else []
        edge_style = p.edge_style
        if isinstance(edge_style, str):            # base de 11 px: "grass" vira "grass11" (crosta mais rasa)
            edge_style = at.edge_preset_for_size(edge_style, size)
        elif isinstance(edge_style, dict) and isinstance(edge_style.get("preset"), str):
            edge_style = {**edge_style, "preset": at.edge_preset_for_size(edge_style["preset"], size)}
        style = at.EdgeStyle.coerce(edge_style)
        vs = f"_v{p.version:02d}"
        d = ctx.version_dir
        ctx.progress(0.2, "desenhando as variantes")
        full = at.make_autotile_set(base, style, allowed, seed=p.seed)
        exported: dict[str, Image.Image] = {}
        masks: dict[str, int] = {}
        if p.set in ("full16", "both"):
            for m in at.ALL_MASKS:
                name = f"{prefix}_{at.mask_name(m)}{vs}.png"
                exported[name], masks[name] = full[m], m
        if p.set in ("minimal", "both"):
            mini = at.minimal_set(base, style, material, allowed, seed=p.seed, tiles_by_mask=full)
            stem = prefix[: -len(material) - 1] if prefix.endswith("_" + material) else prefix
            for key, img in mini.items():
                name = f"{stem}_{key}{vs}.png"
                exported[name] = img
                masks[name] = at.MINIMAL_MASKS[key[len(material) + 1:]][0]
        files: list[OutputFile] = []
        for name, img in exported.items():
            save_png(img, d / name)
            files.append(OutputFile(d / name, "part", "image/png"))
        sheet, meta = at.atlas(exported, columns=p.columns, masks=masks)
        meta.update({"prefix": prefix, "material": material, "palette_group": group, "seed": p.seed,
                     "edge_style": style.model_dump(mode="json")})
        save_png(sheet, d / "atlas.png")
        at.write_atlas_json(meta, d / "atlas.json")
        files += [OutputFile(d / "atlas.png", "primary", "image/png"),
                  OutputFile(d / "atlas.json", "source", "application/json")]
        ctx.progress(0.6, "validando")
        save_png(at.render_test_map(full), d / "test_map.png")
        big = at.render_test_map(full, background="#1a1830")
        save_png(big.resize((big.width * 4, big.height * 4), Image.Resampling.NEAREST), d / "test_map_4x.png")
        files += [OutputFile(d / "test_map.png", "preview", "image/png"),
                  OutputFile(d / "test_map_4x.png", "preview", "image/png")]
        set_rep = autotile_report(full, name=f"{prefix}_mNN{vs}.png")
        per_file: dict[str, Report] = {}
        for name, img in exported.items():
            fallback = "tile_edge" if size == at.TILE else "tile11_edge"
            sp = specs.get(spec_name_for_file(name, canvas=img.size) or fallback) or specs[fallback]
            per_file[name] = validate(img, sp, palette, name=name, palette_group=group)
        ok = set_rep.ok and all(r.ok for r in per_file.values())
        rep_json = {"ok": ok, "autotile": set_rep.model_dump(mode="json"),
                    "files": {k: r.model_dump(mode="json") for k, r in per_file.items()}}
        (d / "report.json").write_text(json.dumps(rep_json, ensure_ascii=False, indent=1), encoding="utf-8")
        md = ["# Relatório de validação — autotile (Asset Forge Pixel)", "",
              "Máscara cardinal: cima=1, direita=2, baixo=4, esquerda=8 (bit = vizinho sólido); 15 = base.", "",
              set_rep.to_markdown(), *[r.to_markdown() for r in per_file.values()]]
        (d / "report.md").write_text("\n".join(md), encoding="utf-8")
        files += [OutputFile(d / "report.json", "report", "application/json"),
                  OutputFile(d / "report.md", "report", "text/markdown")]
        ctx.meta["kind"] = "tileset"
        ex = ctx.meta.setdefault("extra", {})
        ex["validation"] = {**set_rep.summary(), "ok_all": ok,
                            "files_failed": {k: r.summary()["failed"] for k, r in per_file.items() if not r.ok}}
        ex["pixel"] = {"palette_group": group, "material": material, "prefix": prefix, "set": p.set,
                       "seed": p.seed, "files": list(exported), "palette": "fallback" if palette.fallback else "project",
                       "tile_size": size, "edge_style": edge_style if isinstance(edge_style, str) else None}
        ex["atlas"] = {k: meta[k] for k in ("tile_size", "ppu", "columns", "rows", "width", "height")}
        bad = [k for k, r in per_file.items() if not r.ok]
        ctx.log("autotile: " + ("validadores OK" if ok else "validadores REPROVARAM: "
                                + ", ".join(c.id for c in set_rep.failed if c.elim)
                                + (("; arquivos: " + ", ".join(bad)) if bad else "")))
        return files


# ---------------------------------------------------------------- pixel.poses

class BodyParams(BaseModel):
    """Coerência só do corpo (validators.check_body_coherence; voadores: asas e garras ficam fora)."""

    model_config = ConfigDict(extra="forbid")
    box: tuple[int, int, int, int] = Field(..., description="(x0, y0, x1, y1) inclusivo, y de BAIXO para cima")
    body_colors: list[str] | None = Field(None, description="cores que contam como corpo (vazio = todo pixel na caixa)")
    wing_colors: list[str] = Field(default_factory=list, description="cores das asas (permitidas nas poses à parte)")
    compare: list[int] | None = Field(None, description="poses comparadas entre si (vazio = todas)")
    eye_color: str | None = Field(None, description="linha do olho = onde se mede a largura da cabeça")
    head_poses: list[int] | None = Field(None, description="poses cuja cabeça varia ± 1 px (vazio = compare)")
    head_offsets: dict[int, tuple[int, int]] | None = Field(None, description="{pose: (mín, máx)} relativos à menor "
                                                                             "cabeça de head_poses (ex.: mergulho)")
    boxes: dict[int, tuple[int, int, int, int]] | None = Field(None, description="caixa própria por pose")
    max_mask_diff: float = Field(0.40, description="diferença máxima de máscara do corpo entre pares")


class PosesParams(PipelineParams):
    maps: list[dict[str, Any]] = Field(..., min_length=1, description="pixel maps das poses, em ordem (mesmo canvas)")
    cyclic: list[str] = Field(default_factory=list, description="classes em laço (ciclo de caminhada): o par "
                                                                "última -> primeira também conta nos 8–60%")
    body: BodyParams | None = Field(None, description="coerência do corpo (voadores): roda check_body_coherence")
    classes: list[str] | None = Field(None, description='classe de cada pose (paralela a maps), ex.: ["locomocao", '
                                                       '"locomocao", "ataque", "ataque"]; vazio = todas na mesma')
    labels: list[str] | None = Field(None, description='nome de cada pose para as prévias (parado, passo...)')
    spec: str | dict[str, Any] = Field("enemy", description="preset (enemy, enemy_skeleton, enemy_flyer) ou spec inline")
    palette_group: str | None = Field(None, description="grupo da paleta (inimigo/esqueleto...); vazio = o da spec")
    prefix: str = Field("enemy_catacumba_inimigo_v01", description="prefixo dos arquivos: <prefixo>_<n>.png "
                                                                   "(e <prefixo>_<n>_hit.png)")
    mode: Literal["ground", "flyer"] = Field("ground", description="ground = linha do chão estável; "
                                                                 "flyer = centro de massa por classe")
    columns: int | None = Field(None, ge=1, le=64, description="colunas da folha (vazio = uma linha, o strip)")
    hit: bool = Field(True, description="gravar a silhueta branca de cada pose (flash de dano)")
    hit_color: str = Field("#ffffff", description="cor da silhueta de dano")
    quantize: bool = Field(True, description="trocar cores da legenda fora da paleta pela permitida mais próxima")
    material: str | None = Field(None, description="material para a regra de cor (raro em inimigos)")
    tags: list[str] | None = Field(None, description="tags de cor somadas às da spec (inimigos já têm danger)")
    preview_scale: int = Field(6, ge=1, le=16, description="ampliação das prévias")
    sheet_meta: dict[str, Any] | None = Field(None, description="metadados livres somados ao sheet.json (ex.: "
                                                                "animações {nome: [quadros]}, ordem dos quadros)")


class PoseSet(Pipeline):
    """Grava, por versão:
        <prefixo>_<n>.png       part    — cada pose no canvas exato da spec (16 PPU)
        <prefixo>_<n>_hit.png   part    — silhueta chapada (#ffffff) da pose, para o flash de dano
        sheet.png               primary — as poses lado a lado (ou em grade), 1:1
        sheet.json              source  — retângulos, pivô (px e normalizado), classe e nome de cada quadro
        pixelmaps.json          source  — os mapas editáveis (lista, na ordem)
        preview.png             preview — tira ampliada com rótulos, chão e pivô + onion skin por classe
        report.json / .md       report  — check_pose_set por classe (+ laço das classes `cyclic`), coerência do
                                          corpo (`body`: check_body_coherence) e validação de cada pose
    `sheet_meta` soma chaves livres ao sheet.json (animações, ordem dos quadros, remapeamento de versões).
    """

    id = "pixel.poses"
    title = "Poses de inimigo (folha)"
    description = ("Desenha N poses de um inimigo a partir de pixel maps (mesmo canvas/pivô/paleta), grava cada "
                   "quadro, a silhueta branca de dano, a folha + JSON (retângulos, pivô, classes) e roda os "
                   "validadores de cada pose e de coerência por classe de pose (chão estável, paleta, 8–60%).")
    resources = {"cpu"}
    kind = "spritesheet"
    Params = PosesParams

    def default_slug(self, params: PosesParams) -> str:  # type: ignore[override]
        return f"{params.prefix} poses"

    def run(self, ctx: Context, p: PosesParams) -> list[OutputFile]:
        from .poses import animation_strip, hit_silhouette, onion_skin, pose_sheet, write_sheet_json
        from .spec import norm_hex
        from .validators import check_pose_set, pose_class_groups

        n = len(p.maps)
        if p.classes is not None and len(p.classes) != n:
            raise ValueError(f"classes tem {len(p.classes)} rótulos para {n} poses")
        if p.labels is not None and len(p.labels) != n:
            raise ValueError(f"labels tem {len(p.labels)} nomes para {n} poses")
        maps = [pmod.parse(m) for m in p.maps]
        spec, palette = _resolve(ctx, p.spec, (maps[0].width, maps[0].height))
        group = p.palette_group or spec.palette_group
        bad = [i for i, m in enumerate(maps) if (m.width, m.height) != tuple(spec.canvas)]
        if bad:
            raise ValueError(f"poses {bad} não têm o canvas da spec {spec.name!r} ({spec.width}x{spec.height})")
        changes: dict[str, str] = {}
        if p.quantize:
            for i, m in enumerate(maps):
                maps[i], ch = quantize_map(m, palette, group, spec.outline_color)
                changes.update(ch)
            if changes:
                ctx.log("cores fora da paleta quantizadas: " + ", ".join(f"{a}->{b}" for a, b in changes.items()))
        imgs = [pmod.render(m, side="R") for m in maps]
        d = ctx.version_dir
        files: list[OutputFile] = []
        names = [f"{p.prefix}_{i}.png" for i in range(n)]
        hit_color = norm_hex(p.hit_color)
        tags = list(p.tags or [])
        reports: dict[str, Report] = {}
        ctx.progress(0.3, "validando as poses")
        for name, img in zip(names, imgs):
            save_png(img, d / name)
            files.append(OutputFile(d / name, "part", "image/png"))
            reports[name] = validate(img, spec, palette, name=name, palette_group=group, material=p.material,
                                     tags=tags)
            if p.hit:
                hname = name[:-4] + "_hit.png"
                save_png(hit_silhouette(img, hit_color), d / hname)
                files.append(OutputFile(d / hname, "part", "image/png"))
        piv = spec.pivot_xy()
        labels = p.labels or [str(i) for i in range(n)]
        sheet, meta = pose_sheet(imgs, p.columns, names=names, labels=p.labels, classes=p.classes, pivot=piv)
        meta.update({"prefix": p.prefix, "spec": spec.name, "palette_group": group, "mode": p.mode,
                     "hit_suffix": "_hit" if p.hit else None, "cyclic": p.cyclic})
        for k, v in (p.sheet_meta or {}).items():
            if k in meta:
                raise ValueError(f"sheet_meta: a chave {k!r} é reservada")
            meta[k] = v
        save_png(sheet, d / "sheet.png")
        write_sheet_json(meta, d / "sheet.json")
        (d / "pixelmaps.json").write_text(json.dumps([m.to_dict() for m in maps], ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        files += [OutputFile(d / "sheet.png", "primary", "image/png"),
                  OutputFile(d / "sheet.json", "source", "application/json"),
                  OutputFile(d / "pixelmaps.json", "source", "application/json")]
        ctx.progress(0.6, "coerência das poses")
        set_checks = check_pose_set(imgs, mode=p.mode, classes=p.classes, cyclic=p.cyclic)
        if p.body is not None:
            from .validators import check_body_coherence

            b = p.body
            for i in [*(b.compare or []), *(b.head_poses or []), *(b.head_offsets or {}), *(b.boxes or {})]:
                if not 0 <= i < n:
                    raise ValueError(f"body: pose {i} fora de 0..{n - 1}")
            set_checks += check_body_coherence(
                imgs, b.box, body_colors=b.body_colors, wing_colors=b.wing_colors, compare=b.compare,
                eye_color=b.eye_color, boxes=b.boxes, max_mask_diff=b.max_mask_diff, head_poses=b.head_poses,
                head_offsets=b.head_offsets)
        set_ok = all(c.ok for c in set_checks)
        set_rep = Report.build(set_checks, spec=spec.name, kind="pose_set", file=f"{p.prefix}_*.png",
                               canvas=tuple(spec.canvas))
        # prévia: tira com rótulos (nome · classe) + onion skin de cada classe com 2+ poses
        cls = p.classes or ["*"] * n
        strip = animation_strip(imgs, p.preview_scale, pivot=piv,
                                labels=[f"{i} {labels[i]} · {cls[i]}" for i in range(n)])
        groups = pose_class_groups(n, p.classes)
        onions = [(k, onion_skin([imgs[i] for i in idx], p.preview_scale)) for k, idx in groups.items() if len(idx) > 1]
        W = max([strip.width] + [sum(o.width + 8 for _, o in onions) + 8])
        H = strip.height + (max((o.height for _, o in onions), default=0) + 22 if onions else 0)
        prev = Image.new("RGB", (W, H), (0x1a, 0x18, 0x30))
        prev.paste(strip, (0, 0))
        from PIL import ImageDraw, ImageFont

        dr = ImageDraw.Draw(prev)
        x = 8
        for k, o in onions:
            prev.paste(o, (x, strip.height + 14))
            dr.text((x, strip.height + 2), f"onion: {k}", fill=(0xcd, 0xc9, 0xdf), font=ImageFont.load_default())
            x += o.width + 8
        prev.save(d / "preview.png", format="PNG")
        files.append(OutputFile(d / "preview.png", "preview", "image/png"))
        ok = set_ok and all(r.ok for r in reports.values())
        rep_json = {"ok": ok, "pose_set": set_rep.model_dump(mode="json"),
                    "frames": {k: r.model_dump(mode="json") for k, r in reports.items()}}
        (d / "report.json").write_text(json.dumps(rep_json, ensure_ascii=False, indent=1), encoding="utf-8")
        md = ["# Relatório de validação — poses (Asset Forge Pixel)", "",
              f"Classes: {json.dumps(groups, ensure_ascii=False)}", "", set_rep.to_markdown(),
              *[r.to_markdown() for r in reports.values()]]
        (d / "report.md").write_text("\n".join(md), encoding="utf-8")
        files += [OutputFile(d / "report.json", "report", "application/json"),
                  OutputFile(d / "report.md", "report", "text/markdown")]
        ctx.meta["kind"] = "spritesheet"
        ex = ctx.meta.setdefault("extra", {})
        ex["validation"] = {**set_rep.summary(), "ok_all": ok,
                            "frames_failed": {k: r.summary()["failed"] for k, r in reports.items() if not r.ok},
                            "pose_set": {c.id: c.ok for c in set_checks}}
        ex["spec"] = spec.model_dump(mode="json")
        ex["pixel"] = {"palette_group": group, "prefix": p.prefix, "files": names, "classes": p.classes,
                       "labels": p.labels, "mode": p.mode, "tags": tags, "quantized": changes, "cyclic": p.cyclic,
                       "body": p.body.model_dump(mode="json") if p.body else None,
                       "palette": "fallback" if palette.fallback else "project"}
        ex["sheet"] = {k: meta[k] for k in ("frame_size", "count", "columns", "rows", "width", "height", "pivot")}
        ctx.log("poses: " + ("validadores OK" if ok else "validadores REPROVARAM: "
                             + ", ".join(c.id for c in set_checks if not c.ok)
                             + (("; quadros: " + ", ".join(k for k, r in reports.items() if not r.ok))
                                if any(not r.ok for r in reports.values()) else "")))
        return files


# ---------------------------------------------------------------- pixel.slice

class SliceParams(PipelineParams):
    slice: dict[str, Any] = Field(..., description='JSON de fatiamento {"format": "asset-forge-slice/1", "source_size", '
                                                   '"background", "parts": [{"name", "file_name", "spec", "palette_group", '
                                                   '"rect", "polygon", "erase", "target", "mask", "clear", "colors"}]} '
                                                   '(ver forge_pixel.slice)')
    preview_scale: int = Field(10, ge=1, le=16, description="ampliação da prévia das peças")


class SliceParts(Pipeline):
    """Grava, por versão:
        <file_name de cada peça>   part    — cada peça no canvas exato da sua spec (16 PPU)
        sprite.png                 primary — a primeira peça (a que o asset representa)
        source.<ext>               source  — a imagem de entrada sem alteração (ex.: a geração de IA bruta)
        slice.json                 source  — o JSON de fatiamento usado
        overlay.png                preview — a fonte sem fundo com retângulos, polígonos, áreas apagadas e grade
        preview.png                preview — as peças ampliadas
        report.json / .md          report  — validadores de cada peça
    `mask` das peças aceita arquivo ou asset (slug@vNNN; usa o PNG primary)."""

    id = "pixel.slice"
    title = "Fatiar imagem em peças"
    description = ("Recorta peças de uma imagem inteira (ex.: o personagem todo gerado por IA) a partir de um JSON de "
                   "fatiamento (retângulo/polígono na fonte, caixa no canvas, máscara de silhueta, áreas apagadas) e "
                   "encaixa cada uma na spec: célula pela moda das cores já quantizadas na paleta travada, recorte à "
                   "área segura, contorno de 1 px e validadores. Guarda a imagem de entrada como fonte. Determinístico.")
    resources = {"cpu"}
    kind = "sprite"
    Params = SliceParams
    requires_input = True

    def default_slug(self, params: SliceParams) -> str:  # type: ignore[override]
        parts = params.slice.get("parts") or [{}]
        fn = parts[0].get("file_name")
        return Path(fn).stem if fn else "pixel slice"

    def run(self, ctx: Context, p: SliceParams) -> list[OutputFile]:
        import shutil

        from forge_core.runner import resolve_input

        from .slice import SliceSpec, overlay, slice_image

        files_in = ctx.input_files("primary") or ctx.input_files()
        imgs = [f for f in files_in if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")]
        if not imgs:
            raise ValueError("pixel.slice precisa de um input_asset de imagem (a imagem inteira a fatiar)")
        sl = SliceSpec.model_validate(p.slice)
        specs = load_specs(ctx.project)
        palette = load_palette(ctx.project)
        masks: dict[str, Image.Image] = {}
        for part in sl.parts:
            if part.mask and part.mask not in masks:
                av, path = resolve_input(ctx.project, part.mask)
                if av is not None:
                    fs = ctx.project.store.files_of(av, "primary") or ctx.project.store.files_of(av)
                    path = next(f for f in fs if f.suffix.lower() == ".png")
                with Image.open(path) as im:
                    masks[part.mask] = im.convert("RGBA")
        with Image.open(imgs[0]) as src:
            src.load()
            ctx.progress(0.2, "fatiando")
            results = slice_image(src, sl, palette, specs, masks)
            ov = overlay(src, sl, 1)
        d = ctx.version_dir
        out: list[OutputFile] = []
        src_copy = d / ("source" + imgs[0].suffix.lower())
        shutil.copyfile(imgs[0], src_copy)
        (d / "slice.json").write_text(sl.model_dump_json(indent=1), encoding="utf-8")
        reports: dict[str, Report] = {}
        ctx.progress(0.6, "validando")
        for i, r in enumerate(results):
            name = r.part.file_name or f"{r.part.name}.png"
            save_png(r.image, d / name)
            out.append(OutputFile(d / name, "part", "image/png"))
            if i == 0:
                save_png(r.image, d / "sprite.png")
                out.append(OutputFile(d / "sprite.png", "primary", "image/png"))
            reports[name] = validate(r.image, r.spec, palette, name=r.part.file_name, side=r.part.side,
                                     palette_group=r.group, material=r.part.material, tags=r.part.tags)
        out += [OutputFile(src_copy, "source", "image/png" if src_copy.suffix == ".png" else "image/jpeg"),
                OutputFile(d / "slice.json", "source", "application/json")]
        ov.save(d / "overlay.png")
        out.append(OutputFile(d / "overlay.png", "preview", "image/png"))
        tiles = [zoom_preview(r.image, r.spec, scale=p.preview_scale) for r in results]
        prev = Image.new("RGB", (sum(t.width + 8 for t in tiles), max(t.height for t in tiles)), (0x1a, 0x18, 0x30))
        x = 0
        for t in tiles:
            prev.paste(t, (x, 0))
            x += t.width + 8
        prev.save(d / "preview.png", format="PNG")
        out.append(OutputFile(d / "preview.png", "preview", "image/png"))
        ok = all(r.ok for r in reports.values())
        rep_json = {"ok": ok, **{k: r.model_dump(mode="json") for k, r in reports.items()}}
        (d / "report.json").write_text(json.dumps(rep_json, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "report.md").write_text("# Relatório de validação — fatiador (Asset Forge Pixel)\n\n"
                                     + "\n".join(r.to_markdown() for r in reports.values()), encoding="utf-8")
        out += [OutputFile(d / "report.json", "report", "application/json"),
                OutputFile(d / "report.md", "report", "text/markdown")]
        first = results[0]
        first_name = first.part.file_name or f"{first.part.name}.png"
        ctx.meta["kind"] = first.spec.kind
        ex = ctx.meta.setdefault("extra", {})
        ex["validation"] = reports[first_name].summary()
        ex["validation"]["ok_all"] = ok
        ex["spec"] = first.spec.model_dump(mode="json")
        ex["pixel"] = {"side": first.part.side, "palette_group": first.group, "file_name": first.part.file_name,
                       "material": first.part.material, "tags": first.part.tags,
                       "palette": "fallback" if palette.fallback else "project", "source": imgs[0].name,
                       "parts": [r.part.file_name or r.part.name for r in results]}
        for name, r in reports.items():
            ctx.log(f"{name}: " + ("validadores OK" if r.ok else "REPROVOU: " + ", ".join(c.id for c in r.failed if c.elim)))
        return out


# ---------------------------------------------------------------- pixel.parallax

class ParallaxParams(PipelineParams):
    map: dict[str, Any] | None = Field(None, description="pixel map da camada (outline.auto=false); vazio = o PNG do input_asset")
    spec: str | dict[str, Any] | None = Field(None, description='"parallax" (opaca) ou "parallax_open" (transparência '
                                                                "binária); vazio = deduzida do nome (bg_*_far = opaca)")
    borrow_groups: list[str] = Field(default_factory=list, description="grupos da paleta travada cujas cores a camada "
                                                                       "pode usar além do núcleo (não há grupo de fundo)")
    palette_group: str | None = Field(None, description="nome do grupo proposto (ex.: fundo/castelo); vazio = do nome")
    file_name: str | None = Field(None, description="nome final no jogo, ex.: bg_castelo_far_v01.png")
    tags: list[str] | None = Field(None, description="tags de cor (castle libera carmim; light/fire liberam ouro)")
    solid_assets: list[str] = Field(default_factory=list, description="tiles sólidos do plano de jogo (refs) para o "
                                                                      "check_parallax_stack")
    stack_assets: list[str] = Field(default_factory=list, description="camadas MAIS DISTANTES da mesma pilha (refs, da "
                                                                      "mais distante para a mais próxima)")
    wall_assets: list[str] = Field(default_factory=list, description="paredes de fundo (refs), só informativo")


class ParallaxLayer(Pipeline):
    """Grava, por versão:
        sprite.png         primary — a camada 640x360 (não ampliada; 16 PPU)
        pixelmap.json      source  — o mapa editável (quando veio de `map`)
        preview.png        preview — a camada repetida 2x na horizontal (marcas na emenda) e, com
                                     stack_assets, a pilha composta até esta camada
        report.json / .md  report  — validadores da spec + check_tileable_x + check_parallax_stack
    """

    id = "pixel.parallax"
    title = "Camada de parallax"
    description = ("Publica uma camada de fundo de parallax (640x360, repete em x) desenhada à mão ou proceduralmente: "
                   "paleta travada (núcleo + grupos emprestados), alfa binário, emenda horizontal e a pilha contra os "
                   "tiles sólidos (cada camada mais escura e menos contrastada que o plano de jogo; distante < próxima).")
    resources = {"cpu"}
    kind = "sprite"
    Params = ParallaxParams
    accepts_input = True

    def default_slug(self, params: ParallaxParams) -> str:  # type: ignore[override]
        return Path(params.file_name).stem if params.file_name else "parallax layer"

    def _png(self, ctx: Context, ref: str) -> Image.Image:
        from .api import _resolve_file

        _, path = _resolve_file(ctx.project, ref)
        with Image.open(path) as im:
            return im.convert("RGBA")

    def run(self, ctx: Context, p: ParallaxParams) -> list[OutputFile]:
        import re

        from .parallax import composite, seam_marks, stack_vertical, tile_x
        from .spec import spec_name_for_file
        from .validators import check_parallax_stack, check_tileable_x

        pm = None
        if p.map is not None:
            pm = pmod.parse(p.map)
            img = pmod.render(pm, outline=False)
        else:
            prim = ctx.input_files("primary") or ctx.input_files()
            pngs = [f for f in prim if f.suffix.lower() == ".png"]
            if not pngs:
                raise ValueError("pixel.parallax precisa de `map` ou de um input_asset com PNG")
            with Image.open(pngs[0]) as im:
                img = im.convert("RGBA")
            pm = pmod.from_image(img)
        spec_ref = p.spec or (spec_name_for_file(p.file_name) if p.file_name else None) or "parallax"
        spec, palette = _resolve(ctx, spec_ref, None)
        m = re.match(r"^bg_([a-z0-9]+)_", p.file_name or "")
        group = p.palette_group or ("fundo/" + m.group(1) if m else "fundo")
        if not palette.fallback:
            missing = [g for g in p.borrow_groups if g not in palette.groups]
            if missing:
                raise ValueError(f"grupos de paleta desconhecidos: {missing}")
            cols = sorted({c for g in p.borrow_groups for c in palette.groups[g]})
            palette = palette.model_copy(update={"groups": {**palette.groups, group: cols}})
        ctx.progress(0.4, "validando")
        report = validate(img, spec, palette, name=p.file_name, palette_group=group if not palette.fallback else None,
                          tags=list(p.tags or []))
        checks = list(report.checks)
        checks.append(check_tileable_x(img, spec.tile_tolerance))
        stack = [self._png(ctx, r) for r in p.stack_assets]
        if p.solid_assets:
            solids = [self._png(ctx, r) for r in p.solid_assets]
            walls = [self._png(ctx, r) for r in p.wall_assets] or None
            names = [f"camada {i}" for i in range(len(stack))] + [Path(p.file_name or "esta").stem]
            checks.append(check_parallax_stack(dict(zip(names, [*stack, img])), solids, walls))
        report = Report.build(checks, spec=spec.name, kind=spec.kind, file=p.file_name, canvas=img.size)
        d = ctx.version_dir
        files: list[OutputFile] = []
        save_png(img, d / "sprite.png")
        files.append(OutputFile(d / "sprite.png", "primary", "image/png"))
        (d / "pixelmap.json").write_text(pm.to_json(), encoding="utf-8")
        files.append(OutputFile(d / "pixelmap.json", "source", "application/json"))
        prev = [seam_marks(composite([tile_x(img, 2)], background="#0b0a12"), [img.width])]
        if stack:
            prev.append(composite([*stack, img]))
        stack_vertical(prev).convert("RGB").save(d / "preview.png", format="PNG")
        files.append(OutputFile(d / "preview.png", "preview", "image/png"))
        (d / "report.json").write_text(json.dumps({"ok": report.ok, "primary": report.model_dump(mode="json")},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "report.md").write_text("# Relatório de validação — parallax (Asset Forge Pixel)\n\n" + report.to_markdown(),
                                     encoding="utf-8")
        files += [OutputFile(d / "report.json", "report", "application/json"),
                  OutputFile(d / "report.md", "report", "text/markdown")]
        ctx.meta["kind"] = spec.kind
        ex = ctx.meta.setdefault("extra", {})
        ex["validation"] = report.summary()
        st = report.get("parallax_stack")
        if st is not None:
            ex["parallax_stack"] = {k: st.data[k] for k in ("layers", "solid_limit", "margins") if k in st.data}
        ex["spec"] = spec.model_dump(mode="json")
        ex["pixel"] = {"palette_group": group, "borrow_groups": p.borrow_groups, "file_name": p.file_name,
                       "tags": list(p.tags or []), "palette": "fallback" if palette.fallback else "project"}
        ctx.log("validadores OK" if report.ok else "validadores REPROVARAM: "
                + ", ".join(c.id for c in report.failed if c.elim))
        return files


PIPELINES: list[Pipeline] = [PixelMapToSprite(), FitToSpec(), AutotileTiles(), PoseSet(), SliceParts(),
                             ParallaxLayer()]

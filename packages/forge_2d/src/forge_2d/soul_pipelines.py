"""Gerar com Creator Soul: RAG de imagens — busca as referências mais parecidas com o prompt na(s) Soul(s) do
projeto e as usa como guia de estilo no Qwen-Image-Edit; depois pixeliza com o tamanho/paleta/contorno da Soul."""
from __future__ import annotations

import re
import statistics
from typing import Any, Literal

from PIL import Image
from pydantic import Field

from forge_core import OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context

from . import graphs, prompts
from .pipelines import _ensure_comfy, _finish, _models, _palette, _pixel_cfg, _resolve_image, _run, _seed, _upload

# tipo de asset → categorias favorecidas na busca (prefixos; ignorados se a Soul não tiver)
TYPE_CATEGORIES: dict[str, list[str]] = {
    "character": ["npc", "townnpcs", "player", "armor"], "creature": ["npc"], "object": ["item"], "icon": ["item", "buff"],
    "tile": ["tiles", "tile", "wall"], "ui": ["ui"], "background": ["background"], "effect": ["projectile"],
}


def soul_refs(ctx: Context, query: str, k: int = 2, asset_type: str | None = None) -> list[dict[str, Any]]:
    """Referências das Souls ativas do projeto para o texto (com a Soul de origem)."""
    from forge_core.souls import soul_context

    out: list[dict[str, Any]] = []
    for soul, strength in soul_context(ctx.project, "2d"):
        cats = TYPE_CATEGORIES.get(asset_type or "", None)
        res = []
        if cats and any(c.lower().startswith(tuple(cats)) for c in soul.manifest.categories):
            res = soul.retrieve(query, k, categories=cats)
        if len(res) < k:
            res += [r for r in soul.retrieve(query, k) if r["id"] not in {x["id"] for x in res}][: k - len(res)]
        if res and len(res) < k:  # âncoras de estilo: fixadas, depois outras da mesma categoria da 1ª ref
            have = {x["id"] for x in res}
            ents = soul.entries()
            pinned = [e for e in ents if e["id"] in soul.manifest.pinned and e["id"] not in have]
            same = [e for e in ents if e["category"] == res[0]["category"] and e["id"] not in have and e.get("name")]
            res += [{**e, "score": 0.0, "anchor": True} for e in (pinned + same)[: k - len(res)]]
        out += [{**r, "soul": soul, "strength": strength} for r in res]
    return out[:k]


def ref_canvas(img: Image.Image, side: int = 512, bg=(200, 200, 200)) -> Image.Image:
    """Referência legível para o modelo: ampliada em nearest (pixel art) sobre fundo neutro."""
    img = img.convert("RGBA")
    k = max(1, side // max(img.size))
    img = img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (max(img.width, side // 2) + 32, max(img.height, side // 2) + 32), bg + (255,))
    canvas.alpha_composite(img, ((canvas.width - img.width) // 2, (canvas.height - img.height) // 2))
    return canvas.convert("RGB")


def ref_orientation(img: Image.Image) -> str | None:
    """Orientação de sprites alongados pelo eixo principal do alpha: 'diagonal ↗', 'vertical', 'horizontal' ou None."""
    import math

    import numpy as np

    a = np.asarray(img.convert("RGBA"))[..., 3] > 8
    ys, xs = np.nonzero(a)
    if len(xs) < 12:
        return None
    pts = np.stack([xs - xs.mean(), -(ys - ys.mean())])  # y para cima
    w, v = np.linalg.eigh(np.cov(pts))
    if w[1] < 2.2 * max(w[0], 1e-6):  # pouco alongado: sem orientação definida
        return None
    ang = math.degrees(math.atan2(v[1, 1], v[0, 1])) % 180
    if 25 <= ang <= 65:
        return "diagonal, pointing to the upper right at 45 degrees (handle at bottom-left)"
    if 115 <= ang <= 155:
        return "diagonal, pointing to the upper left at 45 degrees"
    if 65 < ang < 115:
        return "vertical"
    return "horizontal"


def common_orientation(imgs: list[Image.Image]) -> str | None:
    found = [o for o in (ref_orientation(i) for i in imgs) if o]
    if found and found.count(found[0]) == len(found):
        return found[0]
    return None


RESTYLE = ("Redraw the subject of image 1 as a single game sprite in the exact art style of the reference sprites "
           "(image 2{and3}): same pixel size and resolution, same outline color and thickness, same shading technique, "
           "same color palette and saturation, same viewing angle and proportions{orient}. Keep the subject and "
           "main colors of image 1. One centered object on a plain light gray background, no text.")
DERIVE = ("Transform the sprite in image 1 into: {prompt}. Keep exactly the same art style as image 1{and2}: same "
          "pixel size, outline, shading, palette and viewing angle. One centered object on a plain light gray "
          "background, no text.")


# ---------------------------------------------------------------- estratégia "lora" (formato de treino)
# Formato das LoRAs do Pixel-LLM-Gen: sprite em resolução de arte num canvas de `cells` células de `cell_px` px,
# ampliado nearest para 1024, fundo magenta; legenda "trrsprite, <tipo>, <descrição>, <tamanho> sprite".

CHROMA = (255, 0, 255)


def size_tag(art: int) -> str:
    return "tiny" if art <= 8 else "small" if art <= 14 else "medium" if art <= 20 else "large"


def lora_kind(lr: dict[str, Any]) -> str:
    return lr.get("name", "item")


_PT_STOP = {"de", "com", "da", "do", "das", "dos", "um", "uma", "em", "no", "na", "nos", "nas", "para", "que", "os", "as"}
_PT_ADJ = {"azul", "azuis", "vermelho", "vermelha", "verde", "verdes", "amarelo", "amarela", "roxo", "roxa", "rosa",
           "laranja", "branco", "branca", "preto", "preta", "cinza", "marrom", "dourado", "dourada", "prateado",
           "prateada", "venenoso", "venenosa", "flamejante", "congelado", "brilhante", "magico", "sombrio", "sagrado",
           "gigante", "grande", "pequeno", "eletrico", "eletrica", "encantado"}


def _dict_translate(text: str) -> tuple[str, int]:
    """Tradução por dicionário (só palavras portuguesas) com a ordem do inglês: "espada de fogo" → "fire sword",
    "cristal azul" → "blue crystal". Devolve (texto, nº de palavras portuguesas desconhecidas)."""
    from forge_core.souls.index import PT_WORDS, SYNONYMS, norm

    words = re.findall(r"[a-z0-9]+", norm(text))
    for i in range(len(words) - 1):  # adjetivo depois do substantivo → antes
        if words[i + 1] in _PT_ADJ and words[i] not in _PT_STOP and words[i] not in _PT_ADJ:
            words[i], words[i + 1] = words[i + 1], words[i]
    i = 0
    while i < len(words) - 2:  # "X de Y" → "Y X"
        if words[i + 1] == "de" and words[i] not in _PT_STOP and words[i + 2] not in _PT_STOP:
            words[i], words[i + 1], words[i + 2] = words[i + 2], words[i], ""
            i += 3
        else:
            i += 1
    out, unknown = [], 0
    for w in words:
        if not w or w in _PT_STOP:
            continue
        key = w if w in PT_WORDS else (w[:-1] if w.endswith("s") and w[:-1] in PT_WORDS else None)
        if key:
            out.append(SYNONYMS[key][0])
        else:
            out.append(w)
            unknown += int(not w.isascii() or bool(re.search(r"(cao|coes|ao|inho|inha)$", w)))
    return " ".join(out), unknown


def english(ctx: Context, text: str) -> str:
    """Prompt em português → descrição curta em inglês. Dicionário primeiro; LLM local só se sobrar palavra
    desconhecida, e a saída só é aceita se vier em inglês e mantiver os substantivos traduzidos."""
    from forge_core.souls.index import norm

    from forge_core.souls.index import PT_WORDS

    toks = set(re.findall(r"[a-z]+", norm(text)))
    looks_pt = bool(re.search(r"[ãõçáéíóúâêô]", text.lower())) or bool(toks & _PT_STOP) or bool(toks & PT_WORDS)
    if not looks_pt:
        return text
    tr, unknown = _dict_translate(text)
    if unknown == 0:
        return tr
    try:
        from forge_core import local_llm

        out = local_llm.assist(f"Translate to English, literally, keep every object word: {text}", target="tags")
        out = out.strip().strip(".")
        keep = [w for w in tr.split() if len(w) > 3 and w.isascii()]
        if re.fullmatch(r"[\x00-\x7f]+", out) and all(w.lower() in out.lower() for w in keep[:3]):
            return re.sub(r"\b(vertical|horizontal)\s+(orientation|position)\b,?", "", out).strip(" ,")
    except Exception:
        pass
    return tr


def guide_canvas(shape: Image.Image, color: tuple[int, int, int], cells: int, cell_px: int) -> Image.Image:
    """Silhueta (resolução de arte) na cor média das refs, centralizada no canvas magenta, ampliada nearest."""
    small = Image.new("RGB", (cells, cells), CHROMA)
    sh = shape.convert("RGBA")
    if sh.getbbox():
        sh = sh.crop(sh.getbbox())
    if sh.width > cells - 2 or sh.height > cells - 2:
        sh.thumbnail((cells - 2, cells - 2), Image.Resampling.NEAREST)
    mask = sh.getchannel("A").point(lambda a: 255 if a > 8 else 0)
    small.paste(Image.new("RGB", sh.size, color), ((cells - sh.width) // 2, (cells - sh.height) // 2), mask)
    return small.resize((cells * cell_px, cells * cell_px), Image.Resampling.NEAREST)


def _components(mask) -> list[list[tuple[int, int]]]:
    """Componentes 8-conectados de uma máscara booleana pequena (resolução de arte)."""
    h, w = mask.shape
    seen = [[False] * w for _ in range(h)]
    comps = []
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or seen[y0][x0]:
                continue
            comp, stack = [], [(y0, x0)]
            seen[y0][x0] = True
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy][xx]:
                            seen[yy][xx] = True
                            stack.append((yy, xx))
            comps.append(comp)
    return comps


def chroma_pixelize(raw: Image.Image, cell_px: int, colors: int | None = None, min_island: float = 0.04) -> Image.Image:
    """Canvas treinado → sprite em resolução de arte com alpha binário.
    1 cor por célula (mediana do miolo); fundo = mediana das células da borda (a LoRA nem sempre pinta magenta puro):
    flood-fill a partir da borda (tolerância 110) + qualquer célula muito parecida com o fundo (48); despill de células
    opacas perto do magenta; remove ilhas soltas menores que `min_island` do sprite."""
    import numpy as np

    a = np.asarray(raw.convert("RGB"), np.float32)
    n = a.shape[0] // cell_px
    a = a[: n * cell_px, : n * cell_px].reshape(n, cell_px, n, cell_px, 3)
    q0, q1 = cell_px // 4, cell_px - cell_px // 4
    art = np.median(a[:, q0:q1, :, q0:q1].transpose(0, 2, 1, 3, 4).reshape(n, n, -1, 3), axis=2)
    border = np.concatenate([art[0], art[-1], art[:, 0], art[:, -1]])
    bgc = np.median(border, axis=0)
    dist = np.sqrt(((art - bgc) ** 2).sum(-1))
    bg = dist < 48
    loose = dist < 110
    seen = np.zeros_like(loose)
    stack = [(y, x) for y in range(n) for x in (0, n - 1)] + [(y, x) for x in range(n) for y in (0, n - 1)]
    while stack:
        y, x = stack.pop()
        if seen[y, x] or not loose[y, x]:
            continue
        seen[y, x] = True
        bg[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            yy, xx = y + dy, x + dx
            if 0 <= yy < n and 0 <= xx < n:
                stack.append((yy, xx))
    dm = np.sqrt(((art - np.array(CHROMA, np.float32)) ** 2).sum(-1))
    spill = (~bg) & ((dm < 100) | (dist < 60))
    for y, x in zip(*np.nonzero(spill)):  # troca pela mediana dos vizinhos limpos
        nb = [art[yy, xx] for yy in range(max(0, y - 1), min(n, y + 2)) for xx in range(max(0, x - 1), min(n, x + 2))
              if not bg[yy, xx] and not spill[yy, xx]]
        if nb:
            art[y, x] = np.median(nb, axis=0)
        else:
            bg[y, x] = True
    comps = _components(~bg)
    if comps:
        biggest = max(len(c) for c in comps)
        for c in comps:
            if len(c) < max(3, min_island * biggest):
                for y, x in c:
                    bg[y, x] = True
    rgba = np.dstack([np.clip(art, 0, 255).astype(np.uint8), np.where(bg, 0, 255).astype(np.uint8)])
    img = Image.fromarray(rgba, "RGBA")
    if colors:
        rgb = img.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
        rgb.putalpha(img.getchannel("A"))
        img = rgb
    box = img.getbbox()
    return img.crop(box) if box else img


class SoulParams(PipelineParams):
    prompt: str = Field(..., description="o que criar; a Soul do projeto fornece estilo e referências")
    asset_type: prompts.AssetType = Field("object", description="ajuda a escolher referências (character→NPCs, effect→projéteis...)")
    strategy: Literal["auto", "lora", "restyle", "derive"] = Field(
        "auto", description="auto: LoRA da Soul se houver uma para a categoria, senão restyle; lora: modelo treinado "
                            "no estilo; restyle: redesenha no estilo das refs; derive: transforma a ref mais parecida")
    references: list[str] = Field(default_factory=list, description="refs escolhidas (soul:<id>/<ref> ou assets); vazio = busca automática")
    query: str = Field("", description="texto da busca de refs (vazio = o prompt)")
    pixelize: bool | None = Field(None, description="vazio = sim se a Soul é pixel art")
    pixel_size: int | None = Field(None, ge=8, le=256, description="maior lado em pixels de arte (vazio = tamanho das refs)")
    texture_scale: int | None = Field(None, ge=1, le=8, description="texels por pixel de arte no PNG final (vazio = escala da Soul)")
    lora_strength: float | None = Field(None, ge=0, le=2, description="estratégia lora: força da LoRA (vazio = da Soul)")
    guide: bool | None = Field(None, description="estratégia lora: partir da silhueta da ref mais parecida (vazio = padrão da LoRA)")
    guide_denoise: float = Field(0.85, ge=0.3, le=1.0)
    npc_kind: Literal["enemy", "town npc"] = "enemy"
    transparent: bool = True
    seed: int | None = None
    steps: int = Field(4, ge=1, le=50, description="restyle/derive (Qwen); a LoRA usa os passos da Soul")


class SoulGenerate(Pipeline):
    id = "2d.soul"
    title = "Gerar com Soul"
    description = ("Gera um asset no estilo da Creator Soul anexada ao projeto. Com LoRA treinada na Soul usa o modelo "
                   "no formato do treino; senão busca as referências mais parecidas (RAG de imagens) e redesenha no "
                   "estilo delas. Pixeliza com tamanho, escala e contorno da coleção.")
    resources = {"gpu"}
    kind = "image"
    Params = SoulParams

    def run(self, ctx: Context, p: SoulParams) -> list[OutputFile]:
        from forge_core.souls import soul_context
        from forge_core.souls.loras import comfy_name, lora_for

        souls = soul_context(ctx.project, "2d")
        if not souls and not p.references:
            raise ValueError("nenhuma Creator Soul anexada ao projeto (aba Estilo) e nenhuma referência informada")
        style = ctx.style()
        models = _models(style)
        k = 4 if not p.references else 2
        if p.references:
            refs = [{"ref": r, "img": _resolve_image(ctx, r), "name": r} for r in p.references[:2]]
        else:
            found = soul_refs(ctx, p.query or p.prompt, k, p.asset_type)
            if not found and p.strategy in ("restyle", "derive"):
                raise ValueError("nenhuma referência parecida na Soul — descreva com outras palavras ou escolha refs")
            refs = [{"ref": f"soul:{r['soul'].id}/{r['id']}", "img": Image.open(r["soul"].ref_path(r["id"])),
                     "name": r.get("name") or r["stem"], "entry": r} for r in found]
        ctx.log("referências: " + (", ".join(r["name"] for r in refs) or "nenhuma"))
        soul = souls[0][0] if souls else None
        sm = soul.manifest if soul else None
        lora = None
        if soul and p.strategy in ("auto", "lora"):
            # tipo pedido explicitamente vence; senão a categoria da referência mais parecida
            cat = {"character": "NPC", "creature": "NPC", "effect": "Projectile"}.get(p.asset_type) or                 next((r["entry"]["category"] for r in refs if r.get("entry")), None) or "Item"
            lora = lora_for(soul, cat)
            if p.strategy == "lora" and lora is None:
                raise ValueError(f"a Soul não tem LoRA para a categoria {cat} (tem: {[x['name'] for x in sm.loras]})")
        strategy = "lora" if lora else ("restyle" if p.strategy == "auto" else p.strategy)
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        orient = common_orientation([r["img"] for r in refs[:2]])
        if orient:
            ctx.log(f"orientação das referências: {orient}")

        # tamanho alvo em pixels de arte (mediana das refs), usado por todas as estratégias
        sizes = [max(r["entry"].get("fw") or 0, r["entry"].get("fh") or 0) / max(1, sm.pixel.scale)
                 for r in refs[:2] if r.get("entry") and sm]
        art = p.pixel_size or (int(statistics.median(sizes)) if sizes else (sm.pixel.art_size if sm else 32))
        scale = p.texture_scale or (sm.pixel.scale if sm else 1)

        if strategy == "lora":
            return self._lora(ctx, p, soul, lora, refs, art, scale, seed, comfy_name(soul, lora["file"]), style, orient)
        if not refs:
            raise ValueError("nenhuma referência parecida na Soul — descreva com outras palavras ou escolha refs")

        ref_imgs = [ref_canvas(r["img"]) for r in refs[:2]]
        pixel_on = p.pixelize if p.pixelize is not None else bool(sm and sm.pixel.is_pixel_art)
        if strategy == "restyle":
            content = f"{p.prompt}, {orient}" if orient else p.prompt
            full = prompts.compose(content, style, p.asset_type, False, True)
            g, img_id, _ = graphs.text2img_zimage(full, 1024, 1024, seed, 8, models=models)
            draft = _run(ctx, g, [img_id], span=(0.05, 0.4), label="rascunho do conteúdo")[img_id]
            names = [_upload(ctx, draft.convert("RGB"), "draft.png")]
            instruction = RESTYLE.replace("{and3}", " and image 3" if len(ref_imgs) > 1 else "").replace(
                "{orient}", f", oriented {orient} like the references" if orient else "")
        else:
            names = []
            full = p.prompt
            instruction = DERIVE.replace("{prompt}", p.prompt).replace("{and2}", " and image 2" if len(ref_imgs) > 1 else "")
        names += [_upload(ctx, im, f"ref{i}.png") for i, im in enumerate(ref_imgs)]
        g, img_id, mask_id = graphs.qwen_edit(instruction, names[:3], seed, p.steps, models=models, remove_bg=p.transparent)
        out = _run(ctx, g, [img_id, mask_id], span=(0.45 if strategy == "restyle" else 0.05, 0.9),
                   label="redesenhando no estilo da Soul")
        pixel = None
        if pixel_on:
            pixel = _pixel_cfg(style, size=max(8, min(256, art)), scale=scale,
                               outline=sm.pixel.outline if sm and sm.pixel.outline else None)
        ctx.meta.update(seed=seed, prompt=p.prompt, extra={
            "strategy": strategy, "full_prompt": full, "instruction": instruction, "model": "qwen-image-edit-2511",
            "soul": soul.id if soul else None, "orientation": orient,
            "references": [{"ref": r["ref"], "name": r["name"]} for r in refs[:2]]})
        return _finish(ctx, out[img_id], mask=out.get(mask_id) if mask_id else None, pixel=pixel, palette=_palette(style))

    def _lora(self, ctx: Context, p: SoulParams, soul, lr: dict[str, Any], refs: list[dict[str, Any]], art: int,
              scale: int, seed: int, lora_name: str, style: dict[str, Any], orient: str | None = None) -> list[OutputFile]:
        import numpy as np

        from .pipelines import _models

        cell = int(lr.get("cell_px", 32))
        cells = 1024 // cell
        kind = lora_kind(lr)
        art = max(4, min(art, cells - 2))
        desc = english(ctx, p.prompt)
        ctx.log(f"descrição: {desc}")
        prompt = lr["template"].replace("{prompt}", "{X}")
        extra = []
        if kind == "npc":
            prompt = prompt.replace("npc, {X}", f"npc, {p.npc_kind}, {{X}}") if f"npc, {p.npc_kind}" not in prompt else prompt
        if orient and orient.startswith("diagonal") and kind == "item":
            extra.append("diagonal, pointing up-right")
        if kind == "projectile":
            extra.append("single frame")
        extra.append(f"{size_tag(art)} sprite")
        full = prompt.replace("{X}", ", ".join([desc, *extra]))
        negative = lr.get("negative") or "blurry, smooth, gradient, photo, realistic, 3d, text, watermark, multiple objects"
        use_guide = p.guide if p.guide is not None else bool(lr.get("guide"))
        init = None
        if use_guide and refs and refs[0].get("entry"):
            shape = refs[0]["img"].convert("RGBA")  # miniatura: ampliada nearest → volta à resolução de arte
            e = refs[0]["entry"]
            tw, th = max(1, round((e.get("fw") or shape.width) / max(1, soul.manifest.pixel.scale))), \
                max(1, round((e.get("fh") or shape.height) / max(1, soul.manifest.pixel.scale)))
            shape = shape.resize((tw, th), Image.Resampling.NEAREST)
            px = np.concatenate([np.asarray(r["img"].convert("RGBA")).reshape(-1, 4) for r in refs])
            px = px[px[:, 3] > 128][:, :3]
            color = tuple(int(c) for c in px.mean(0)) if len(px) else (200, 200, 200)
            guide = guide_canvas(shape, color, cells, cell)
            init = _upload(ctx, guide, "guide.png")
            ctx.log(f"guia de silhueta: {refs[0]['name']} ({tw}x{th} px de arte)")
        ctx.log(f"LoRA {lr['file']} | {full}")
        g, img_id, _ = graphs.text2img_checkpoint(
            full, negative, 1024, 1024, seed, checkpoint=lr.get("base", "sd_xl_base_1.0.safetensors"),
            steps=int(lr.get("steps", 30)), cfg=float(lr.get("cfg", 7.0)), sampler=lr.get("sampler", "dpmpp_2m"),
            scheduler=lr.get("scheduler", "karras"), loras=[{"name": lora_name, "strength": p.lora_strength or lr.get("strength", 1.0)}],
            models=_models(style), init_image=init, denoise=p.guide_denoise if init else 1.0)
        raw = _run(ctx, g, [img_id], span=(0.05, 0.9), label=f"gerando com a LoRA ({kind})")[img_id]
        sprite = chroma_pixelize(raw, cell)
        d = ctx.version_dir
        raw.save(d / "source.png")
        out = sprite.resize((sprite.width * scale, sprite.height * scale), Image.Resampling.NEAREST)
        out.save(d / "sprite.png")
        from . import imaging

        imaging.preview(sprite, 512).save(d / "preview.png")
        ctx.meta.update(seed=seed, prompt=p.prompt, kind="sprite", extra={
            "strategy": "lora", "full_prompt": full, "negative": negative, "lora": lr["file"], "lora_kind": kind,
            "guide": bool(init), "soul": soul.id, "pixel": {"art_size": list(sprite.size), "scale": scale, "cell_px": cell},
            "references": [{"ref": r["ref"], "name": r["name"]} for r in refs[:2]]})
        return [OutputFile(d / "sprite.png", "primary"), OutputFile(d / "preview.png", "preview"),
                OutputFile(d / "source.png", "source")]


SOUL_PIPELINES: list[Pipeline] = [SoulGenerate()]

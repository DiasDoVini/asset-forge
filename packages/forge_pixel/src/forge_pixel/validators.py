"""Validadores automáticos (aceite-assetforge.md §2-§3): passa/falha, sem olhar a imagem.

`validate(img, spec, palette)` roda todos os cheques que se aplicam à spec e devolve um `Report`.
Cheques marcados `elim` reprovam o asset (ele não vai para a rubrica visual).
Coordenadas nos detalhes seguem o `.meta` do Unity: (x, y) em px a partir do canto INFERIOR esquerdo.

Cheques: canvas, alpha, palette, outline, islands, safe_area, anchor, tileable, opaque, max_colors,
coverage, name, material_color, thickness, color_rules, shape (forma de bloco de 11 px: alfa = máscara
canônica), lr_pair. Para conjuntos de imagens:
check_pose_set (inimigos, por classe de pose), check_nine_slice (UI), check_cumulative (estágios de
rachadura, só o núcleo escuro), check_same_canvas; tiles (aceite §B): check_autotile (16 máscaras,
junções e mapa de teste; `autotile_report` devolve um Report), check_wall_contrast (parede x sólido) e
check_ore_on_base (minério sobre a pedra), check_shape_seams (forma x bloco cheio nas arestas de contato);
fundos: check_tileable_x (camada larga repete em x) e
check_parallax_stack (camadas distante -> próxima x tiles sólidos).

Exceção de coordenadas: os cheques do §7 (material_color, thickness, color_rules) devolvem em `data`
listas `*_px` com [x, y] em coordenadas de IMAGEM (canto superior esquerdo = [0, 0]), prontas para
destacar os pixels na prévia (`data["coords"] == "top-left"`).
"""
from __future__ import annotations

import re
from typing import Any, Literal, Sequence

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from .palette import darken, hex_to_rgb, js_round, rgb_to_hex
from .raster import border_pixels, bbox, components, neighbors4, to_rgba
from .spec import AssetSpec, PaletteSet, norm_hex, side_of_file

MAX_LISTED = 12


class Check(BaseModel):
    id: str
    title: str
    ok: bool
    elim: bool = True
    skipped: bool = False
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    ok: bool
    spec: str
    kind: str | None = None
    file: str | None = None
    side: str | None = None
    canvas: tuple[int, int] | None = None
    checks: list[Check] = Field(default_factory=list)

    @classmethod
    def build(cls, checks: list[Check], **kw: Any) -> "Report":
        ok = all(c.ok for c in checks if c.elim)
        return cls(ok=ok, checks=checks, **kw)

    def get(self, check_id: str) -> Check | None:
        return next((c for c in self.checks if c.id == check_id), None)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def summary(self) -> dict[str, Any]:
        """Resumo compacto para asset.json (extra.validation)."""
        return {
            "ok": self.ok,
            "spec": self.spec,
            "file": self.file,
            "side": self.side,
            "failed": [c.id for c in self.checks if not c.ok and c.elim],
            "warnings": [c.id for c in self.checks if not c.ok and not c.elim],
            "checks": {c.id: ("skip" if c.skipped else c.ok) for c in self.checks},
        }

    def to_markdown(self) -> str:
        head = f"## Validadores — `{self.file or 'sprite.png'}`" + (f" (lado {self.side})" if self.side else "")
        res = "**APROVADO nos validadores** (segue para a rubrica visual)" if self.ok else \
            "**REPROVADO** — falhou em item [elim]: " + ", ".join(f"`{c.id}`" for c in self.checks if c.elim and not c.ok)
        lines = [head, "", f"Spec: `{self.spec}` ({self.kind or '?'}"
                 + (f", canvas {self.canvas[0]}x{self.canvas[1]}" if self.canvas else "") + ")", "", f"Resultado: {res}", "",
                 "| Cheque | Tipo | Resultado | Detalhe |", "|---|---|---|---|"]
        for c in self.checks:
            mark = "—" if c.skipped else ("passa" if c.ok else "**falha**")
            kind = "[elim]" if c.elim else "aviso"
            detail = c.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {c.title} | {kind} | {mark} | {detail} |")
        pal = self.get("palette")
        if pal and not pal.ok and pal.data.get("off_palette"):
            lines += ["", "Cores fora da paleta (cor: pixels):", ""]
            lines += [f"- `{k}`: {v}" for k, v in pal.data["off_palette"].items()]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- helpers

def _bl(arr_h: int, ys: np.ndarray, xs: np.ndarray, limit: int = MAX_LISTED) -> list[tuple[int, int]]:
    """Coordenadas de imagem -> (x, y) a partir do canto inferior esquerdo (limitado)."""
    return [(int(x), int(arr_h - 1 - y)) for y, x in list(zip(ys, xs))[:limit]]


def _fmt_pts(pts: list[tuple[int, int]], total: int) -> str:
    s = ", ".join(f"({x},{y})" for x, y in pts)
    return s + (f" … (+{total - len(pts)})" if total > len(pts) else "")


def _colors(arr: np.ndarray) -> dict[str, int]:
    op = arr[..., 3] > 0
    if not op.any():
        return {}
    uniq, cnt = np.unique(arr[op, :3], axis=0, return_counts=True)
    return dict(sorted(((rgb_to_hex(c), int(n)) for c, n in zip(uniq, cnt)), key=lambda t: (-t[1], t[0])))


def _color_mask(arr: np.ndarray, color: str) -> np.ndarray:
    r, g, b = hex_to_rgb(color)
    return (arr[..., 3] > 0) & (arr[..., 0] == r) & (arr[..., 1] == g) & (arr[..., 2] == b)


def _skip(cid: str, title: str, why: str, elim: bool = True) -> Check:
    return Check(id=cid, title=title, ok=True, elim=elim, skipped=True, detail=why)


def _tl(mask: np.ndarray, limit: int = 256) -> list[list[int]]:
    """Pixels de uma máscara como [x, y] em coordenadas de imagem (topo-esquerda), para destaque."""
    ys, xs = np.nonzero(mask)
    return [[int(x), int(y)] for y, x in list(zip(ys, xs))[:limit]]


def _fmt_tl(pts: list[list[int]], total: int, limit: int = MAX_LISTED) -> str:
    s = ", ".join(f"[{x},{y}]" for x, y in pts[:limit])
    return s + (f" … (+{total - min(len(pts), limit)})" if total > min(len(pts), limit) else "")


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB uint8 (..., 3) -> (H em graus 0..360, S 0..1, V 0..1), vetorizado (mesma fórmula do colorsys)."""
    c = rgb[..., :3].astype(np.float64) / 255.0
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx, mn = c.max(axis=-1), c.min(axis=-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1), 0.0)
    h = np.zeros_like(mx)
    nz = d > 0
    dd = np.where(nz, d, 1)
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = ((g - b)[rm] / dd[rm]) % 6
    h[gm] = (b - r)[gm] / dd[gm] + 2
    h[bm] = (r - g)[bm] / dd[bm] + 4
    return h * 60.0, s, mx


def _hue_in(h: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Matiz dentro de [lo, hi] em graus; lo > hi dá a volta no 0 (ex.: 340..10 = carmim)."""
    return (h >= lo) & (h <= hi) if lo <= hi else (h >= lo) | (h <= hi)


# ---------------------------------------------------------------- cheques individuais

def check_canvas(arr: np.ndarray, spec: AssetSpec) -> Check:
    h, w = arr.shape[:2]
    ok = (w, h) == tuple(spec.canvas)
    return Check(id="canvas", title="Canvas exato", ok=ok,
                 detail=f"{w}x{h}" + ("" if ok else f" (esperado {spec.width}x{spec.height})"),
                 data={"size": [w, h], "expected": list(spec.canvas)})


def check_alpha(arr: np.ndarray, spec: AssetSpec) -> Check:
    if spec.alpha == "allow":
        return _skip("alpha", "Alfa binário", "spec permite semitransparência", elim=False)
    a = arr[..., 3]
    if spec.alpha == "translucent":                     # água (aceite §8)
        lo, hi = spec.alpha_range
        empty = int((a == 0).sum())
        out = (a < lo) | (a > hi)
        levels = sorted({int(v) for v in np.unique(a)})
        n = int(out.sum())
        ys, xs = np.nonzero(out)
        pts = _bl(arr.shape[0], ys, xs)
        ok = empty == 0 and n == 0 and len(levels) <= spec.alpha_levels
        return Check(id="alpha", title=f"Alfa translúcido ({lo}..{hi}, ≤ {spec.alpha_levels} níveis)", ok=ok,
                     detail=f"níveis de alfa {levels}; {empty} px vazios; {n} px fora da faixa"
                     + (f": {_fmt_pts(pts, n)}" if n else ""),
                     data={"levels": levels, "empty": empty, "count": n, "pixels": pts})
    bad = (a != 0) & (a != 255)
    n = int(bad.sum())
    ys, xs = np.nonzero(bad)
    pts = _bl(arr.shape[0], ys, xs)
    return Check(id="alpha", title="Alfa binário", ok=n == 0,
                 detail="todo pixel com alfa 0 ou 255" if n == 0 else f"{n} pixels semitransparentes: {_fmt_pts(pts, n)}",
                 data={"count": n, "pixels": pts})


def allowed_colors(spec: AssetSpec, palette: PaletteSet, side: str | None = None, group: str | None = None) -> set[str]:
    allowed = palette.allowed(group if group is not None else spec.palette_group)
    if side == "L":  # peças _L: as mesmas cores a 78% (o contorno fica)
        allowed = allowed | {darken(c, 0.78) for c in allowed if c != spec.outline_color}
    return allowed


def check_palette(arr: np.ndarray, spec: AssetSpec, palette: PaletteSet | None, side: str | None = None,
                  group: str | None = None) -> Check:
    title = "Paleta"
    if palette is None or palette.fallback:
        return _skip("palette", title, "projeto sem paleta declarada (pixel.palette): cheque ignorado")
    g = group if group is not None else spec.palette_group
    try:
        allowed = allowed_colors(spec, palette, side, g)
    except KeyError as e:
        return Check(id="palette", title=title, ok=False, detail=str(e).strip('"'), data={"group": g})
    used = _colors(arr)
    off = {c: n for c, n in used.items() if c not in allowed}
    scope = "núcleo" + (f" + grupo `{g}`" if g else "") + (" (+ tons _L a 78%)" if side == "L" else "")
    if not off:
        detail = f"{len(used)} cores, todas no {scope}"
    else:
        detail = f"{len(off)} cores fora do {scope} ({sum(off.values())} px): " + \
                 ", ".join(f"{c}×{n}" for c, n in list(off.items())[:MAX_LISTED])
    return Check(id="palette", title=title, ok=not off, detail=detail,
                 data={"group": g, "used": used, "off_palette": off})


def check_outline(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = "Contorno 1 px " + spec.outline_color
    op = arr[..., 3] > 0
    oc = _color_mask(arr, spec.outline_color)
    border = border_pixels(op, edges=spec.outline_edges)
    if spec.outline == "optional":
        return _skip("outline", title, "contorno opcional nesta spec", elim=False)
    if spec.outline == "none":
        nb = int(border.sum())
        ring = int((border & oc).sum())
        ok = nb == 0 or ring <= nb // 2
        return Check(id="outline", title="Sem contorno", ok=ok,
                     detail="sem anel de contorno" if ok else f"{ring} de {nb} pixels da borda são {spec.outline_color}",
                     data={"border": nb, "outline_on_border": ring})
    if not op.any():
        return Check(id="outline", title=title, ok=False, detail="imagem vazia")
    missing = border & ~oc
    body = op & ~oc
    thick = oc & ~neighbors4(body)        # contorno que não encosta no corpo = 2ª camada / contorno solto
    n_miss, n_thick = int(missing.sum()), int(thick.sum())
    parts = []
    my, mx = np.nonzero(missing)
    ty, tx = np.nonzero(thick)
    miss_pts, thick_pts = _bl(arr.shape[0], my, mx), _bl(arr.shape[0], ty, tx)
    if n_miss:
        parts.append(f"{n_miss} furos (borda da silhueta sem contorno): {_fmt_pts(miss_pts, n_miss)}")
    if n_thick:
        parts.append(f"{n_thick} px de contorno grosso/solto (não encosta no corpo): {_fmt_pts(thick_pts, n_thick)}")
    if not body.any():
        parts.append("só há pixels de contorno")
    ok = not parts
    return Check(id="outline", title=title, ok=ok,
                 detail="contínuo, 1 px, só por fora" if ok else "; ".join(parts),
                 data={"missing": n_miss, "thick": n_thick, "missing_px": miss_pts, "thick_px": thick_pts,
                       "outline_px": int(oc.sum())})


def check_islands(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = f"Sem ilhas (< {spec.min_island} px)"
    op = arr[..., 3] > 0
    comps = components(op, spec.island_connectivity)
    sparkle = {c for c in spec.sparkle_colors}
    bad = []
    for comp in comps:
        if len(comp) >= spec.min_island:
            continue
        cols = {rgb_to_hex(arr[y, x, :3]) for y, x in comp}
        if sparkle and cols <= sparkle:
            continue
        bad.append(comp)
    pts = [(int(c[0][1]), int(arr.shape[0] - 1 - c[0][0])) for c in bad]
    return Check(id="islands", title=title, ok=not bad,
                 detail=f"{len(comps)} grupo(s) conexo(s), nenhum isolado" if not bad else
                 f"{len(bad)} ilha(s) pequena(s) em {_fmt_pts(pts[:MAX_LISTED], len(pts))}",
                 data={"components": len(comps), "islands": pts, "sizes": sorted(len(c) for c in comps)})


def check_safe_area(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = "Dentro da área segura"
    if spec.safe_area is None:
        return _skip("safe_area", title, "spec sem área segura")
    x0, r0, x1, r1 = spec.safe_box_rows()
    op = arr[..., 3] > 0
    inside = np.zeros_like(op)
    inside[max(r0, 0):max(r1, 0), max(x0, 0):max(x1, 0)] = True
    out = op & ~inside
    n = int(out.sum())
    ys, xs = np.nonzero(out)
    pts = _bl(arr.shape[0], ys, xs)
    sx, sy, sw, sh = spec.safe_area
    return Check(id="safe_area", title=title, ok=n == 0,
                 detail=f"tudo em x {sx}..{sx + sw - 1}, y {sy}..{sy + sh - 1}" if n == 0 else
                 f"{n} px fora da área ({sx},{sy},{sw},{sh}): {_fmt_pts(pts, n)}",
                 data={"outside": n, "pixels": pts})


def check_anchor(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = f"Âncora no pivô (≤ {spec.anchor_tolerance} px)"
    if not spec.check_anchor:
        return _skip("anchor", title, "âncora não exigida nesta spec")
    op = arr[..., 3] > 0
    if not op.any():
        return Check(id="anchor", title=title, ok=False, detail="imagem vazia")
    px, py = spec.pivot_xy()
    h = arr.shape[0]
    ys, xs = np.nonzero(op)
    yb = h - 1 - ys                                  # y a partir de baixo; pixel = [x, x+1] x [yb, yb+1]
    dx = np.maximum(np.maximum(xs - px, 0), px - (xs + 1))
    dy = np.maximum(np.maximum(yb - py, 0), py - (yb + 1))
    d = np.sqrt(dx ** 2 + dy ** 2)
    i = int(np.argmin(d))
    dist = float(d[i])
    ok = dist <= spec.anchor_tolerance
    return Check(id="anchor", title=title, ok=ok,
                 detail=f"pixel opaco mais próximo do pivô ({px:g},{py:g}) em ({int(xs[i])},{int(yb[i])}), distância {dist:.2f} px",
                 data={"distance": dist, "nearest": [int(xs[i]), int(yb[i])], "pivot": [px, py]})


def _pair_diffs(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    """Diferença média entre colunas adjacentes (interiores) e a da emenda (última -> primeira)."""
    d = np.linalg.norm(rgb[:, 1:] - rgb[:, :-1], axis=2).mean(axis=0)
    seam = float(np.linalg.norm(rgb[:, -1] - rgb[:, 0], axis=1).mean())
    return d, seam


def seam_stats(arr: np.ndarray, axis: Literal["x", "y"], tolerance: float = 1.5) -> dict[str, float | bool]:
    """Tileabilidade num eixo. x = emenda entre a última e a primeira coluna (repetição lateral).

    Diferença = distância RGB média por linha entre colunas adjacentes. As fronteiras "do miolo"
    (colunas 1..W-2, sem as que tocam a borda) dão a referência; limite = max(tolerância × média,
    maior fronteira do miolo) + 1 — o "≈ diferença média entre colunas internas" do aceite, sem
    reprovar padrões regulares como tijolo (cuja emenda é igual às juntas internas).
    Falha se a emenda (última×0) passa do limite ou se a 1ª/última coluna destoa das vizinhas
    (a "grade" visível: linha de cor diferente na borda)."""
    rgb = arr[..., :3].astype(np.float64)
    if axis == "y":
        rgb = rgb.transpose(1, 0, 2)
    if rgb.shape[1] < 4:
        return {"seam": 0.0, "interior": 0.0, "limit": 0.0, "edge": 0.0, "edge_line": False, "ok": True}
    inner, seam = _pair_diffs(rgb)
    core = inner[1:-1]
    limit = max(tolerance * float(core.mean()), float(core.max())) + 1.0
    edge = float(max(inner[0], inner[-1]))
    edge_line = edge > limit
    ok = seam <= limit and not edge_line
    return {"seam": round(seam, 3), "interior": round(float(core.mean()), 3), "limit": round(limit, 3),
            "edge": round(edge, 3), "edge_line": bool(edge_line), "ok": bool(ok)}


def check_tileable(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = "Tileável sem emenda"
    if not spec.tileable:
        return _skip("tileable", title, "spec não tileável")
    axes = ["x", "y"] if spec.tile_axes == "both" else [spec.tile_axes]
    stats = {ax: seam_stats(arr, ax, spec.tile_tolerance) for ax in axes}
    ok = all(s["ok"] for s in stats.values())
    names = {"x": "coluna última×0", "y": "linha última×0"}
    parts = []
    for ax, s in stats.items():
        msg = f"{names[ax]}: emenda {s['seam']:.1f} ≤ limite {s['limit']:.1f}" if s["ok"] else \
            (f"{names[ax]}: emenda {s['seam']:.1f} > limite {s['limit']:.1f}" if s["seam"] > s["limit"]
             else f"{names[ax]}: linha de cor diferente na borda (dif. {s['edge']:.1f})")
        parts.append(msg)
    return Check(id="tileable", title=title, ok=ok, detail="; ".join(parts), data=stats)


def check_opaque(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = "Sem pixel transparente"
    if not spec.opaque:
        return _skip("opaque", title, "transparência permitida nesta spec")
    tr = arr[..., 3] == 0
    n = int(tr.sum())
    ys, xs = np.nonzero(tr)
    pts = _bl(arr.shape[0], ys, xs)
    return Check(id="opaque", title=title, ok=n == 0,
                 detail="tile sólido, sem transparência" if n == 0 else f"{n} px transparentes: {_fmt_pts(pts, n)}",
                 data={"transparent": n})


def check_max_colors(arr: np.ndarray, spec: AssetSpec) -> Check:
    title = f"Máx. {spec.max_colors} cores" if spec.max_colors else "Limite de cores"
    if not spec.max_colors:
        return _skip("max_colors", title, "spec sem limite de cores")
    used = _colors(arr)
    ok = len(used) <= spec.max_colors
    return Check(id="max_colors", title=title, ok=ok, detail=f"{len(used)} cores (limite {spec.max_colors})",
                 data={"count": len(used), "colors": list(used)})


def check_coverage(arr: np.ndarray, spec: AssetSpec) -> Check:
    if not spec.coverage:
        return _skip("coverage", "Cobertura do objeto", "spec sem faixa de cobertura")
    lo, hi = spec.coverage
    title = f"Objeto cobre {lo}..{hi} px"
    b = bbox(arr[..., 3] > 0)
    if b is None:
        return Check(id="coverage", title=title, ok=False, detail="imagem vazia")
    w, h = b[2] - b[0], b[3] - b[1]
    m = max(w, h)
    return Check(id="coverage", title=title, ok=lo <= m <= hi,
                 detail=f"maior dimensão {m} px ({w}x{h})", data={"size": [w, h], "max_dim": m})


def check_name(name: str | None, spec: AssetSpec) -> Check:
    title = "Nome do arquivo"
    pat = spec.resolved_name_pattern()
    if not name:
        return _skip("name", title, "nome final não informado")
    if not pat:
        return _skip("name", title, "spec sem padrão de nome")
    base = re.split(r"[\\/]", name)[-1]
    ok = re.match(pat, base) is not None
    return Check(id="name", title=title, ok=ok, detail=f"`{base}`" + ("" if ok else f" não segue `{pat}`"),
                 data={"name": base, "pattern": pat})


# ---------------------------------------------------------------- §7: material, espessura, regras de cor

# assinaturas de cor (H em graus, S e V em 0..1)
def _sig_copper(h, s, v):      # cobre/laranja metálico claro (madeira de cabo é mais escura: V < 60%)
    return _hue_in(h, 15, 35) & (s >= 0.45) & (v >= 0.60)


def _sig_soul(h, s, v):        # brilho ciano de alma
    return _hue_in(h, 170, 200) & (s >= 0.30) & (v >= 0.60)


def _sig_blue(h, s, v):        # azul saturado (não é ferro: ferro é cinza-azulado)
    return _hue_in(h, 200, 260) & (s >= 0.40)


def _sig_gold(h, s, v):
    return _hue_in(h, 35, 55) & (s >= 0.40) & (v >= 0.60)


def _sig_crimson(h, s, v):
    return _hue_in(h, 340, 10) & (s >= 0.40)


# material -> (faixa do material, assinaturas proibidas, descrição da faixa)
MATERIAL_RULES: dict[str, tuple[Any, tuple[Any, ...], str]] = {
    "copper": (lambda h, s, v: _hue_in(h, 15, 35) & (s >= 0.45), (_sig_soul, _sig_blue),
               "matiz 15–35° com S ≥ 45%"),
    "iron": (lambda h, s, v: (s <= 0.25) & (_hue_in(h, 200, 260) | (s < 0.10)), (_sig_copper, _sig_soul, _sig_blue),
             "S ≤ 25% e matiz 200–260° (ou neutro, S < 10%): cinza-azulado"),
    "soul": (lambda h, s, v: _hue_in(h, 170, 200) & (s >= 0.20) & (v >= 0.50), (_sig_copper,),
             "ciano 170–200° (S ≥ 20%, V ≥ 50%), o tom mais claro com V ≥ 70%; ouro só como brilho ≤ 15%"),
    "wood": (lambda h, s, v: _hue_in(h, 20, 40) & (s >= 0.25) & (s <= 0.60), (_sig_soul,),
             "matiz 20–40° com S 25–60%"),
    "leather": (lambda h, s, v: _hue_in(h, 20, 40) & (s >= 0.25) & (s <= 0.60), (_sig_soul,),
                "matiz 20–40° com S 25–60%"),
}
MATERIAL_MIN_RATIO = 0.60
SOUL_GOLD_MAX = 0.15
# soul: o carmim é a cor do tipo de alma ofensiva (decisão de arte); danger fica para inimigos/projéteis
CRIMSON_TAGS = frozenset({"danger", "blood", "castle", "vania", "life", "soul"})
GOLD_TAGS = frozenset({"soul", "reward", "noble", "fire", "light"})
# grupos de paleta cuja cor "dourada" é a do próprio material (decisão de arte: a areia), não ouro
GOLD_EXEMPT_GROUPS = frozenset({"tile/sand"})


def _fill_mask(arr: np.ndarray, spec: AssetSpec) -> np.ndarray:
    """Pixels opacos que não são da cor do contorno."""
    return (arr[..., 3] > 0) & ~_color_mask(arr, spec.outline_color)


def check_material_color(arr: np.ndarray, spec: AssetSpec, material: str | None = None) -> Check:
    """Cor por material (aceite §7). Heurística, sem saber qual parte é "base" (pedra, cabo de madeira):

    1. pixels de destaque = preenchimento (sem o contorno) que NÃO é escuro-neutro (V < 35%, ou
       S < 30% com V < 50%: a pedra e as sombras) nem brilho branco (S < 15% e V ≥ 90%; conta só no
       ferro, que é neutro); dentre eles, só os saturados (S ≥ 20%) ou claros (V ≥ 70%);
    2. passa se ≥ 60% desses pixels caem na faixa do material E nenhum deles cai numa assinatura
       proibida (cobre/alma no ferro, azul saturado no ferro e no cobre, alma no cobre, cobre na alma...);
    3. alma: o tom mais claro na faixa tem V ≥ 70% e ouro (35–55°) é no máximo 15% dos destaques.

    Um cabo de madeira (S ~50%, V < 60%) entra nos destaques mas fica fora da faixa do ferro; numa
    picareta de ferro a cabeça é a maioria, então passa. Material sem regra (pedra, ouro...) é ignorado.
    """
    mat = (material or spec.material or "").strip().lower() or None
    title = f"Cor do material ({mat})" if mat else "Cor do material"
    if not mat:
        return _skip("material_color", title, "asset sem material declarado")
    if mat not in MATERIAL_RULES:
        return _skip("material_color", title, f"material {mat!r} sem regra de cor")
    in_range, forbidden_sigs, desc = MATERIAL_RULES[mat]
    h, s, v = rgb_to_hsv(arr)
    fill = _fill_mask(arr, spec)
    dark = (v < 0.35) | ((s < 0.30) & (v < 0.50))
    white = (s < 0.15) & (v >= 0.90)
    accent = fill & ~dark & ((s >= 0.20) | (v >= 0.70))
    if mat != "iron":
        accent &= ~white
    n_acc = int(accent.sum())
    inside = accent & in_range(h, s, v)
    n_in = int(inside.sum())
    ratio = n_in / n_acc if n_acc else 0.0
    forb = np.zeros_like(accent)
    for sig in forbidden_sigs:
        forb |= accent & sig(h, s, v)
    n_forb = int(forb.sum())
    off = accent & ~inside
    problems = []
    if n_acc == 0:
        problems.append("nenhum pixel de destaque (saturado ou claro) para julgar o material")
    elif ratio < MATERIAL_MIN_RATIO:
        problems.append(f"só {ratio * 100:.0f}% dos destaques na faixa ({n_in}/{n_acc}; mínimo "
                        f"{MATERIAL_MIN_RATIO * 100:.0f}%)")
    if n_forb:
        problems.append(f"{n_forb} px em cor proibida para {mat}: {_fmt_tl(_tl(forb), n_forb)}")
    data: dict[str, Any] = {"coords": "top-left", "material": mat, "rule": desc, "accent": n_acc, "in_range": n_in,
                            "ratio": round(ratio, 3), "forbidden_px": _tl(forb), "off_px": _tl(off),
                            "in_px": _tl(inside)}
    if mat == "soul" and n_acc:
        vmax = float(v[inside].max()) if n_in else 0.0
        gold = accent & _sig_gold(h, s, v)
        g_ratio = int(gold.sum()) / n_acc
        data.update({"max_value": round(vmax, 3), "gold_ratio": round(g_ratio, 3), "gold_px": _tl(gold)})
        if n_in and vmax < 0.70:
            problems.append(f"alma sem brilho: tom mais claro com V {vmax * 100:.0f}% (< 70%)")
        if g_ratio > SOUL_GOLD_MAX:
            problems.append(f"ouro em {g_ratio * 100:.0f}% dos destaques (alma aceita ≤ {SOUL_GOLD_MAX * 100:.0f}%)")
    ok = not problems
    return Check(id="material_color", title=title, ok=ok,
                 detail=(f"{ratio * 100:.0f}% dos destaques na faixa do {mat} ({desc})" if ok else "; ".join(problems)),
                 data=data)


def check_thickness(arr: np.ndarray, spec: AssetSpec, min_blocks: int | None = None, max_lonely: float = 0.15,
                    connectivity: Literal[4, 8] = 8) -> Check:
    """Espessura (aceite §7): o preenchimento (sem contorno) tem ≥ 3 blocos 2x2 sólidos (posições
    sobrepostas contam; `spec.min_blocks` muda o mínimo: 2 nos tiles de 11 px) e no máximo 15% dos pixels
    sem vizinho da mesma cor (brilhos declarados não contam). "Vizinho" = 8-vizinhança: faixas de cor em
    diagonal (lâmina, cabo de 2 px em diagonal) são contínuas; o que reprova é o "pontilhado" (pixels de
    cor solta). [elim] em ícone e arma."""
    if min_blocks is None:
        min_blocks = spec.min_blocks if spec.min_blocks is not None else 3
    elim = spec.kind in ("icon", "weapon")
    title = f"Espessura (≥ {min_blocks} blocos 2x2, ≤ {round(max_lonely * 100)}% px soltos)"
    fill = _fill_mask(arr, spec)
    n_fill = int(fill.sum())
    if n_fill == 0:
        return Check(id="thickness", title=title, ok=False, elim=elim, detail="sem preenchimento (só contorno ou vazio)",
                     data={"coords": "top-left", "blocks": 0, "block_px": [], "lonely_px": [], "lonely_ratio": 0.0})
    blocks = fill[:-1, :-1] & fill[1:, :-1] & fill[:-1, 1:] & fill[1:, 1:]
    n_blocks = int(blocks.sum())
    rgb = arr[..., :3].astype(np.int32)
    key = (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]
    h, w = fill.shape
    pad_k = np.full((h + 2, w + 2), -1, np.int64)
    pad_k[1:-1, 1:-1] = np.where(fill, key, -1)
    same = np.zeros_like(fill)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dy == 0 and dx == 0) or (connectivity == 4 and dy != 0 and dx != 0):
                continue
            same |= pad_k[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx] == key
    sparkle = np.zeros_like(fill)
    for c in spec.sparkle_colors:
        sparkle |= _color_mask(arr, c)
    counted = fill & ~sparkle
    lonely = counted & ~same
    n_lonely, n_counted = int(lonely.sum()), int(counted.sum())
    ratio = n_lonely / n_counted if n_counted else 0.0
    problems = []
    if n_blocks < min_blocks:
        problems.append(f"fino demais: {n_blocks} bloco(s) 2x2 sólidos (mínimo {min_blocks})")
    if ratio > max_lonely:
        problems.append(f"pontilhado: {n_lonely} de {n_counted} px ({ratio * 100:.0f}%) sem vizinho da mesma cor: "
                        + _fmt_tl(_tl(lonely), n_lonely))
    ok = not problems
    return Check(id="thickness", title=title, ok=ok, elim=elim,
                 detail=(f"{n_blocks} blocos 2x2; {ratio * 100:.0f}% px soltos" if ok else "; ".join(problems)),
                 data={"coords": "top-left", "blocks": n_blocks, "block_px": _tl(blocks), "lonely": n_lonely,
                       "lonely_ratio": round(ratio, 3), "lonely_px": _tl(lonely)})


def check_color_rules(arr: np.ndarray, spec: AssetSpec, tags: Sequence[str] | None = None,
                      palette_group: str | None = None) -> Check:
    """Regras de cor por tag (aceite §7): carmim (matiz 340–10°, S ≥ 40%) só com danger/blood/castle/
    vania/life/soul (alma ofensiva); ouro (35–55°, S ≥ 40%, V ≥ 60%) só com soul/reward/noble/fire/light. O contorno não conta.
    Grupos de GOLD_EXEMPT_GROUPS (tile/sand) liberam o "ouro": ali é a cor do próprio material."""
    tg = {t.strip().lower() for t in [*(spec.tags or []), *(tags or [])] if t and t.strip()}
    groups = {g.strip() for g in (palette_group or spec.palette_group or "").split("+") if g.strip()}
    title = "Regras de cor (carmim/ouro por tag)"
    h, s, v = rgb_to_hsv(arr)
    fill = _fill_mask(arr, spec)
    crimson = fill & _sig_crimson(h, s, v)
    gold = fill & _sig_gold(h, s, v)
    crimson_ok = bool(tg & CRIMSON_TAGS)
    gold_ok = bool(tg & GOLD_TAGS) or bool(groups & GOLD_EXEMPT_GROUPS)
    bad_c = crimson if not crimson_ok else np.zeros_like(crimson)
    bad_g = gold if not gold_ok else np.zeros_like(gold)
    problems = []
    if bad_c.any():
        n = int(bad_c.sum())
        problems.append(f"{n} px carmim sem tag {'/'.join(sorted(CRIMSON_TAGS))}: {_fmt_tl(_tl(bad_c), n)}")
    if bad_g.any():
        n = int(bad_g.sum())
        problems.append(f"{n} px ouro sem tag {'/'.join(sorted(GOLD_TAGS))}: {_fmt_tl(_tl(bad_g), n)}")
    ok = not problems
    detail = (f"carmim {int(crimson.sum())} px, ouro {int(gold.sum())} px; tags: {', '.join(sorted(tg)) or '—'}"
              if ok else "; ".join(problems))
    return Check(id="color_rules", title=title, ok=ok, detail=detail,
                 data={"coords": "top-left", "tags": sorted(tg), "crimson": int(crimson.sum()), "gold": int(gold.sum()),
                       "crimson_px": _tl(bad_c), "gold_px": _tl(bad_g)})


def check_lr_pair(left: Image.Image | np.ndarray, right: Image.Image | np.ndarray, factor: float = 0.78,
                  outline_color: str = "#0b0a12", tolerance: int = 2) -> Check:
    """_L = mesma máscara alfa que _R e cores a `factor` (±tolerância por canal), menos o contorno."""
    title = f"Par _L/_R (mesma forma, cor a {round(factor * 100)}%)"
    L, R = to_rgba(left), to_rgba(right)
    if L.shape != R.shape:
        return Check(id="lr_pair", title=title, ok=False, detail=f"canvas diferentes: {L.shape[1]}x{L.shape[0]} × {R.shape[1]}x{R.shape[0]}")
    ml, mr = L[..., 3] > 0, R[..., 3] > 0
    mask_diff = int((ml != mr).sum())
    both = ml & mr
    oc = _color_mask(R, outline_color)
    exp = R[..., :3].astype(np.float64)
    exp[~oc] = js_round(exp[~oc] * factor)
    diff = np.abs(L[..., :3].astype(np.float64) - exp).max(axis=2)
    bad = both & (diff > tolerance)
    n_bad = int(bad.sum())
    ys, xs = np.nonzero(bad)
    pts = _bl(L.shape[0], ys, xs)
    parts = []
    if mask_diff:
        parts.append(f"máscara alfa difere em {mask_diff} px")
    if n_bad:
        parts.append(f"{n_bad} px fora de {round(factor * 100)}% ±{tolerance}: {_fmt_pts(pts, n_bad)}")
    return Check(id="lr_pair", title=title, ok=not parts,
                 detail="mesma máscara, cores a 78%, contorno intacto" if not parts else "; ".join(parts),
                 data={"mask_diff": mask_diff, "color_off": n_bad, "pixels": pts})


# ---------------------------------------------------------------- formas de bloco (spec-blocos-11px.md §3/§5)

def check_shape(img: Image.Image | np.ndarray, shape: str, size: int | None = None) -> Check:
    """Forma do bloco por máscara alfa [elim]: os pixels opacos são EXATAMENTE a máscara canônica da forma
    (autotile.shape_mask: escada de 1 px nas ladeiras, 2 px no pico, meio-bloco nas linhas de baixo).
    Reprova pixel vazio dentro da forma (furo) e pixel opaco fora dela (pixel solto no ar)."""
    from .autotile import shape_mask

    a = to_rgba(img)
    h, w = a.shape[:2]
    n = int(size) if size else w
    title = f"Forma {shape} ({n}x{n})"
    if (w, h) != (n, n):
        return Check(id="shape", title=title, ok=False, detail=f"canvas {w}x{h} (esperado {n}x{n})",
                     data={"shape": shape, "size": n})
    try:
        want = shape_mask(shape, n)
    except KeyError as e:
        return Check(id="shape", title=title, ok=False, detail=str(e).strip('"'), data={"shape": shape})
    op = a[..., 3] > 0
    holes = want & ~op
    extra = op & ~want
    nh, ne = int(holes.sum()), int(extra.sum())
    parts = []
    if nh:
        parts.append(f"{nh} px vazios dentro da forma: {_fmt_tl(_tl(holes), nh)}")
    if ne:
        parts.append(f"{ne} px opacos fora da forma: {_fmt_tl(_tl(extra), ne)}")
    return Check(id="shape", title=title, ok=not parts,
                 detail=(f"alfa = máscara de {shape} ({int(want.sum())} px sólidos)" if not parts else "; ".join(parts)),
                 data={"coords": "top-left", "shape": shape, "size": n, "solid": int(want.sum()),
                       "holes": nh, "extra": ne, "holes_px": _tl(holes), "extra_px": _tl(extra)})


# arestas de cada forma que encostam em bloco: (lado da forma, lado do bloco vizinho)
SHAPE_CONTACT_EDGES: dict[str, tuple[str, ...]] = {
    "full": ("left", "right", "up", "down"),
    "slope_down": ("left", "down"),
    "slope_up": ("right", "down"),
    "peak": ("down",),
    "half": ("left", "right", "down"),
}


def check_shape_seams(shape_img: Image.Image | np.ndarray, full_img: Image.Image | np.ndarray, shape: str,
                      tolerance: float = 1.5, crust_depth: int = 0) -> Check:
    """Emenda da forma com o bloco cheio do mesmo material [elim] (spec §5: "a emenda vale só nas arestas que
    encostam em bloco"): em cada aresta de contato (SHAPE_CONTACT_EDGES), a diferença média entre a
    linha/coluna da forma e a oposta do bloco cheio vizinho (só pixels opacos) fica ≤ o limite de
    tileabilidade do próprio bloco cheio naquele eixo (seam_stats). Ex.: slope_down ao lado direito de um
    full = coluna 10 do full × coluna 0 da ladeira; ladeira em cima do full = linha 10 da ladeira × linha 0.
    `crust_depth`: os pixels a menos de N px (na vertical) do topo da sua coluna são a crosta da borda
    exposta (grama, quina, parapeito), não textura, e ficam fora da conta (0 = conta tudo).
    O limite de cada aresta é max(limite de tileabilidade do bloco, emenda do próprio bloco cheio nos mesmos
    pixels + 1): a forma passa se não piora a emenda que o material já tem ali."""
    s, f = to_rgba(shape_img), to_rgba(full_img)
    title = f"Emenda da forma {shape} com o bloco cheio"
    if s.shape != f.shape:
        return Check(id="shape_seams", title=title, ok=False,
                     detail=f"canvas diferentes: {s.shape[1]}x{s.shape[0]} × {f.shape[1]}x{f.shape[0]}")
    edges = SHAPE_CONTACT_EDGES.get(shape)
    if edges is None:
        return Check(id="shape_seams", title=title, ok=False, detail=f"forma desconhecida: {shape!r}")
    lim = {ax: float(seam_stats(f, ax, tolerance)["limit"]) for ax in ("x", "y")}
    op = s[..., 3] > 0
    # profundidade de cada pixel a partir do topo sólido da sua coluna (0 = o pixel exposto de cima)
    depth = np.cumsum(op, axis=0) - 1
    texture = op & (depth >= crust_depth)
    res: dict[str, dict[str, float]] = {}
    bad = []
    for side in edges:
        # (pixels da forma na aresta, pixels do bloco vizinho do outro lado, máscara de textura da aresta)
        # (aresta da forma, aresta do bloco vizinho, a mesma aresta no próprio bloco cheio, máscara de textura)
        if side == "left":
            es, ef, eo, ax, tx = s[:, 0], f[:, -1], f[:, 0], "x", texture[:, 0]
        elif side == "right":
            es, ef, eo, ax, tx = s[:, -1], f[:, 0], f[:, -1], "x", texture[:, -1]
        elif side == "down":
            es, ef, eo, ax, tx = s[-1, :], f[0, :], f[-1, :], "y", texture[-1, :]
        else:
            es, ef, eo, ax, tx = s[0, :], f[-1, :], f[0, :], "y", texture[0, :]
        both = (es[:, 3] > 0) & (ef[:, 3] > 0) & tx
        if not both.any():
            bad.append(f"{side}: aresta de contato vazia")
            continue

        def _mean(a, b):
            return float(np.linalg.norm(a[both, :3].astype(np.float64) - b[both, :3].astype(np.float64), axis=1).mean())

        d = _mean(es, ef)
        # a emenda do próprio bloco cheio nos MESMOS pixels: num padrão regular (argamassa do tijolo) uma
        # amostra parcial da aresta pode passar do limite medido na linha inteira; a forma não piora o material
        own = _mean(eo, ef)
        allowed = max(lim[ax], own + 1.0)
        res[side] = {"seam": round(d, 3), "limit": round(allowed, 3), "full_seam": round(own, 3), "px": int(both.sum())}
        if d > allowed:
            bad.append(f"{side}: emenda {d:.1f} > limite {allowed:.1f}")
    detail = ("; ".join(f"{k} {v['seam']:.1f} ≤ {v['limit']:.1f}" for k, v in res.items()) if not bad
              else "; ".join(bad))
    return Check(id="shape_seams", title=title, ok=not bad, detail=detail, data={"edges": res, "shape": shape})


# ---------------------------------------------------------------- validação completa

def validate(img: Image.Image | np.ndarray, spec: AssetSpec, palette: PaletteSet | None = None, *,
             name: str | None = None, side: str | None = None, pair: Image.Image | np.ndarray | None = None,
             palette_group: str | None = None, references: Sequence[Image.Image] | None = None,
             material: str | None = None, tags: Sequence[str] | None = None) -> Report:
    """Roda todos os cheques da spec.
    name: nome final do arquivo (cheque de nome; o lado _L/_R também é deduzido dele).
    side: "L" aceita os tons a 78% na paleta; com `pair` (a peça _R) roda o cheque do par.
    references: imagens da mesma categoria; confere que o canvas é o mesmo (aviso).
    material: copper, iron, soul, wood, leather (outros sem regra); vazio = spec.material.
    tags: tags de cor do asset (somadas a spec.tags) para a regra de carmim/ouro."""
    arr = to_rgba(img)
    side = side or (side_of_file(name) if name else None)
    checks = [check_canvas(arr, spec)]
    geom_ok = checks[0].ok
    checks.append(check_alpha(arr, spec))
    checks.append(check_palette(arr, spec, palette, side, palette_group))
    checks.append(check_outline(arr, spec))
    checks.append(check_islands(arr, spec))
    if geom_ok:
        checks.append(check_safe_area(arr, spec))
        checks.append(check_anchor(arr, spec))
    else:
        checks.append(_skip("safe_area", "Dentro da área segura", "não avaliado: canvas errado"))
        checks.append(_skip("anchor", "Âncora no pivô", "não avaliado: canvas errado"))
    checks.append(check_tileable(arr, spec))
    checks.append(check_opaque(arr, spec))
    checks.append(check_max_colors(arr, spec))
    checks.append(check_coverage(arr, spec))
    checks.append(check_name(name, spec))
    checks.append(check_material_color(arr, spec, material))
    checks.append(check_thickness(arr, spec))
    checks.append(check_color_rules(arr, spec, tags, palette_group))
    if spec.shape is not None:               # forma de bloco (11 px): alfa = máscara canônica
        checks.append(check_shape(arr, spec.shape, spec.width) if geom_ok else
                      _skip("shape", f"Forma {spec.shape}", "não avaliado: canvas errado"))
    if spec.nine_slice is not None:          # UI 9-slice: margens declaradas na spec
        checks.append(check_nine_slice(arr, spec.nine_slice))
    if pair is not None:
        checks.append(check_lr_pair(arr, pair, outline_color=spec.outline_color))
    if references:
        c = check_same_canvas([arr, *[to_rgba(r) for r in references]])
        c.elim = False
        c.title = "Mesmo canvas das referências"
        checks.append(c)
    return Report.build(checks, spec=spec.name, kind=spec.kind, file=name, side=side,
                        canvas=(arr.shape[1], arr.shape[0]))


# ---------------------------------------------------------------- conjuntos de imagens

def check_same_canvas(images: Sequence[Image.Image | np.ndarray]) -> Check:
    sizes = [(to_rgba(i).shape[1], to_rgba(i).shape[0]) for i in images]
    ok = len(set(sizes)) <= 1
    return Check(id="same_canvas", title="Mesmo canvas em todas", ok=ok,
                 detail=f"{sizes[0][0]}x{sizes[0][1]}" if ok and sizes else "tamanhos: " + ", ".join(f"{w}x{h}" for w, h in sizes),
                 data={"sizes": sizes})


def _ground_row(arr: np.ndarray) -> int | None:
    rows = np.nonzero((arr[..., 3] > 0).any(axis=1))[0]
    return int(arr.shape[0] - 1 - rows.max()) if len(rows) else None


def _center_of_mass(arr: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.nonzero(arr[..., 3] > 0)
    return (float(xs.mean()), float(arr.shape[0] - 1 - ys.mean())) if len(xs) else None


PoseClasses = Sequence[str] | dict[str, Sequence[int]]


def pose_class_groups(n: int, classes: PoseClasses | None = None) -> dict[str, list[int]]:
    """Classes de pose -> {rótulo: [índices em ordem]}. Aceita lista paralela às imagens
    (["locomocao", "locomocao", "ataque", "ataque"]) ou dict {rótulo: [índices]}; índice sem classe
    no dict vira a sua própria classe. None = todas na mesma classe."""
    if classes is None:
        return {"*": list(range(n))}
    groups: dict[str, list[int]] = {}
    if isinstance(classes, dict):
        seen: set[int] = set()
        for label, idx in classes.items():
            for i in idx:
                if not 0 <= int(i) < n:
                    raise ValueError(f"classe {label!r}: índice de pose {i} fora de 0..{n - 1}")
                groups.setdefault(str(label), []).append(int(i))
                seen.add(int(i))
        for i in range(n):
            if i not in seen:
                groups[f"#{i}"] = [i]
        return {k: sorted(v) for k, v in groups.items()}
    labels = list(classes)
    if len(labels) != n:
        raise ValueError(f"classes tem {len(labels)} rótulos para {n} poses")
    for i, label in enumerate(labels):
        groups.setdefault(str(label), []).append(i)
    return groups


def check_pose_set(images: Sequence[Image.Image | np.ndarray], mode: Literal["ground", "flyer"] = "ground",
                   change_range: tuple[float, float] = (0.08, 0.60), max_palette_diff: int = 1,
                   com_tolerance: float = 2.0, classes: PoseClasses | None = None,
                   cyclic: Sequence[str] = ()) -> list[Check]:
    """Coerência de poses de inimigo (aceite §C e §7): canvas e linha do chão iguais em TODAS as poses;
    paleta igual (≤ 1 cor de diferença), centro de massa (voador, ≤ 2 px) e mudança entre poses
    consecutivas (8%..60% do corpo) só entre poses da MESMA classe (voo com voo, solo com solo;
    preparação/golpe e repouso são classes próprias). `classes`: ver `pose_class_groups`.
    `cyclic`: classes em laço (ciclo de caminhada): o par última -> primeira também é comparado."""
    arrs = [to_rgba(i) for i in images]
    out = [check_same_canvas(arrs)]
    if not out[0].ok:
        return out
    groups = pose_class_groups(len(arrs), classes)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if mode == "ground":
        grounds = [_ground_row(a) for a in arrs]
        ok = len(set(grounds)) <= 1
        out.append(Check(id="ground_line", title="Linha do chão estável", ok=ok,
                         detail=f"pé mais baixo em y={grounds[0]}" if ok else f"linhas do chão: {grounds}",
                         data={"ground": grounds}))
    else:
        coms = [_center_of_mass(a) for a in arrs]
        spreads: dict[str, float] = {}
        for label, idx in groups.items():
            valid = [coms[i] for i in idx if coms[i]]
            spreads[label] = max((max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a in valid for b in valid), default=0.0)
        spread = max(spreads.values(), default=0.0)
        out.append(Check(id="center_of_mass", title=f"Centro de massa varia ≤ {com_tolerance:g} px (por classe)",
                         ok=spread <= com_tolerance,
                         detail="variação máxima por classe: " + ", ".join(f"{k} {v:.2f} px" for k, v in spreads.items()),
                         data={"centers": coms, "spread": spread, "spread_per_class": spreads, "classes": groups}))
    pals = [set(_colors(a)) for a in arrs]
    diffs = [0] * len(arrs)
    for idx in groups.values():
        union = set().union(*(pals[i] for i in idx))
        for i in idx:
            diffs[i] = len(union - pals[i])
    out.append(Check(id="pose_palette", title=f"Mesma paleta na classe (≤ {max_palette_diff} cor de diferença)",
                     ok=max(diffs, default=0) <= max_palette_diff,
                     detail=f"cores faltando por pose (dentro da classe): {diffs}",
                     data={"missing_per_pose": diffs, "union": sorted(set().union(*pals)), "classes": groups}))
    lo, hi = change_range
    ratios, pairs = [], []
    loops = {str(c) for c in cyclic}
    for label, idx in groups.items():
        seq = list(zip(idx, idx[1:]))
        if label in loops and len(idx) > 2:
            seq.append((idx[-1], idx[0]))
        for i, j in seq:
            a, b = arrs[i], arrs[j]
            body = ((a[..., 3] > 0).sum() + (b[..., 3] > 0).sum()) / 2 or 1
            changed = ((a[..., 3] > 0) != (b[..., 3] > 0)) | (((a[..., 3] > 0) & (b[..., 3] > 0)) & (a[..., :3] != b[..., :3]).any(axis=2))
            r = round(float(changed.sum() / body), 3)
            ratios.append(r)
            pairs.append({"class": label, "from": i, "to": j, "ratio": r})
    ok = all(lo <= r <= hi for r in ratios)
    detail = ("mudança por par consecutivo da mesma classe: " + ", ".join(
        f"{p['from']}→{p['to']} {p['ratio'] * 100:.0f}%" for p in pairs)) if pairs else "nenhuma classe com 2+ poses"
    out.append(Check(id="pose_change", title=f"Mudança entre poses {round(lo * 100)}%..{round(hi * 100)}% (por classe)",
                     ok=ok, detail=detail, data={"ratios": ratios, "pairs": pairs, "classes": groups,
                                                 "classes_compared": sorted(multi), "cyclic": sorted(loops)}))
    return out


def check_body_coherence(images: Sequence[Image.Image | np.ndarray], box: tuple[int, int, int, int],
                         body_colors: Sequence[str] | None = None, wing_colors: Sequence[str] = (),
                         compare: Sequence[int] | None = None, eye_color: str | None = None,
                         boxes: dict[int, tuple[int, int, int, int]] | None = None,
                         max_mask_diff: float = 0.40, max_palette_diff: int = 1, head_tolerance: int = 1,
                         align: int = 1, head_poses: Sequence[int] | None = None,
                         head_offsets: dict[int, tuple[int, int]] | None = None) -> list[Check]:
    """Coerência só do corpo entre poses (aceite §7, voadores: asas e garras ficam fora).
    `box` = (x0, y0, x1, y1) inclusivo, com y contado de BAIXO para cima como o pivô; `boxes` sobrescreve por pose.
    Com `body_colors`, só pixels dessas cores contam como corpo (paleta, máscara e cabeça). `compare` = poses
    comparadas entre si (ex.: voo + mergulho); as demais (ex.: repouso de cabeça para baixo) só não podem usar
    cor fora de corpo ∪ asas. Dentro da caixa: máscara do corpo com diferença ≤ `max_mask_diff` entre pares,
    paleta do corpo com ≤ `max_palette_diff` cor faltando vs a união, e largura do corpo na linha de `eye_color`
    e na seguinte ± `head_tolerance` (sem `eye_color`: as 3 primeiras linhas ocupadas). O contorno entra na paleta
    mas não na máscara/cabeça (ele também envolve as asas); a máscara é comparada no melhor deslocamento de até
    `align` px (o mergulho desloca o corpo 1 px). Cabeça: `head_poses` (padrão = `compare`) são comparadas entre si
    com ± `head_tolerance`; `head_offsets` = {pose: (mín, máx)} relativos à menor largura de `head_poses`
    (ex.: mergulho {3: (0, 2)})."""
    from .palette import hex_to_rgb
    from .spec import OUTLINE_COLOR

    arrs = [to_rgba(i) for i in images]
    h = arrs[0].shape[0]
    idx = list(compare) if compare is not None else list(range(len(arrs)))
    rest = [i for i in range(len(arrs)) if i not in idx]
    body = {hex_to_rgb(c) for c in body_colors} if body_colors else None
    outline = hex_to_rgb(OUTLINE_COLOR)
    wings = {hex_to_rgb(c) for c in wing_colors}
    eye = hex_to_rgb(eye_color) if eye_color else None

    def crop(i: int) -> np.ndarray:
        x0, y0, x1, y1 = (boxes or {}).get(i, box)
        return arrs[i][h - 1 - y1:h - y0, x0:x1 + 1]

    def body_mask(c: np.ndarray, with_outline: bool = False) -> np.ndarray:
        m = c[..., 3] > 0
        if body is None:
            return m
        rgb = c[..., :3]
        hit = np.zeros(m.shape, bool)
        for col in body:
            if with_outline or col != outline:
                hit |= (rgb == col).all(axis=2)
        return m & hit

    crops = {i: crop(i) for i in range(len(arrs))}
    masks = {i: body_mask(crops[i]) for i in idx}
    worst, pairs = 0.0, []
    for n, i in enumerate(idx):
        for j in idx[n + 1:]:
            mi, mj = masks[i], masks[j]
            if mi.shape != mj.shape:      # caixa própria numa pose: compara na interseção alinhada à esquerda/baixo
                hh, ww = min(mi.shape[0], mj.shape[0]), min(mi.shape[1], mj.shape[1])
                mi, mj = mi[-hh:, :ww], mj[-hh:, :ww]
            area = (mi.sum() + mj.sum()) / 2 or 1
            best = min(int((mi != np.roll(np.roll(mj, dy, 0), dx, 1)).sum())
                       for dy in range(-align, align + 1) for dx in range(-align, align + 1))
            r = round(float(best / area), 3)
            pairs.append({"from": i, "to": j, "ratio": r})
            worst = max(worst, r)
    out = [Check(id="body_mask", title=f"Corpo: diferença de máscara ≤ {round(max_mask_diff * 100)}%", ok=worst <= max_mask_diff,
                 detail=f"pior par {worst * 100:.0f}% (poses {idx}, caixa {list(box)})",
                 data={"pairs": pairs, "box": list(box), "compared": idx})]
    pals = {i: {tuple(int(v) for v in p[:3]) for p in crops[i][body_mask(crops[i], True)]} for i in idx}
    union = set().union(*pals.values()) if pals else set()
    missing = {i: len(union - pals[i]) for i in idx}
    allowed = (body or set()) | wings | {outline}
    extra = {i: sorted("#%02x%02x%02x" % c for c in {tuple(int(v) for v in p[:3]) for p in arrs[i][arrs[i][..., 3] > 0]}
                       - allowed) for i in rest} if body is not None else {}
    ok = max(missing.values(), default=0) <= max_palette_diff and not any(extra.values())
    out.append(Check(id="body_palette", title=f"Corpo: mesma paleta (≤ {max_palette_diff} cor de diferença)", ok=ok,
                     detail=f"cores do corpo por pose: {[len(pals[i]) for i in idx]}; faltando vs união: {list(missing.values())}"
                     + (f"; cores fora do conjunto nas poses à parte: {extra}" if any(extra.values()) else ""),
                     data={"colors_per_pose": {i: len(pals[i]) for i in idx}, "missing_per_pose": missing,
                           "extra_colors": extra}))
    hp = list(head_poses) if head_poses is not None else idx
    offsets = head_offsets or {}
    heads = {}
    for i in dict.fromkeys(hp + list(offsets)):
        c = crops[i]
        m = masks[i] if i in masks else body_mask(c)
        rows = [r for r in range(m.shape[0]) if m[r].any()]
        if eye is not None:
            eye_rows = [r for r in range(c.shape[0]) if ((c[r, :, :3] == eye).all(axis=1) & (c[r, :, 3] > 0)).any()]
            sel = [eye_rows[0], eye_rows[0] + 1] if eye_rows else rows[:3]
        else:
            sel = rows[:3]
        heads[i] = max((int(m[r].sum()) for r in sel if r < m.shape[0]), default=0)
    ref = [heads[i] for i in hp]
    spread = max(ref, default=0) - min(ref, default=0)
    base = min(ref, default=0)
    off_bad = {i: heads[i] for i, (lo, hi) in offsets.items() if not base + lo <= heads[i] <= base + hi}
    detail = f"larguras: {heads}; ciclo {hp}: variação {spread} px"
    if offsets:
        detail += "; à parte: " + ", ".join(f"{i} em {base + lo}..{base + hi}" for i, (lo, hi) in offsets.items())
    out.append(Check(id="body_head", title=f"Corpo: largura da cabeça ± {head_tolerance} px", ok=spread <= head_tolerance
                     and not off_bad, detail=detail, data={"head_widths": heads, "out_of_range": off_bad}))
    return out


def check_nine_slice(img: Image.Image | np.ndarray, border: tuple[int, int, int, int],
                     tolerance: float = 1.5) -> Check:
    """9-slice (UI): border = (L, B, R, T) como no Unity. As margens cabem no canvas, e o miolo e as
    faixas das bordas repetem sem emenda (emenda ≤ limite, como na tileabilidade)."""
    arr = to_rgba(img)
    h, w = arr.shape[:2]
    L, B, R, T = border
    title = f"9-slice (L,B,R,T = {L},{B},{R},{T})"
    if min(border) < 0 or L + R >= w or B + T >= h:
        return Check(id="nine_slice", title=title, ok=False, detail=f"margens não cabem no canvas {w}x{h}")
    cx0, cx1, cy0, cy1 = L, w - R, T, h - B        # miolo em coordenadas de imagem (topo = T)
    regions = {
        "miolo": (arr[cy0:cy1, cx0:cx1], ["x", "y"]),
        "topo": (arr[:cy0, cx0:cx1], ["x"]),
        "base": (arr[cy1:, cx0:cx1], ["x"]),
        "esquerda": (arr[cy0:cy1, :cx0], ["y"]),
        "direita": (arr[cy0:cy1, cx1:], ["y"]),
    }
    bad, data = [], {}
    for nm, (reg, axes) in regions.items():
        if reg.size == 0:
            continue
        for ax in axes:
            s = seam_stats(reg, ax, tolerance)  # type: ignore[arg-type]
            data[f"{nm}_{ax}"] = s
            if not s["ok"]:
                bad.append(f"{nm} ({ax})")
    alpha_bad = int(((arr[..., 3] != 0) & (arr[..., 3] != 255)).sum())
    if alpha_bad:
        bad.append(f"{alpha_bad} px semitransparentes")
    return Check(id="nine_slice", title=title, ok=not bad,
                 detail="margens cabem; miolo e bordas repetem sem emenda" if not bad else "emenda em: " + ", ".join(bad),
                 data=data)


def _luminance(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def check_cumulative(stages: Sequence[Image.Image | np.ndarray], core_colors: Sequence[str] | None = None,
                     core_tolerance: float = 0.05) -> Check:
    """Estágios acumulativos (rachaduras, aceite §7): cada estágio contém todos os pixels do NÚCLEO
    ESCURO do anterior (continuam opacos e no núcleo). O reflexo claro pode mudar ou sumir entre estágios.
    core_colors: cores do núcleo; vazio = a(s) mais escura(s) de todos os estágios (luminância até
    `core_tolerance` acima da mais escura)."""
    arrs = [to_rgba(s) for s in stages]
    title = "Estágios acumulativos (núcleo escuro)"
    if len({a.shape for a in arrs}) > 1:
        return Check(id="cumulative", title=title, ok=False, detail="canvas diferentes entre estágios")
    if core_colors:
        core = {norm_hex(c) for c in core_colors}
    else:
        used = set().union(*(set(_colors(a)) for a in arrs))
        if not used:
            return Check(id="cumulative", title=title, ok=False, detail="estágios vazios")
        lum = {c: float(_luminance(np.array(hex_to_rgb(c)))) for c in used}
        darkest = min(lum.values())
        core = {c for c, v in lum.items() if v <= darkest + core_tolerance}

    def core_mask(a: np.ndarray) -> np.ndarray:
        m = np.zeros(a.shape[:2], bool)
        for c in core:
            m |= _color_mask(a, c)
        return m

    problems, lost_px = [], []
    for i, (a, b) in enumerate(zip(arrs, arrs[1:]), start=1):
        ca, cb = core_mask(a), core_mask(b)
        lost = ca & ~cb
        grew = int(((b[..., 3] > 0) & ~(a[..., 3] > 0)).sum()) + int((cb & ~ca).sum())
        if lost.any():
            problems.append(f"estágio {i + 1} perde {int(lost.sum())} px do núcleo do estágio {i}: "
                            + _fmt_tl(_tl(lost), int(lost.sum())))
            lost_px.append({"stage": i + 1, "px": _tl(lost)})
        elif grew == 0:
            problems.append(f"estágio {i + 1} igual ao {i}")
    return Check(id="cumulative", title=title, ok=not problems,
                 detail=(f"{len(arrs)} estágios, o núcleo de cada um contém o do anterior" if not problems
                         else "; ".join(problems)),
                 data={"coords": "top-left", "core_colors": sorted(core), "lost": lost_px})


# ---------------------------------------------------------------- tiles: autotile, parede x sólido, minério

def _lonely(arr: np.ndarray) -> np.ndarray:
    """Pixels opacos sem nenhum vizinho (8-vizinhança, dentro da imagem) opaco da MESMA cor."""
    op = arr[..., 3] > 0
    rgb = arr[..., :3].astype(np.int64)
    key = np.where(op, (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2], -1)
    h, w = op.shape
    pad = np.full((h + 2, w + 2), -2, np.int64)
    pad[1:-1, 1:-1] = key
    same = np.zeros_like(op)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                same |= pad[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx] == key
    return op & ~same


def _edge_depth(tile: np.ndarray, base: np.ndarray, side: str) -> np.ndarray:
    """Profundidade da borda por coluna (topo/baixo) ou por linha (esquerda/direita): quantos pixels
    seguidos, a partir do lado, diferem do tile base (crosta, aro). Pixel transparente conta como borda."""
    t, b = tile, base
    if side in ("left", "right"):
        t, b = t.transpose(1, 0, 2), b.transpose(1, 0, 2)
    if side in ("down", "right"):
        t, b = t[::-1], b[::-1]
    diff = (t != b).any(axis=2)
    n = diff.shape[0]
    out = np.zeros(diff.shape[1], int)
    for x in range(diff.shape[1]):
        k = 0
        while k < n and diff[k, x]:
            k += 1
        out[x] = k
    return out


def _set_tile_size(tiles: dict[int, np.ndarray]) -> int:
    """Tamanho do tile de um conjunto: o do base (15); sem base, o mais comum; vazio = 16."""
    from .autotile import BASE_MASK, TILE

    if BASE_MASK in tiles:
        return int(tiles[BASE_MASK].shape[1])
    sizes = [int(a.shape[1]) for a in tiles.values() if a.shape[0] == a.shape[1]]
    return max(set(sizes), key=sizes.count) if sizes else TILE


def check_autotile(tiles_by_mask: dict[int, Image.Image | np.ndarray], required: Sequence[int] | None = None,
                   tolerance: float = 1.5, grid: Sequence[str] | None = None, max_orphans: int = 0,
                   tile_size: int | None = None) -> list[Check]:
    """Coerência de um conjunto de autotile (aceite §B), máscara cardinal de 4 bits (ver autotile.py).

    - autotile_set [elim]: todas as máscaras de `required` presentes (padrão: as 16; ou a lista mínima);
    - autotile_canvas [elim]: todas do tamanho do tile (`tile_size`; vazio = o do base 15: 16x16 nos tiles
      entregues, 11x11 nos blocos novos); autotile_alpha [elim]: alfa binário e o base (15) opaco;
    - autotile_seams [elim]: para cada par de máscaras que podem ser vizinhas (a com o lado sólido virado
      para b, e a MESMA exposição no eixo perpendicular; senão é um degrau legítimo do terreno), a emenda
      entre a última coluna/linha de a e a primeira de b fica ≤ o limite de tileabilidade (seam_stats) do
      tile "canônico" com aquela exposição (ex.: topo|topo mede contra o próprio topo, máscara 14); o
      base e cada canônico também têm de ser tileáveis no eixo;
    - autotile_map [elim]: monta `render_test_map` (12x8 com buracos; só quando todas as máscaras da
      grade existem) e confere: sem ilha opaca < 2 px; sem "degrau" (a profundidade da crosta/aro muda,
      ao atravessar a junção, no máximo o que já muda dentro do tile canônico, mín. 1 px); sem pixel
      órfão criado pela junção (sem vizinho da mesma cor no mapa, mas com vizinho quando cada vizinho
      conferido é trocado pelo tile canônico da junção e o resto pelo base)."""
    from .autotile import BASE_MASK, DEFAULT_TEST_GRID, DOWN, LEFT, RIGHT, UP, masks_of_grid, render_test_map

    tiles = {int(k): to_rgba(v) for k, v in tiles_by_mask.items()}
    TILE = tile_size or _set_tile_size(tiles)
    req = sorted({int(m) for m in (required if required is not None else range(16))})
    out: list[Check] = []
    missing = [m for m in req if m not in tiles]
    out.append(Check(id="autotile_set", title=f"Conjunto de máscaras ({len(req)})", ok=not missing,
                     detail=(f"{len(tiles)} variantes; faltam: {missing}" if missing else
                             f"{len(req)} máscaras presentes: {req}"),
                     data={"present": sorted(tiles), "required": req, "missing": missing}))
    bad_size = {m: [a.shape[1], a.shape[0]] for m, a in tiles.items() if a.shape[:2] != (TILE, TILE)}
    out.append(Check(id="autotile_canvas", title=f"Todas {TILE}x{TILE}", ok=not bad_size,
                     detail=f"todas {TILE}x{TILE}" if not bad_size else f"tamanhos errados: {bad_size}",
                     data={"wrong": bad_size, "tile_size": TILE}))
    semi = {m: int(((a[..., 3] != 0) & (a[..., 3] != 255)).sum()) for m, a in tiles.items()}
    semi = {m: n for m, n in semi.items() if n}
    base_tr = int((tiles[BASE_MASK][..., 3] == 0).sum()) if BASE_MASK in tiles else 0
    probs: list[str] = []
    if semi:
        probs.append(f"semitransparência em {semi}")
    if base_tr:
        probs.append(f"base (15) com {base_tr} px transparentes")
    out.append(Check(id="autotile_alpha", title="Alfa binário, base opaco", ok=not probs,
                     detail="alfa 0/255 em todas; base opaco" if not probs else "; ".join(probs),
                     data={"semi": semi, "base_transparent": base_tr}))
    if bad_size or BASE_MASK not in tiles:
        why = "não avaliado: " + ("canvas errado" if bad_size else "falta o base (15)")
        out.append(_skip("autotile_seams", "Junções sem emenda", why))
        out.append(_skip("autotile_map", "Mapa de teste coerente", why))
        return out

    # ---- emendas entre pares vizinhos
    seam_bad: list[str] = []
    pairs = 0
    limits: dict[tuple[str, int], dict[str, Any] | None] = {}

    def canon_stats(axis: str, c: int) -> dict[str, Any] | None:
        if (axis, c) not in limits:
            limits[(axis, c)] = seam_stats(tiles[c], axis, tolerance) if c in tiles else None  # type: ignore[arg-type]
        return limits[(axis, c)]

    for axis in ("x", "y"):
        perp = (UP | DOWN) if axis == "x" else (LEFT | RIGHT)
        near, far = (RIGHT, LEFT) if axis == "x" else (DOWN, UP)
        for c in sorted({(m & perp) | near | far for m in tiles}):
            st = canon_stats(axis, c)
            if st is not None and not st["ok"]:
                seam_bad.append(f"canônico {c:02d} não tileável em {axis} (emenda {st['seam']:.1f}, "
                                f"limite {st['limit']:.1f}, borda {st['edge']:.1f})")
        for a, ta in tiles.items():
            if not a & near:
                continue
            for b, tb in tiles.items():
                if not b & far or (a & perp) != (b & perp):
                    continue
                st = canon_stats(axis, (a & perp) | near | far)
                if st is None:
                    continue
                pairs += 1
                ea = ta[:, -1] if axis == "x" else ta[-1, :]
                eb = tb[:, 0] if axis == "x" else tb[0, :]
                both = (ea[:, 3] > 0) & (eb[:, 3] > 0)
                if not both.any():
                    continue
                d = float(np.linalg.norm(ea[both, :3].astype(np.float64) - eb[both, :3].astype(np.float64),
                                         axis=1).mean())
                if d > float(st["limit"]):
                    seam_bad.append(f"{a:02d}|{b:02d} ({axis}): emenda {d:.1f} > {st['limit']:.1f}")
    out.append(Check(id="autotile_seams", title="Junções sem emenda", ok=not seam_bad,
                     detail=(f"{pairs} pares vizinhos conferidos, todos dentro do limite do tile canônico"
                             if not seam_bad else "; ".join(seam_bad[:MAX_LISTED])
                             + (f" … (+{len(seam_bad) - MAX_LISTED})" if len(seam_bad) > MAX_LISTED else "")),
                     data={"pairs": pairs, "problems": seam_bad}))

    # ---- mapa de teste
    g = list(grid) if grid is not None else list(DEFAULT_TEST_GRID)
    masks = masks_of_grid(g)
    if not all(int(m) in tiles for m in masks.ravel() if m >= 0):
        out.append(_skip("autotile_map", "Mapa de teste coerente",
                         "conjunto parcial: o mapa de teste precisa de todas as máscaras da grade", elim=False))
        return out
    img = to_rgba(render_test_map(tiles, g))
    rows, cols = masks.shape
    probs = []
    small = [c for c in components(img[..., 3] > 0, 8) if len(c) < 2]
    if small:
        probs.append(f"{len(small)} pixel(s) opaco(s) isolado(s) no mapa")
    base = tiles[BASE_MASK]
    steps: list[str] = []
    for side, bit in (("up", UP), ("down", DOWN), ("left", LEFT), ("right", RIGHT)):
        horiz = side in ("up", "down")
        canon = bit ^ 15
        if canon not in tiles:
            continue
        pc = _edge_depth(tiles[canon], base, side)
        allowed = max(1, int(np.abs(np.diff(np.r_[pc, pc[:1]])).max()))
        for r in range(rows):
            for c in range(cols):
                a = int(masks[r, c])
                rr, cc = (r, c + 1) if horiz else (r + 1, c)
                if a < 0 or rr >= rows or cc >= cols:
                    continue
                b = int(masks[rr, cc])
                if b < 0 or (a & bit) or (b & bit):
                    continue
                jump = abs(int(_edge_depth(tiles[a], base, side)[-1]) - int(_edge_depth(tiles[b], base, side)[0]))
                if jump > allowed:
                    steps.append(f"{side} em ({c},{r})→({cc},{rr}): {jump} px > {allowed}")
    if steps:
        probs.append(f"{len(steps)} degrau(s): " + "; ".join(steps[:6]))
    lonely_map = _lonely(img)
    orphans: list[list[int]] = []
    for r in range(rows):
        for c in range(cols):
            m = int(masks[r, c])
            if m < 0:
                continue
            border = np.zeros((TILE, TILE), bool)
            # referência: a variante cercada pelo base, com o tile canônico de cada junção conferida no lugar
            # do vizinho (a junção "ideal" é a emenda do canônico); as pontas de cada lado ficam de fora
            # (encostam na diagonal, que pode ser ar no mapa)
            ref = np.tile(base, (3, 3, 1))
            ref[TILE:2 * TILE, TILE:2 * TILE] = tiles[m]
            inner = slice(1, -1)
            for side, (dr, dc), sl in (("right", (0, 1), (inner, -1)), ("left", (0, -1), (inner, 0)),
                                       ("down", (1, 0), (-1, inner)), ("up", (-1, 0), (0, inner))):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < rows and 0 <= cc < cols) or masks[rr, cc] < 0:
                    continue
                horiz = side in ("left", "right")
                perp = (UP | DOWN) if horiz else (LEFT | RIGHT)
                if (m & perp) != (int(masks[rr, cc]) & perp):
                    continue
                canon = (m & perp) | ((LEFT | RIGHT) if horiz else (UP | DOWN))
                if canon not in tiles:
                    continue
                border[sl] = True
                ref[(1 + dr) * TILE:(2 + dr) * TILE, (1 + dc) * TILE:(2 + dc) * TILE] = tiles[canon]
            if not border.any():
                continue
            ref_lonely = _lonely(ref)[TILE:2 * TILE, TILE:2 * TILE]
            cell = lonely_map[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE]
            ys, xs = np.nonzero(cell & ~ref_lonely & border)
            orphans += [[int(c * TILE + x), int(r * TILE + y)] for y, x in zip(ys, xs)]
    if len(orphans) > max_orphans:
        probs.append(f"{len(orphans)} pixel(s) órfão(s) criado(s) na junção: {_fmt_tl(orphans, len(orphans))}")
    n_masks = len({int(m) for m in masks.ravel() if m >= 0})
    out.append(Check(id="autotile_map", title="Mapa de teste coerente (sem degrau nem pixel solto)", ok=not probs,
                     detail=(f"mapa {cols}x{rows} com {n_masks} máscaras: sem ilha, degrau ou órfão" if not probs
                             else "; ".join(probs)),
                     data={"coords": "top-left", "islands": len(small), "steps": steps, "orphan_px": orphans[:256],
                           "grid": g}))
    return out


def autotile_report(tiles_by_mask: dict[int, Image.Image | np.ndarray], *, required: Sequence[int] | None = None,
                    name: str | None = None, **kw: Any) -> Report:
    """`check_autotile` embrulhado num Report (spec "autotile"); canvas = tamanho do tile do conjunto."""
    checks = check_autotile(tiles_by_mask, required=required, **kw)
    canv = next((c.data.get("tile_size") for c in checks if c.id == "autotile_canvas"), 16)
    return Report.build(checks, spec="autotile", kind="tile", file=name, canvas=(canv, canv))


def tile_stats(img: Image.Image | np.ndarray) -> dict[str, float]:
    """Luminância média (L do OKLab), contraste interno (desvio-padrão de L) e saturação média (croma do
    OKLab) dos pixels opacos."""
    from .palette import rgb_to_oklab

    a = to_rgba(img)
    op = a[..., 3] > 0
    if not op.any():
        return {"luminance": 0.0, "contrast": 0.0, "chroma": 0.0}
    lab = rgb_to_oklab(a[op, :3])
    return {"luminance": round(float(lab[:, 0].mean()), 4), "contrast": round(float(lab[:, 0].std()), 4),
            "chroma": round(float(np.hypot(lab[:, 1], lab[:, 2]).mean()), 4)}


def check_wall_contrast(solid: Image.Image | np.ndarray, wall: Image.Image | np.ndarray) -> Check:
    """Parede de fundo x tile sólido do mesmo material (aceite §B, legibilidade) [elim]: a parede tem
    luminância média MENOR, contraste interno MENOR e saturação (croma) MENOR que o sólido."""
    s, w = tile_stats(solid), tile_stats(wall)
    names = {"luminance": "luminância média", "contrast": "contraste interno", "chroma": "saturação"}
    probs = [f"{label} da parede {w[k]:.3f} ≥ sólido {s[k]:.3f}" for k, label in names.items() if not w[k] < s[k]]
    detail = ", ".join(f"{names[k]} {w[k]:.3f} < {s[k]:.3f}" for k in names) if not probs else "; ".join(probs)
    return Check(id="wall_contrast", title="Parede mais escura e apagada que o sólido", ok=not probs,
                 detail=detail, data={"solid": s, "wall": w})


def check_ore_on_base(ore_tile: Image.Image | np.ndarray, base_tile: Image.Image | np.ndarray,
                      min_shared: float = 0.60) -> Check:
    """Minério desenhado SOBRE a pedra (aceite §B) [elim]: o tile do minério é opaco, tem o canvas do
    base e ≥ 60% dos pixels idênticos aos do tile base (o resto é o minério e a sua sombra)."""
    o, b = to_rgba(ore_tile), to_rgba(base_tile)
    title = f"Minério sobre o base (opaco, ≥ {round(min_shared * 100)}% igual)"
    if o.shape != b.shape:
        return Check(id="ore_on_base", title=title, ok=False,
                     detail=f"canvas diferentes: {o.shape[1]}x{o.shape[0]} × {b.shape[1]}x{b.shape[0]}")
    tr = int((o[..., 3] == 0).sum())
    same = (o == b).all(axis=2) & (o[..., 3] > 0)
    ratio = float(same.sum()) / (o.shape[0] * o.shape[1])
    probs = []
    if tr:
        probs.append(f"{tr} px transparentes (minério flutuando)")
    if ratio < min_shared:
        probs.append(f"só {ratio * 100:.0f}% dos pixels iguais ao base (mínimo {min_shared * 100:.0f}%)")
    return Check(id="ore_on_base", title=title, ok=not probs,
                 detail=f"opaco, {ratio * 100:.0f}% dos pixels iguais ao base" if not probs else "; ".join(probs),
                 data={"shared": round(ratio, 3), "transparent": tr, "coords": "top-left", "ore_px": _tl(~same)})


# ---------------------------------------------------------------- fundos / parallax

def check_tileable_x(img: Image.Image | np.ndarray, tolerance: float = 1.5, percentile: float = 99.0) -> Check:
    """Repetição horizontal de uma camada larga (parallax) [elim]: a emenda (coluna última × 0) não pode
    destoar das fronteiras internas. Diferença por par de colunas = média, por linha, da distância RGBA
    (o alfa conta 255 quando um lado é transparente e o outro não). Limite = max(tolerância × média
    interna, percentil `percentile` das fronteiras internas) + 1: num fundo largo há silhuetas com
    bordas fortes, então a referência é "uma fronteira interna comum", não a maior. Também falha se a
    1ª ou a última coluna destoa das vizinhas (linha de cor diferente na borda)."""
    a = to_rgba(img).astype(np.float64)
    title = "Repete na horizontal sem emenda"
    if a.shape[1] < 4:
        return _skip("tileable_x", title, "imagem estreita demais")
    v = np.concatenate([a[..., :3] * (a[..., 3:4] > 0), (a[..., 3:4] > 0) * 255.0], axis=2)
    inner = np.linalg.norm(v[:, 1:] - v[:, :-1], axis=2).mean(axis=0)
    seam = float(np.linalg.norm(v[:, -1] - v[:, 0], axis=1).mean())
    core = inner[1:-1]
    limit = max(tolerance * float(core.mean()), float(np.percentile(core, percentile))) + 1.0
    edge = float(max(inner[0], inner[-1]))
    rank = float((core < seam).mean() * 100)
    alpha_mis = int(((a[:, -1, 3] > 0) != (a[:, 0, 3] > 0)).sum())
    ok = seam <= limit and edge <= limit
    detail = (f"emenda {seam:.2f} ≤ limite {limit:.2f} (média interna {float(core.mean()):.2f}; a emenda é maior "
              f"que {rank:.0f}% das fronteiras internas)") if ok else (
        f"emenda {seam:.2f} > limite {limit:.2f}" if seam > limit else f"linha de cor diferente na borda (dif. {edge:.2f})")
    return Check(id="tileable_x", title=title, ok=ok, detail=detail,
                 data={"seam": round(seam, 3), "interior_mean": round(float(core.mean()), 3), "limit": round(limit, 3),
                       "edge": round(edge, 3), "seam_rank_pct": round(rank, 1), "alpha_mismatch_rows": alpha_mis})


PARALLAX_KEYS = ("luminance", "contrast", "chroma")


def check_parallax_stack(layers: Sequence[Image.Image | np.ndarray] | dict[str, Image.Image | np.ndarray],
                         solid_tiles: Sequence[Image.Image | np.ndarray],
                         walls: Sequence[Image.Image | np.ndarray] | None = None) -> Check:
    """Pilha de parallax x tiles sólidos (a ideia do check_wall_contrast levada ao fundo) [elim].

    `layers` do mais DISTANTE ao mais PRÓXIMO (lista, ou dict nome -> imagem na ordem). Medidas de
    `tile_stats` nos pixels opacos: luminância média (L do OKLab), contraste interno (desvio-padrão de L)
    e saturação (croma médio do OKLab). Passa quando:
      1. toda camada tem luminância, contraste e croma MENORES que os de TODOS os tiles sólidos dados
         (o limite é o menor valor entre eles), para o plano de jogo ler à frente;
      2. o contraste cresce estritamente do distante ao próximo (o distante é o mais apagado/
         atmosférico, o próximo o mais contrastado) e o croma não diminui (cada camada ≥ a anterior;
         afrouxado de "estrito": margens de 0,001 seriam ruído, não critério visual).
    `walls` (opcional, só informativo): as paredes de fundo, que ficam ENTRE o parallax e o plano de jogo."""
    named = list(layers.items()) if isinstance(layers, dict) else [(f"camada {i}", l) for i, l in enumerate(layers)]
    title = "Pilha de parallax: mais apagada que o sólido, distante < próximo"
    if not named or not solid_tiles:
        return Check(id="parallax_stack", title=title, ok=False, detail="faltam camadas ou tiles sólidos")
    stats = {n: tile_stats(l) for n, l in named}
    solid = [tile_stats(t) for t in solid_tiles]
    limit = {k: min(s[k] for s in solid) for k in PARALLAX_KEYS}
    labels = {"luminance": "luminância", "contrast": "contraste", "chroma": "croma"}
    probs: list[str] = []
    for n, s in stats.items():
        for k in PARALLAX_KEYS:
            if not s[k] < limit[k]:
                probs.append(f"{n}: {labels[k]} {s[k]:.4f} ≥ sólido {limit[k]:.4f}")
    for k in ("contrast", "chroma"):
        seq = [stats[n][k] for n, _ in named]
        for (na, a), (nb, b) in zip(zip([n for n, _ in named], seq), zip([n for n, _ in named][1:], seq[1:])):
            # contraste: estritamente crescente; croma: não decrescente (aceite-fundos de 13/10, resposta 4)
            if not (a < b if k == "contrast" else a <= b):
                probs.append(f"{labels[k]} {'não cresce' if k == 'contrast' else 'diminui'} de {na} ({a:.4f}) "
                             f"para {nb} ({b:.4f})")
    margins = {n: {k: round(limit[k] - s[k], 4) for k in PARALLAX_KEYS} for n, s in stats.items()}
    data: dict[str, Any] = {"layers": stats, "solid_limit": limit, "solid": solid, "margins": margins,
                            "order": [n for n, _ in named]}
    if walls:
        data["walls"] = [tile_stats(w) for w in walls]
    detail = ("; ".join(f"{n}: L {s['luminance']:.3f} / contraste {s['contrast']:.4f} / croma {s['chroma']:.4f}"
                        for n, s in stats.items())
              + f" < sólido L {limit['luminance']:.3f} / {limit['contrast']:.4f} / {limit['chroma']:.4f}") \
        if not probs else "; ".join(probs)
    return Check(id="parallax_stack", title=title, ok=not probs, detail=detail, data=data)
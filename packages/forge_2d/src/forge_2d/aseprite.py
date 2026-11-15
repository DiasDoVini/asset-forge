"""Leitura e escrita do formato binário do Aseprite (.aseprite / .ase), sem depender do Aseprite.

Referência: https://github.com/aseprite/aseprite/blob/main/docs/ase-file-specs.md

Leitura: RGBA, tons de cinza e indexado (paleta + índice transparente); camadas (visibilidade, grupos, opacidade,
modos de mistura mais comuns), cels raw/comprimidos/ligados, camadas de tilemap com tilesets embutidos, tags (com
direção e repetições), durações por frame e fatias (retângulo, centro de 9-slice e pivô). Camadas de referência e
tilesets externos são pulados.
Cada frame vem composto numa imagem RGBA do tamanho do canvas; `layers=True` devolve também cada camada à parte.

Escrita: RGBA, uma camada por vez (ou várias, com nome), um cel comprimido por frame/camada, tags e durações.
O que se grava é lido de volta por este módulo e pelo Aseprite.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from PIL import Image

MAGIC_FILE = 0xA5E0
MAGIC_FRAME = 0xF1FA

DIRECTIONS = ["forward", "reverse", "pingpong", "pingpong_reverse"]  # 0..3
BLEND_NAMES = ["normal", "multiply", "screen", "overlay", "darken", "lighten", "color_dodge", "color_burn", "hard_light",
               "soft_light", "difference", "exclusion", "hue", "saturation", "color", "luminosity", "addition",
               "subtract", "divide"]


class AsepriteError(ValueError):
    pass


@dataclass
class AseLayer:
    name: str
    flags: int
    type: int            # 0 normal, 1 grupo, 2 tilemap
    child_level: int
    blend: int
    opacity: int
    tileset: int | None = None   # camadas de tilemap
    effects: list[dict[str, Any]] = field(default_factory=list)   # efeitos do editor do Asset Forge
    clip: bool = False           # máscara de recorte (só onde a camada de baixo tem pixels)

    @property
    def visible(self) -> bool:
        return bool(self.flags & 1)

    @property
    def reference(self) -> bool:
        return bool(self.flags & 64)


@dataclass
class AseCel:
    layer: int
    x: int
    y: int
    opacity: int
    z: int
    image: Image.Image | None      # RGBA (tilemaps: renderizado a partir do tileset no fim da leitura)
    link: int | None = None        # frame de onde copiar (cel ligado)
    tilemap: tuple[int, int, list[int]] | None = None   # (colunas, linhas, índices) em cels de tilemap


@dataclass
class AseTag:
    name: str
    from_: int
    to: int
    direction: str = "forward"
    repeat: int = 0


@dataclass
class AseFile:
    width: int
    height: int
    color_depth: int
    layers: list[AseLayer] = field(default_factory=list)
    frames: list[list[AseCel]] = field(default_factory=list)   # cels de cada frame
    durations: list[int] = field(default_factory=list)         # ms
    tags: list[AseTag] = field(default_factory=list)
    palette: list[tuple[int, int, int, int]] = field(default_factory=list)
    slices: list[dict[str, Any]] = field(default_factory=list)
    tilesets: dict[int, tuple[int, int, list[Image.Image]]] = field(default_factory=dict)  # id -> (tw, th, tiles)
    grid: tuple[int, int, int, int] = (0, 0, 16, 16)
    pixel_ratio: tuple[int, int] = (1, 1)
    skipped: list[str] = field(default_factory=list)           # o que não foi lido (tilemaps...)

    # ---- composição -----------------------------------------------------
    def parents(self, idx: int) -> list[int]:
        """Índices dos grupos acima da camada. No arquivo o grupo vem ANTES dos filhos (nível + 1)."""
        out = []
        level = self.layers[idx].child_level
        for j in range(idx - 1, -1, -1):
            o = self.layers[j]
            if o.type == 1 and o.child_level == level - 1:
                out.append(j)
                level = o.child_level
                if level == 0:
                    break
        return out

    def _layer_visible(self, idx: int) -> bool:
        """Visível se ela e todos os grupos acima dela estão visíveis."""
        return self.layers[idx].visible and all(self.layers[j].visible for j in self.parents(idx))

    def resolved_cels(self, frame: int) -> list[AseCel]:
        """Cels do frame com os ligados já resolvidos para a imagem original."""
        out = []
        for c in self.frames[frame]:
            if c.link is not None:
                src = next((s for s in self.frames[c.link] if s.layer == c.layer and s.link is None), None)
                if src is None or src.image is None:
                    continue
                c = AseCel(c.layer, src.x, src.y, c.opacity, c.z, src.image)
            if c.image is not None:
                out.append(c)
        return out

    def _placed(self, c: AseCel) -> np.ndarray:
        """Cel no tamanho do canvas (uint8 RGBA), com a opacidade do cel aplicada."""
        out = np.zeros((self.height, self.width, 4), np.uint8)
        arr = np.asarray(c.image.convert("RGBA"), np.uint8)
        h, w = arr.shape[:2]
        x0, y0 = max(c.x, 0), max(c.y, 0)
        x1, y1 = min(c.x + w, self.width), min(c.y + h, self.height)
        if x1 > x0 and y1 > y0:
            out[y0:y1, x0:x1] = arr[y0 - c.y:y1 - c.y, x0 - c.x:x1 - c.x]
        if c.opacity < 255:
            out[..., 3] = (out[..., 3].astype(np.uint16) * c.opacity // 255).astype(np.uint8)
        return out

    def render_frame(self, frame: int, include_hidden: bool = False, only_layers: set[int] | None = None) -> Image.Image:
        from .effects import apply_effects

        canvas = np.zeros((self.height, self.width, 4), np.float32)
        by_layer = {c.layer: c for c in self.resolved_cels(frame)}
        clip_base: np.ndarray | None = None
        for li, lay in enumerate(self.layers):
            if lay.type == 1 or lay.reference:
                continue
            c = by_layer.get(li)
            visible = (include_hidden or self._layer_visible(li)) and (only_layers is None or li in only_layers)
            data = apply_effects(self._placed(c), lay.effects) if c is not None else None
            if not lay.clip:
                clip_base = data if (visible and data is not None) else None
            elif data is not None:
                if clip_base is None:
                    data = None
                else:
                    data = data.copy()
                    data[..., 3] = (data[..., 3].astype(np.uint16) * clip_base[..., 3] // 255).astype(np.uint8)
            if not visible or data is None:
                continue
            src = data.astype(np.float32) / 255.0
            group_op = float(np.prod([self.layers[j].opacity / 255.0 for j in self.parents(li)])) if lay.child_level else 1.0
            src[..., 3] *= (lay.opacity / 255.0) * group_op
            canvas = _blend(canvas, src, lay.blend)
        return Image.fromarray(np.clip(canvas * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def _blend(dst: np.ndarray, src: np.ndarray, mode: int) -> np.ndarray:
    """Composição premultiplicada-correta de src sobre dst (floats 0..1) com o modo de mistura do Aseprite."""
    sa, da = src[..., 3:4], dst[..., 3:4]
    sc, dc = src[..., :3], dst[..., :3]
    name = BLEND_NAMES[mode] if 0 <= mode < len(BLEND_NAMES) else "normal"
    if name == "multiply":
        b = sc * dc
    elif name == "screen":
        b = sc + dc - sc * dc
    elif name == "overlay":
        b = np.where(dc <= 0.5, 2 * sc * dc, 1 - 2 * (1 - sc) * (1 - dc))
    elif name == "hard_light":
        b = np.where(sc <= 0.5, 2 * sc * dc, 1 - 2 * (1 - sc) * (1 - dc))
    elif name == "darken":
        b = np.minimum(sc, dc)
    elif name == "lighten":
        b = np.maximum(sc, dc)
    elif name == "difference":
        b = np.abs(dc - sc)
    elif name == "exclusion":
        b = sc + dc - 2 * sc * dc
    elif name == "addition":
        b = np.minimum(1.0, sc + dc)
    elif name == "subtract":
        b = np.maximum(0.0, dc - sc)
    elif name == "color_dodge":
        b = np.where(sc >= 1, 1.0, np.minimum(1.0, dc / np.maximum(1e-6, 1 - sc)))
    elif name == "color_burn":
        b = np.where(sc <= 0, 0.0, 1 - np.minimum(1.0, (1 - dc) / np.maximum(1e-6, sc)))
    elif name == "divide":
        b = np.where(sc <= 0, 1.0, np.minimum(1.0, dc / np.maximum(1e-6, sc)))
    else:  # normal (e hue/sat/cor/luminosidade, que ficam como normal)
        b = sc
    ra = sa + da * (1 - sa)
    # onde há fundo, a mistura entra proporcional à opacidade do fundo; onde não há, vale a cor da fonte
    mixed = (1 - da) * sc + da * b
    rc = np.where(ra > 0, (sa * mixed + (1 - sa) * da * dc) / np.maximum(ra, 1e-6), 0.0)
    return np.concatenate([rc, ra], axis=-1)


# ---------------------------------------------------------------- leitura

class _R:
    def __init__(self, data: bytes) -> None:
        self.d = data
        self.p = 0

    def u8(self) -> int:
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def skip(self, n: int) -> None:
        self.p += n

    def string(self) -> str:
        n = self.u16()
        s = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def bytes(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]
        self.p += n
        return b


def _pixels_to_rgba(raw: bytes, w: int, h: int, depth: int, palette: list[tuple[int, int, int, int]],
                    transparent: int) -> Image.Image:
    if depth == 32:
        arr = np.frombuffer(raw, np.uint8, count=w * h * 4).reshape(h, w, 4)
        return Image.fromarray(arr.copy(), "RGBA")
    if depth == 16:
        arr = np.frombuffer(raw, np.uint8, count=w * h * 2).reshape(h, w, 2)
        rgba = np.stack([arr[..., 0], arr[..., 0], arr[..., 0], arr[..., 1]], axis=-1)
        return Image.fromarray(rgba.copy(), "RGBA")
    if depth == 8:
        idx = np.frombuffer(raw, np.uint8, count=w * h).reshape(h, w)
        pal = np.zeros((256, 4), np.uint8)
        for i, c in enumerate(palette[:256]):
            pal[i] = c
        rgba = pal[idx].copy()
        rgba[idx == transparent, 3] = 0
        return Image.fromarray(rgba, "RGBA")
    raise AsepriteError(f"profundidade de cor não suportada: {depth}")


def read_aseprite(src: str | Path | bytes | BinaryIO) -> AseFile:
    """Lê um .aseprite/.ase. Levanta AsepriteError se não for um arquivo válido."""
    if isinstance(src, (str, Path)):
        data = Path(src).read_bytes()
    elif isinstance(src, bytes):
        data = src
    else:
        data = src.read()
    if len(data) < 128:
        raise AsepriteError("arquivo curto demais para ser um .aseprite")
    r = _R(data)
    r.u32()  # tamanho do arquivo
    if r.u16() != MAGIC_FILE:
        raise AsepriteError("não é um arquivo Aseprite (assinatura 0xA5E0 ausente)")
    n_frames, w, h, depth = r.u16(), r.u16(), r.u16(), r.u16()
    flags = r.u32()
    r.u16()          # velocidade (obsoleta)
    r.skip(8)
    transparent = r.u8()
    r.skip(3)
    ncolors = r.u16()
    pw, ph = r.u8(), r.u8()
    gx, gy, gw, gh = r.i16(), r.i16(), r.u16(), r.u16()
    r.skip(84)
    if depth not in (8, 16, 32):
        raise AsepriteError(f"profundidade de cor inválida: {depth}")
    ase = AseFile(w, h, depth, pixel_ratio=(pw or 1, ph or 1))
    if gw and gh:
        ase.grid = (gx, gy, gw, gh)
    prev_chunk = 0
    if depth == 8 and ncolors:
        ase.palette = [(0, 0, 0, 255)] * ncolors
    layer_opacity_valid = bool(flags & 1)
    r.p = 128
    new_palette = False
    for fi in range(n_frames):
        fstart = r.p
        fsize = r.u32()
        if r.u16() != MAGIC_FRAME:
            raise AsepriteError(f"frame {fi}: assinatura 0xF1FA ausente")
        old_chunks = r.u16()
        duration = r.u16()
        r.skip(2)
        new_chunks = r.u32()
        nchunks = new_chunks or old_chunks
        ase.durations.append(duration or 100)
        ase.frames.append([])
        for _ in range(nchunks):
            cstart = r.p
            csize = r.u32()
            ctype = r.u16()
            cend = cstart + csize
            if ctype == 0x2004:  # layer
                lflags, ltype, level = r.u16(), r.u16(), r.u16()
                r.skip(4)
                blend = r.u16()
                opacity = r.u8()
                r.skip(3)
                name = r.string()
                ts_index = r.u32() if ltype == 2 else None
                ase.layers.append(AseLayer(name, lflags, ltype, level, blend, opacity if layer_opacity_valid else 255, ts_index))
            elif ctype == 0x2005:  # cel
                li, x, y, op, ctp, z = r.u16(), r.i16(), r.i16(), r.u8(), r.u16(), r.i16()
                r.skip(5)
                if ctp == 1:
                    ase.frames[fi].append(AseCel(li, x, y, op, z, None, link=r.u16()))
                elif ctp in (0, 2):
                    cw, ch = r.u16(), r.u16()
                    body = r.bytes(cend - r.p)
                    raw = zlib.decompress(body) if ctp == 2 else body
                    bpp = {32: 4, 16: 2, 8: 1}[depth]
                    if len(raw) < cw * ch * bpp:
                        raise AsepriteError(f"frame {fi}: cel truncado")
                    img = _pixels_to_rgba(raw, cw, ch, depth, ase.palette, transparent) if cw and ch else None
                    ase.frames[fi].append(AseCel(li, x, y, op, z, img))
                elif ctp == 3:
                    tw_, th_, bits = r.u16(), r.u16(), r.u16()
                    id_mask = r.u32()
                    r.skip(12 + 10)
                    if bits != 32:
                        ase.skipped.append(f"tilemap com {bits} bits por tile (frame {fi})")
                    else:
                        raw = zlib.decompress(r.bytes(cend - r.p))
                        ids = [v & id_mask for v in struct.unpack_from(f"<{tw_ * th_}I", raw)]
                        ase.frames[fi].append(AseCel(li, x, y, op, z, None, tilemap=(tw_, th_, ids)))
                else:
                    ase.skipped.append(f"cel de tipo {ctp} (camada {li}, frame {fi})")
            elif ctype == 0x2023:  # tileset
                ts_id, ts_flags, count = r.u32(), r.u32(), r.u32()
                tw_, th_ = r.u16(), r.u16()
                r.skip(2 + 14)
                ts_name = r.string()
                if ts_flags & 1:
                    r.skip(8)
                if ts_flags & 2:
                    zlen = r.u32()
                    raw = zlib.decompress(r.bytes(zlen))
                    sheet = _pixels_to_rgba(raw, tw_, th_ * count, depth, ase.palette, transparent) if count else None
                    tiles = [sheet.crop((0, i * th_, tw_, (i + 1) * th_)) for i in range(count)] if sheet else []
                    ase.tilesets[ts_id] = (tw_, th_, tiles)
                else:
                    ase.skipped.append(f"tileset externo \"{ts_name}\"")
            elif ctype == 0x2018:  # tags
                n = r.u16()
                r.skip(8)
                for _t in range(n):
                    a, b, d, rep = r.u16(), r.u16(), r.u8(), r.u16()
                    r.skip(6 + 3 + 1)
                    ase.tags.append(AseTag(r.string(), a, b, DIRECTIONS[d] if d < 4 else "forward", rep))
            elif ctype == 0x2019:  # paleta
                total = r.u32()
                first, last = r.u32(), r.u32()
                r.skip(8)
                if len(ase.palette) < total:
                    ase.palette += [(0, 0, 0, 255)] * (total - len(ase.palette))
                for i in range(first, last + 1):
                    eflags = r.u16()
                    rr, gg, bb, aa = r.u8(), r.u8(), r.u8(), r.u8()
                    if eflags & 1:
                        r.string()
                    ase.palette[i] = (rr, gg, bb, aa)
                new_palette = True
            elif ctype in (0x0004, 0x0011) and depth == 8 and not new_palette:
                packets = r.u16()
                pos = 0
                for _p in range(packets):
                    pos += r.u8()
                    n = r.u8() or 256
                    for i in range(n):
                        rr, gg, bb = r.u8(), r.u8(), r.u8()
                        if ctype == 0x0011:
                            rr, gg, bb = (rr * 255) // 63, (gg * 255) // 63, (bb * 255) // 63
                        while len(ase.palette) <= pos + i:
                            ase.palette.append((0, 0, 0, 255))
                        ase.palette[pos + i] = (rr, gg, bb, 255)
                    pos += n
            elif ctype == 0x2022:  # fatias
                n = r.u32()
                flags_s = r.u32()
                r.skip(4)
                name = r.string()
                for _k in range(n):
                    fno = r.u32()
                    sx, sy = struct.unpack_from("<ii", r.d, r.p)
                    r.skip(8)
                    sw, sh = r.u32(), r.u32()
                    sl: dict[str, Any] = {"name": name, "frame": fno, "x": sx, "y": sy, "w": sw, "h": sh}
                    if flags_s & 1:
                        cx, cy = struct.unpack_from("<ii", r.d, r.p)
                        r.skip(8)
                        sl["center"] = {"x": cx, "y": cy, "w": r.u32(), "h": r.u32()}
                    if flags_s & 2:
                        px_, py_ = struct.unpack_from("<ii", r.d, r.p)
                        r.skip(8)
                        sl["pivot"] = {"x": px_, "y": py_}
                    ase.slices.append(sl)
            elif ctype == 0x2020 and prev_chunk == 0x2004 and ase.layers:  # dados do Asset Forge na camada
                uflags = r.u32()
                if uflags & 1:
                    txt = r.string()
                    if txt.startswith("asset-forge:"):
                        import json as _json
                        try:
                            meta = _json.loads(txt[len("asset-forge:"):])
                            ase.layers[-1].effects = list(meta.get("effects") or [])
                            ase.layers[-1].clip = bool(meta.get("clip"))
                        except ValueError:
                            ase.skipped.append(f"dados da camada \"{ase.layers[-1].name}\" ilegíveis")
            elif ctype == 0x2020 and prev_chunk == 0x2022 and ase.slices:  # cor da fatia
                uflags = r.u32()
                if uflags & 1:
                    r.string()
                if uflags & 2:
                    rr, gg, bb = r.u8(), r.u8(), r.u8()
                    ase.slices[-1]["color"] = f"#{rr:02x}{gg:02x}{bb:02x}"
            prev_chunk = ctype
            r.p = cend
        r.p = fstart + fsize
    _render_tilemaps(ase)
    return ase


def _render_tilemaps(ase: AseFile) -> None:
    """Cels de tilemap viram imagem (tiles do tileset colados na grade, a partir da posição do cel)."""
    for fi, cels in enumerate(ase.frames):
        for c in cels:
            if c.tilemap is None:
                continue
            lay = ase.layers[c.layer] if c.layer < len(ase.layers) else None
            ts = ase.tilesets.get(lay.tileset) if lay and lay.tileset is not None else None
            if ts is None:
                ase.skipped.append(f"tilemap sem tileset (frame {fi})")
                continue
            tw, th, tiles = ts
            cols, rows, ids = c.tilemap
            img = Image.new("RGBA", (cols * tw, rows * th))
            for i, t in enumerate(ids):
                if 0 < t < len(tiles):
                    img.paste(tiles[t], ((i % cols) * tw, (i // cols) * th))
            c.image = img


# ---------------------------------------------------------------- escrita

def _chunk(ctype: int, body: bytes) -> bytes:
    return struct.pack("<IH", len(body) + 6, ctype) + body


def _string(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def write_aseprite(path: str | Path, frames: list[Image.Image] | list[list[tuple[str, Image.Image]]],
                   durations: list[int] | None = None, tags: list[dict[str, Any]] | None = None,
                   slices: list[dict[str, Any]] | None = None) -> Path:
    """Grava um .aseprite RGBA.

    `frames`: lista de imagens (uma camada "Layer 1") ou, por frame, lista de (nome da camada, imagem) — as camadas
    são as mesmas em todos os frames, na ordem de baixo para cima. Todas as imagens têm o mesmo tamanho.
    `tags`: [{"name", "from", "to", "direction": forward|reverse|pingpong|pingpong_reverse, "repeat"}].
    """
    if not frames:
        raise AsepriteError("sem frames para gravar")
    per_frame: list[list[tuple[str, Image.Image]]] = [
        [("Layer 1", f)] if isinstance(f, Image.Image) else list(f) for f in frames]  # type: ignore[arg-type]
    names = [n for n, _ in per_frame[0]]
    w, h = per_frame[0][0][1].size
    durs = list(durations or [])
    durs += [durs[-1] if durs else 100] * (len(per_frame) - len(durs))

    out = bytearray()
    for fi, layers in enumerate(per_frame):
        if [n for n, _ in layers] != names:
            raise AsepriteError("as camadas devem ser as mesmas em todos os frames")
        chunks = []
        if fi == 0:
            chunks.append(_chunk(0x2007, struct.pack("<HHI", 1, 0, 0) + b"\0" * 8))   # perfil de cor: sRGB
            for name in names:
                chunks.append(_chunk(0x2004, struct.pack("<HHHHHHB3x", 3, 0, 0, 0, 0, 0, 255) + _string(name)))
            if tags:
                body = struct.pack("<H8x", len(tags))
                for t in tags:
                    d = DIRECTIONS.index(t.get("direction", "forward")) if t.get("direction", "forward") in DIRECTIONS else 0
                    body += struct.pack("<HHBH6x3xB", int(t["from"]), int(t["to"]), d, int(t.get("repeat", 0)), 0) + _string(t["name"])
                chunks.append(_chunk(0x2018, body))
        for li, (_n, img) in enumerate(layers):
            img = img.convert("RGBA")
            if img.size != (w, h):
                raise AsepriteError("todas as imagens devem ter o mesmo tamanho")
            body = struct.pack("<HhhBHh5x", li, 0, 0, 255, 2, 0) + struct.pack("<HH", w, h) + zlib.compress(img.tobytes(), 6)
            chunks.append(_chunk(0x2005, body))
        if fi == 0 and slices:
            for s in slices:
                body = struct.pack("<III", 1, 0, 0) + _string(s["name"]) + struct.pack(
                    "<IiiII", 0, int(s["x"]), int(s["y"]), int(s["w"]), int(s["h"]))
                chunks.append(_chunk(0x2022, body))
        payload = b"".join(chunks)
        n = len(chunks)
        out += struct.pack("<IHHHxxI", 16 + len(payload), MAGIC_FRAME, min(n, 0xFFFF), int(durs[fi]), n) + payload
    header = struct.pack("<IHHHHHIH8xBxxxHBBhhHH84x", 128 + len(out), MAGIC_FILE, len(per_frame), w, h, 32, 1,
                         int(durs[0]), 0, 0, 1, 1, 0, 0, 16, 16)
    assert len(header) == 128, len(header)
    p = Path(path)
    p.write_bytes(header + bytes(out))
    return p

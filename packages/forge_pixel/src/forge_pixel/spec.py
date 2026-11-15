"""Especificações de asset (canvas, pivô, área segura, contorno, paleta) e conjuntos de paleta.

Convenção de coordenadas (igual ao `.meta` do Unity): pivô e área segura em px a partir do canto
**inferior esquerdo**. Um pivô (x, y) é um ponto da grade (canto de pixel); o pixel (x, y) ocupa o
quadrado [x, x+1] x [y, y+1]. As funções `*_rows()` convertem para linhas da imagem (topo = 0).

Configuração por projeto (project.yaml):

    pixel:
      palette:                      # ou "palette: team/art/paletas.json" (caminho relativo ao projeto)
        core: ["#0b0a12", ...]
        groups:
          personagem/chest: {cores: ["#7e1a2f", ...]}   # também aceita lista simples
      specs:
        torso: {safe_area: [2, 0, 12, 16]}            # sobrescreve campos de um preset
        sword: {kind: weapon, canvas: [8, 30], pivot: [4, 30], outline: required}  # spec nova
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

OUTLINE_COLOR = "#0b0a12"
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

Kind = Literal["part", "tile", "icon", "enemy", "ui", "fx", "weapon", "wall", "bg", "npc"]
# formas de bloco (spec-blocos-11px.md §3): cheio, ladeira "\" e "/", pico/telhado e meio-bloco
Shape = Literal["full", "slope_down", "slope_up", "peak", "half"]
SHAPES: tuple[str, ...] = ("full", "slope_down", "slope_up", "peak", "half")


def norm_hex(c: str) -> str:
    """'#ABCDEF' / 'abcdef' -> '#abcdef'. Levanta ValueError para formato inválido."""
    m = _HEX.match(str(c).strip())
    if not m:
        raise ValueError(f"cor inválida: {c!r} (use #rrggbb)")
    return "#" + m.group(1).lower()


# ---------------------------------------------------------------- paleta

class PaletteSet(BaseModel):
    """Paleta travada: cores do núcleo + grupos nomeados (personagem/chest, tile/stone, ...)."""

    model_config = ConfigDict(extra="forbid")

    core: list[str] = Field(default_factory=list)
    groups: dict[str, list[str]] = Field(default_factory=dict)
    # True quando o projeto não declarou paleta (só a cor do contorno): o cheque de paleta é ignorado
    fallback: bool = False

    @field_validator("core")
    @classmethod
    def _core(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(norm_hex(c) for c in v))

    @field_validator("groups")
    @classmethod
    def _groups(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        return {k: list(dict.fromkeys(norm_hex(c) for c in cs)) for k, cs in v.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaletteSet":
        """Aceita o formato do paletas.json: {core: [...], groups: {nome: {cores: [...]}}} (ou listas simples)."""
        groups: dict[str, list[str]] = {}
        for name, g in (d.get("groups") or {}).items():
            if isinstance(g, dict):
                groups[name] = list(g.get("cores") or g.get("colors") or [])
            else:
                groups[name] = list(g or [])
        return cls(core=list(d.get("core") or []), groups=groups, fallback=bool(d.get("fallback", False)))

    @classmethod
    def only_outline(cls, color: str = OUTLINE_COLOR) -> "PaletteSet":
        return cls(core=[color], fallback=True)

    def allowed(self, group: str | None = None) -> set[str]:
        """Núcleo ∪ cores do grupo (group=None: só o núcleo). Grupo desconhecido levanta KeyError.
        Grupo composto "a+b" = união de grupos aprovados (inimigo novo cujo grupo próprio ainda não foi
        aprovado: usa os grupos mais próximos, ex. "inimigo/esqueleto+personagem/legs")."""
        out = set(self.core)
        if group:
            names = [group] if group in self.groups else [g.strip() for g in group.split("+") if g.strip()]
            for g in names:
                if g not in self.groups:
                    raise KeyError(f"grupo de paleta desconhecido: {g!r}. Disponíveis: {sorted(self.groups)}")
                out |= set(self.groups[g])
        return out


# ---------------------------------------------------------------- spec

class AssetSpec(BaseModel):
    """Regras de um tipo de asset. Tudo em px; pivô e área segura com origem no canto inferior esquerdo."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Kind = "part"
    canvas: tuple[int, int]                                   # (largura, altura)
    pivot: tuple[float, float] | None = None                  # (x, y) a partir do canto inferior esquerdo
    safe_area: tuple[int, int, int, int] | None = None        # (x, y, l, a), origem inferior esquerda
    outline: Literal["required", "none", "optional"] = "optional"
    outline_color: str = OUTLINE_COLOR
    # borda do canvas conta como "fora" para o contorno? (False: juntas abertas no pivô, como o pescoço)
    outline_edges: bool = False
    alpha: Literal["binary", "allow", "translucent"] = "binary"
    # alpha="translucent" (água, aceite §8): nenhum pixel 100% transparente, alfa dentro de alpha_range e no
    # máximo alpha_levels valores distintos
    alpha_range: tuple[int, int] = (160, 192)
    alpha_levels: int = 4
    palette_group: str | None = None
    max_colors: int | None = None
    tileable: bool = False
    tile_axes: Literal["both", "x", "y"] = "both"
    tile_tolerance: float = Field(1.5, description="emenda ≤ tolerância × diferença média entre colunas internas")
    opaque: bool = False                                      # sem nenhum pixel transparente (tiles sólidos)
    coverage: tuple[int, int] | None = None                   # maior dimensão do objeto (min, max), ícones
    min_island: int = 2
    island_connectivity: Literal[4, 8] = 8
    sparkle_colors: list[str] = Field(default_factory=list)   # brilhos declarados: ilhas só dessas cores passam
    anchor: Literal["bottom", "top", "center", "floor", "none"] = "center"   # regra de encaixe do fit
    anchor_x: Literal["center", "left", "right"] = "center"
    check_anchor: bool = False                                # validar pixel opaco a ≤ tolerância do pivô
    anchor_tolerance: int = 1
    name_pattern: str | None = None
    # cor por material (copper, iron, soul, wood, leather; outros nomes não têm regra) e tags de cor
    # (danger, blood, castle, vania, life liberam carmim; soul, reward, noble, fire, light liberam ouro).
    # Os parâmetros `material`/`tags` do validate() têm precedência / somam-se a estes.
    material: str | None = None
    tags: list[str] = Field(default_factory=list)
    # 9-slice (UI): margens (L, B, R, T) como no Border do Unity; o validate() roda o check_nine_slice
    nine_slice: tuple[int, int, int, int] | None = None
    # hitbox lógica do jogo (l, a) em px, centrada no x do pivô e com a base no y do pivô (EnemyData w×h).
    # Só informativa: a arte mostra, o colisor é do código; vai para o sheet.json e para as pranchas.
    hitbox: tuple[int, int] | None = None
    # forma do bloco (tiles de 11 px): o alfa 0 tem de formar exatamente a máscara canônica (check_shape)
    shape: Shape | None = None
    # espessura (aceite §7): mínimo de blocos 2x2 sólidos; None = 3 (o tile de 11 px aceita 2)
    min_blocks: int | None = None
    description: str = ""

    @field_validator("outline_color")
    @classmethod
    def _oc(cls, v: str) -> str:
        return norm_hex(v)

    @field_validator("sparkle_colors")
    @classmethod
    def _sc(cls, v: list[str]) -> list[str]:
        return [norm_hex(c) for c in v]

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(str(t).strip().lower() for t in v if str(t).strip()))

    @field_validator("canvas")
    @classmethod
    def _canvas(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 1 or v[1] < 1 or v[0] > 4096 or v[1] > 4096:
            raise ValueError(f"canvas inválido: {v}")
        return v

    # ---- helpers de coordenadas --------------------------------------
    @property
    def width(self) -> int:
        return self.canvas[0]

    @property
    def height(self) -> int:
        return self.canvas[1]

    def pivot_xy(self) -> tuple[float, float]:
        """Pivô efetivo (centro do canvas quando não declarado)."""
        return self.pivot if self.pivot is not None else (self.width / 2, self.height / 2)

    def safe_rect(self) -> tuple[int, int, int, int]:
        """Área segura (x, y, l, a) com origem inferior esquerda; o canvas todo quando não declarada."""
        return self.safe_area if self.safe_area is not None else (0, 0, self.width, self.height)

    def safe_box_rows(self) -> tuple[int, int, int, int]:
        """Área segura em coordenadas de imagem: (x0, linha0, x1, linha1) exclusivos, linha 0 = topo."""
        x, y, w, h = self.safe_rect()
        return x, self.height - (y + h), x + w, self.height - y

    def resolved_name_pattern(self) -> str | None:
        return self.name_pattern if self.name_pattern is not None else NAME_PATTERNS.get(self.kind)


# padrões de nome por tipo (aceite §2): tipo_bioma_nome_vNN.png, part_<conjunto>_<parte>[_L|_R]_vNN.png
NAME_PATTERNS: dict[str, str] = {
    "part": r"^part_[a-z0-9]+(?:_[a-z0-9]+)+?(?:_[LR])?_v\d{2}\.png$",
    "tile": r"^tile_[a-z0-9]+_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",
    "wall": r"^wall_[a-z0-9]+_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",
    # enemy_<bioma>_<nome>_vNN[_<pose>][_hit] (o _hit é a silhueta branca do flash de dano)
    "enemy": r"^enemy_[a-z0-9]+_[a-z0-9]+_v\d{2}(?:_[a-z0-9]+)?(?:_hit)?\.png$",
    "icon": r"^icon_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",
    "weapon": r"^(?:weapon|tool)_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",   # tool_: ferramentas (picareta)
    "ui": r"^ui_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",
    # fx_<nome>_vNN[_<quadro>][_hit] (o _hit é a silhueta branca, como nos inimigos: eco da investida)
    "fx": r"^fx_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}(?:_[a-z0-9]+)?(?:_hit)?\.png$",
    "bg": r"^bg_[a-z0-9]+_[a-z0-9]+(?:_[a-z0-9]+)*_v\d{2}\.png$",      # bg_<bioma>_<far|mid|near>_vNN
    # npc_<nome>_vNN[_<quadro>][_hit] (refugiados da vila: parado/falando)
    "npc": r"^npc_[a-z0-9]+_v\d{2}(?:_[a-z0-9]+)?(?:_hit)?\.png$",
}


def _part(name: str, canvas, pivot, safe, anchor, anchor_x="center", check_anchor=True, desc="") -> dict[str, Any]:
    return dict(name=name, kind="part", canvas=canvas, pivot=pivot, safe_area=safe, outline="required",
                anchor=anchor, anchor_x=anchor_x, check_anchor=check_anchor, description=desc)


# tabela do guia-de-partes.md / aceite-assetforge.md (jogo de referência, 16 PPU).
# Inimigos já levam a tag "danger" (carmim = perigo, telegrafia do ataque).
DEFAULT_SPEC_DATA: dict[str, dict[str, Any]] = {
    "head": _part("head", (20, 20), (10, 0), (2, 0, 16, 15), "bottom", desc="cabeça: o pescoço termina no pivô"),
    "hair": _part("hair", (20, 20), (10, 0), (2, 0, 16, 15), "bottom", check_anchor=False,
                  desc="cabelo: sobrepõe a cabeça, mesmo canvas"),
    "hat": _part("hat", (20, 20), (10, 0), (2, 0, 16, 15), "bottom", check_anchor=False,
                 desc="chapéu/capuz: cobre a cabeça sem mudar o canvas"),
    "torso": _part("torso", (16, 18), (8, 0), (2, 0, 12, 16), "bottom",
                   desc="do quadril ao pescoço (a gola dos casacos chega a y=15)"),
    "pelvis": _part("pelvis", (14, 8), (7, 4), (2, 0, 10, 8), "center", desc="cintura e topo das pernas"),
    "limb": _part("limb", (8, 12), (4, 10), (1, 2, 6, 9), "top",
                  desc="braço alto/baixo, coxa, canela: segmento de 6 px pendurado no pivô (topo)"),
    "limb_metal": _part("limb_metal", (8, 12), (4, 10), (1, 2, 7, 10), "top",
                        desc="braço alto de armadura de metal (ombreira): como limb, área segura 1 px maior à direita e no topo"),
    "hand": _part("hand", (8, 8), (4, 6), (2, 1, 5, 6), "top", desc="punho; soquete da arma 1 px abaixo do pivô"),
    "boot": _part("boot", (12, 12), (4, 10), (1, 0, 10, 8), "floor", check_anchor=False,
                  desc="bota: pé para a direita, sola em y=1; o cano cobre o fim da canela"),
    "cape": _part("cape", (12, 14), (9, 12), (2, 0, 9, 13), "top", anchor_x="right",
                  desc="capa: pendura no pivô (ombro, canto superior direito), cai para a esquerda"),
    "back": _part("back", (12, 16), (8, 8), (2, 1, 6, 15), "center", desc="item de costas (aljava)"),
    "sword": dict(name="sword", kind="weapon", canvas=(8, 30), pivot=(4, 30), outline="required", anchor="top",
                  description="espada na mão: pivô na empunhadura, lâmina para baixo"),
    "dagger": dict(name="dagger", kind="weapon", canvas=(6, 16), pivot=(3, 16), outline="required", anchor="top",
                   description="adaga na mão: pivô na empunhadura"),
    "pickaxe": dict(name="pickaxe", kind="weapon", canvas=(16, 30), pivot=(8, 30), outline="required", anchor="top",
                    description="picareta na mão: pivô no topo do cabo"),
    "icon": dict(name="icon", kind="icon", canvas=(16, 16), pivot=(8, 8), outline="required", coverage=(11, 16),
                 max_colors=8, palette_group="icons", anchor="center", description="ícone de item 16x16"),
    "tile": dict(name="tile", kind="tile", canvas=(16, 16), pivot=(8, 8), outline="none", tileable=True,
                 opaque=True, max_colors=8, anchor="center", description="tile sólido tileável 16x16"),
    "tile_top": dict(name="tile_top", kind="tile", canvas=(16, 16), pivot=(8, 8), outline="none", tileable=True,
                     tile_axes="x", opaque=True, max_colors=12, anchor="center",
                     description="borda de topo: tileável só na horizontal, até 12 cores (grama/musgo + material)"),
    "tile_edge": dict(name="tile_edge", kind="tile", canvas=(16, 16), pivot=(8, 8), outline="none", tileable=False,
                      opaque=True, max_colors=12, anchor="center",
                      description="variante de autotile com lado exposto (top_l, top_r, _m00.._m13): não repete no "
                                  "lado exposto (a coerência é do check_autotile), até 12 cores"),
    "tile_open": dict(name="tile_open", kind="tile", canvas=(16, 16), pivot=(8, 8), outline="optional",
                      tileable=True, tile_axes="x", opaque=False, max_colors=8, anchor="center",
                      description="tile com transparência declarada (plataforma, espinho, tocha, água de superfície)"),
    "tile_water": dict(name="tile_water", kind="tile", canvas=(16, 16), pivot=(8, 8), outline="none",
                       tileable=True, opaque=False, alpha="translucent", max_colors=8, anchor="center",
                       description="água (aceite §8): translúcida, alfa 160..192 em até 4 níveis, sem pixel vazio"),
    "tile_water_surface": dict(name="tile_water_surface", kind="tile", canvas=(16, 16), pivot=(8, 8),
                               outline="none", tileable=True, tile_axes="x", opaque=False, alpha="translucent",
                               max_colors=8, anchor="center",
                               description="superfície da água (aceite §8): como tile_water, repete só em x"),
    "wall": dict(name="wall", kind="wall", canvas=(16, 16), pivot=(8, 8), outline="none", tileable=True,
                 opaque=True, max_colors=8, palette_group="parede/catacumba", anchor="center",
                 description="parede de fundo tileável, mais escura e com pouco contraste"),
    "enemy": dict(name="enemy", kind="enemy", canvas=(40, 60), pivot=(10, 0), outline="required", anchor="bottom",
                  tags=["danger"],
                  description="inimigo de solo (esqueleto): pés no chão"),
    # esqueleto da Catacumba: pivô do .meta (0.25, 0) = (10, 0) px; o pé mais
    # baixo toca y=0 e algum pixel opaco fica a ≤ 1 px do pivô em toda pose. Área segura: 1 px livre nas
    # laterais e no topo (o contorno não encosta na borda do canvas; o ataque alcança até x=38).
    "enemy_skeleton": dict(name="enemy_skeleton", kind="enemy", canvas=(40, 60), pivot=(10, 0),
                           safe_area=(1, 0, 38, 59), outline="required", anchor="bottom", check_anchor=True,
                           palette_group="inimigo/esqueleto", tags=["danger"],
                           description="esqueleto da Catacumba 40x60: pivô (10, 0) = (0.25, 0) do .meta, pés em y=0, "
                                       "olha para a direita; poses por classe (locomoção, ataque, dano, morte)"),
    "enemy_flyer":dict(name="enemy_flyer", kind="enemy", canvas=(32, 24), pivot=(16, 12), outline="required",
                        tags=["danger"],
                        anchor="center", description="inimigo voador (morcego): pivô no centro"),
    # inimigos da 1ª ala (briefing dos inimigos): canvas e pivô sugeridos,
    # hitbox do EnemyData, ≤ 13 cores por quadro. Os grupos inimigo/<nome> ainda não foram aprovados:
    # o palette_group é a união dos grupos aprovados mais próximos (ver PROVISIONAL_ENEMY_GROUPS).
    **{k: dict(name=k, kind="enemy", canvas=cv, pivot=pv, safe_area=(1, 0, cv[0] - 2, cv[1] - 1), outline="required",
               # cão: quadrúpede com o corpo centrado no pivô (12, 0) = meio da hitbox 24x16; o pivô cai entre
               # as patas no galope, então só a linha do chão (check_pose_set) vale como âncora
               anchor="bottom", check_anchor=(k != "enemy_hound"), palette_group=grp, max_colors=13, tags=tg,
               hitbox=hb,
               description=desc)
       for k, cv, pv, hb, grp, tg, desc in (
           ("enemy_zombie", (40, 60), (10, 0), (12, 32), "inimigo/esqueleto+personagem/legs", ["danger"],
            "zumbi da Catacumba 40x60, pivô (10, 0), hitbox 12x32; carne cinza-esverdeada, trapo e osso do esqueleto"),
           ("enemy_hound", (48, 32), (12, 0), (24, 16), "inimigo/esqueleto+personagem/back", ["danger"],
            "cão da Catacumba 48x32, pivô (12, 0), hitbox 24x16; corpo baixo e comprido, couro e osso"),
           ("enemy_archer", (40, 60), (10, 0), (10, 32), "inimigo/esqueleto+personagem/back", ["danger"],
            "arqueiro esqueleto 40x60, pivô (10, 0), hitbox 10x32; capuz de couro e arco (flecha à parte)"),
           ("enemy_armor", (48, 64), (12, 0), (14, 36), "personagem/gloves", ["danger"],
            "armadura vazia 48x64, pivô (12, 0), hitbox 14x36; aço escurecido, brilho carmim fraco no elmo"),
           ("enemy_knight", (56, 64), (14, 0), (14, 38), "personagem/chest", ["danger", "vania", "noble"],
            "cavaleiro de Vânia 56x64, pivô (14, 0), hitbox 14x38; aço + casaco carmim e dourado de Vânia"),
       )},
    # projétil inimigo (flecha do arqueiro, ~8x3 + contorno): branco brilhante com ponta carmim (tag danger)
    "fx_projectile": dict(name="fx_projectile", kind="fx", canvas=(10, 5), pivot=(5, 2.5), outline="required",
                          palette_group="fx", tags=["danger"], anchor="center", max_colors=6, hitbox=(8, 3),
                          description="projétil inimigo 8x3 + contorno (10x5), pivô no centro; aponta para a direita"),
    "crack": dict(name="crack", kind="fx", canvas=(16, 16), pivot=(8, 8), outline="optional", palette_group="fx",
                  anchor="center",
                  description="rachadura de mineração (núcleo escuro acumulativo entre estágios; o reflexo claro pode mudar; "
                              "núcleo escuro de 1 px não é contorno)"),
    # alerta de ataque (aceite do esqueleto v02, 13/10/2026): o "!" sai do sprite do inimigo e vira efeito
    # separado, ligado pelo jogo no quadro de "preparando". Carmim do núcleo (tag danger), contorno de 1 px.
    "fx_alert": dict(name="fx_alert", kind="fx", canvas=(5, 10), pivot=(2.5, 0), outline="required",
                     palette_group="fx", tags=["danger"], anchor="bottom",
                     description="alerta \"!\" 3x8 + contorno (5x10), pivô no centro da base; o jogo posiciona "
                                 "acima da cabeça do inimigo"),
    # fundos de parallax (entrega 6): a janela da câmera inteira (640x360 @ 16 PPU), repete só em x,
    # sem contorno. A distante (céu) é opaca; a média e a próxima têm transparência binária.
    "parallax": dict(name="parallax", kind="bg", canvas=(640, 360), pivot=(320, 0), outline="none", tileable=True,
                     tile_axes="x", opaque=True, max_colors=24, anchor="none",
                     description="camada de parallax opaca 640x360 (a distante, com o céu), repete na horizontal"),
    "parallax_open": dict(name="parallax_open", kind="bg", canvas=(640, 360), pivot=(320, 0), outline="none",
                          tileable=True, tile_axes="x", opaque=False, max_colors=24, anchor="none",
                          description="camada de parallax 640x360 com transparência binária (média/próxima), repete em x"),
    # NPCs da vila: quadro inteiro 32x48 (2x3 tiles), pivô (16, 0) = sola no
    # chão, olha para a direita. Cabe o jogador montado pelo rig (42 px de altura, x −11..8 em volta da raiz)
    # com folga para chapéu/capuz e adereços. 1 px livre nas laterais e no topo (o contorno não encosta).
    "npc": dict(name="npc", kind="npc", canvas=(32, 48), pivot=(16, 0), safe_area=(1, 0, 30, 47),
                outline="required", anchor="bottom", check_anchor=True, palette_group="personagem/npc",
                description="NPC da vila 32x48, pivô (16, 0) = sola; poses parado/falando com chão estável"),
    "cursor": dict(name="cursor", kind="ui", canvas=(18, 18), pivot=(9, 9), outline="none", anchor="center",
                   sparkle_colors=["#ffffff"],
                   description="cursor de mineração 18x18, branco puro (anel tracejado: traços brancos declarados)"),
}


def _ui(name: str, canvas, border: int | None = None, outline: str = "optional", desc: str = "", **kw) -> dict[str, Any]:
    """Peça de UI (lote 2 da Vera): grupo de paleta "ui", pivô no centro; `border` = 9-slice simétrico."""
    d = dict(name=name, kind="ui", canvas=canvas, outline=outline, palette_group="ui", anchor="center",
             description=desc, **kw)
    if border is not None:
        d["nine_slice"] = (border,) * 4
    return d


# UI, lote 2 (especificação da Vera, entrega 7): 9-slices com borda simétrica, estados como arquivos separados
UI_SPEC_DATA: dict[str, dict[str, Any]] = {
    "ui_row": _ui("ui_row", (16, 16), 2, desc="linha de menu 9-slice (normal/focus/pressed)"),
    "ui_slot_icon": _ui("ui_slot_icon", (16, 16), outline="required", max_colors=4, coverage=(9, 16),
                        desc="silhueta monocromática de slot de equipamento vazio"),
    "ui_heal_ring": _ui("ui_heal_ring", (22, 22), max_colors=4, desc="anel de recarga (Image radial, tingido)"),
    "ui_cooldown_fill": _ui("ui_cooldown_fill", (16, 16), opaque=True, max_colors=1,
                            desc="quadrado branco para preenchimento radial de recarga"),
    "ui_minimap_frame": _ui("ui_minimap_frame", (16, 16), 2, desc="moldura 9-slice do minimapa (64x40)"),
    "ui_mapcell": _ui("ui_mapcell", (6, 4), min_island=1, desc="célula do minimapa (com 1 px de vão à direita/embaixo)"),
    "ui_mapcell_lg": _ui("ui_mapcell_lg", (12, 8), min_island=1, desc="célula da tela de Mapa"),
    "ui_room_icon": _ui("ui_room_icon", (8, 8), outline="required", max_colors=6, desc="ícone de sala 8x8"),
    "ui_boss_frame": _ui("ui_boss_frame", (8, 8), 2, desc="moldura 9-slice da barra do chefe (200x8)"),
    "ui_boss_poise_fill": _ui("ui_boss_poise_fill", (1, 2), opaque=True, desc="postura do chefe (âmbar, 1x2)"),
    "ui_slider": _ui("ui_slider", (8, 6), 2, desc="trilho/preenchimento de slider 9-slice"),
    "ui_slider_knob": _ui("ui_slider_knob", (8, 12), desc="botão do slider"),
    "ui_toggle": _ui("ui_toggle", (24, 12), desc="chave liga/desliga"),
    "ui_toast": _ui("ui_toast", (16, 16), 3, desc="aviso (toast) 9-slice"),
    "ui_tooltip": _ui("ui_tooltip", (16, 16), 2, desc="dica (tooltip) 9-slice"),
    "ui_scroll": _ui("ui_scroll", (6, 6), 2, desc="barra de rolagem (trilho/polegar) 9-slice"),
    "ui_stepper": _ui("ui_stepper", (16, 16), desc="botão −/+ do stepper"),
    "ui_cursor_pointer": _ui("ui_cursor_pointer", (12, 12), outline="required", desc="cursor do mouse (seta/mão)"),
    "ui_divider": _ui("ui_divider", (3, 3), 1, desc="divisor 9-slice (linha)"),
    # lote 3 (§12.7 da Vera, modo Plataforma)
    "ui_panel_stone": _ui("ui_panel_stone", (16, 16), 4, desc="painel de pedra 9-slice (modo Plataforma), miolo escuro"),
    "ui_slot_tall": _ui("ui_slot_tall", (40, 88), desc="slot alto das mãos (arma), normal/focus/locked"),
    "ui_preset_tab": _ui("ui_preset_tab", (40, 40), desc="aba de preset (normal/focus/active) e o \"+\""),
}
DEFAULT_SPEC_DATA.update(UI_SPEC_DATA)


def _fx_soul(name: str, canvas, pivot, outline: str = "none", sparkle: tuple[str, ...] = (), desc: str = "",
             **kw) -> dict[str, Any]:
    """Efeito de alma ofensiva (fx-almas-ofensivas.md): grupo de paleta fx/alma (contorno,
    carmim escuro/médio, núcleo quente, branco), tag soul (libera o carmim), no máximo 6 cores, alfa binário
    (aceite §2/§8: o "translúcido" é feito com tons, não com alfa). `sparkle`: faíscas declaradas (ilhas de 1 px)."""
    return dict(name=name, kind="fx", canvas=canvas, pivot=pivot, outline=outline, palette_group="fx/alma",
                tags=["soul"], max_colors=6, anchor="center", sparkle_colors=list(sparkle), description=desc, **kw)


_SPARK = ("#ffffff", "#ff8a96")
# efeitos das 6 almas ofensivas + 3 peças comuns (fx-almas-ofensivas.md). Projéteis (corpo
# que o jogador precisa ler pela silhueta) levam contorno 1 px; rastros, auras e estouros não (aceite §2: efeito
# sem contorno). Rastros: pivô na ponta da direita (encosta no projétil, que voa para a direita).
FX_SOUL_SPEC_DATA: dict[str, dict[str, Any]] = {
    "fx_alma_conjuracao": _fx_soul("fx_alma_conjuracao", (24, 24), (12, 12), sparkle=_SPARK,
                                   desc="aura de conjuração na mão (cresce, pulsa, solta), 6 quadros"),
    "fx_alma_impacto": _fx_soul("fx_alma_impacto", (32, 32), (16, 16), sparkle=_SPARK,
                                desc="estouro carmim no ponto de acerto, 5 quadros"),
    "fx_alma_brasa": _fx_soul("fx_alma_brasa", (3, 3), (1.5, 1.5), min_island=1,
                              desc="brasa solta: atlas de 3 tamanhos (1, 2 e 3 px)"),
    "fx_alma_morcego_voo": _fx_soul("fx_alma_morcego_voo", (16, 12), (8, 6), outline="required",
                                    desc="morcego carmim (projétil), 3 quadros de batida de asa"),
    "fx_alma_morcego_rastro": _fx_soul("fx_alma_morcego_rastro", (8, 6), (8, 3), sparkle=_SPARK,
                                       desc="cauda de 8 px atrás do morcego"),
    "fx_alma_flecha": _fx_soul("fx_alma_flecha", (16, 5), (8, 2.5), outline="required",
                               desc="flecha espectral 16x5 (haste e pena fantasma), 2 quadros de brilho"),
    "fx_alma_flecha_rastro": _fx_soul("fx_alma_flecha_rastro", (24, 3), (24, 1.5),
                                      desc="linha que se apaga atrás da flecha, 24x3"),
    "fx_alma_investida_eco": _fx_soul("fx_alma_investida_eco", (36, 40), (18, 5), outline="required",
                                      desc="eco carmim do jogador na pose do dash (rig, capa a -60°); pivô = raiz "
                                           "do personagem (pés)"),
    "fx_alma_investida_vento": _fx_soul("fx_alma_investida_vento", (24, 16), (12, 8),
                                        desc="3 linhas de velocidade da investida, 3 quadros em laço"),
    "fx_alma_machado": _fx_soul("fx_alma_machado", (16, 16), (8, 8), outline="required",
                                desc="machado duplo girando (projétil), 4 quadros de giro"),
    "fx_alma_machado_arco": _fx_soul("fx_alma_machado_arco", (32, 32), (16, 16),
                                     desc="rastro curvo do machado, 4 quadros"),
    "fx_alma_fatuo": _fx_soul("fx_alma_fatuo", (12, 12), (6, 6), outline="required",
                              desc="fogo-fátuo (esfera teleguiada), 4 quadros de chama"),
    "fx_alma_fatuo_explosao": _fx_soul("fx_alma_fatuo_explosao", (48, 48), (24, 24), sparkle=_SPARK,
                                       desc="explosão de 24 px de raio (nível III), 6 quadros"),
    "fx_alma_corte_onda": _fx_soul("fx_alma_corte_onda", (96, 24), (48, 12), outline="required",
                                   desc="onda de lâmina crescente 96x24 (6 tiles), 5 quadros: nasce, abre, afina"),
    "fx_alma_corte_centelha": _fx_soul("fx_alma_corte_centelha", (8, 8), (4, 4), sparkle=_SPARK,
                                       desc="centelha na borda da onda"),
}
DEFAULT_SPEC_DATA.update(FX_SOUL_SPEC_DATA)

# ---------------------------------------------------------------- blocos de 11 px (spec-blocos-11px.md)
# Tile = 11x11, PPU 16, pivô no centro (5.5, 5.5). Base ≤ 8 cores, topo ≤ 10; espessura ≥ 2 blocos 2x2.
# Nomes: tile_<bioma>_<mat>_<forma>[_<var>]_vNN.png; autotile só do full (_full_mNN, _full_top[_l|_r|_b]).
TILE11 = 11
_T11 = (TILE11, TILE11)
_P11 = (TILE11 / 2, TILE11 / 2)
_MAT = r"[a-z0-9]+(?:_[a-z0-9]+)*?"            # material (pode ter _: ore_copper)
_VAR = r"(?:_[bcd])?"                           # variação b/c (d = pedra calma dos minérios)
SHAPE_PATTERN = re.compile(r"_(full|slope_down|slope_up|peak|half)(?:_|$)")


def _tile11(name: str, desc: str, **kw: Any) -> dict[str, Any]:
    d = dict(name=name, kind="tile", canvas=_T11, pivot=_P11, outline="none", anchor="center", min_blocks=2,
             description=desc)
    d.update(kw)
    return d


TILE11_SPEC_DATA: dict[str, dict[str, Any]] = {
    "tile11": _tile11("tile11", "bloco cheio 11x11 tileável (base, _b/_c, máscara 15), até 8 cores",
                      tileable=True, opaque=True, max_colors=8, shape="full",
                      name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_full{_VAR}(?:_m15)?_v\d{{2}}\.png$"),
    "tile11_top": _tile11("tile11_top", "topo do bloco cheio 11x11 (_full_top, _full_top_b, máscara 14): tileável só "
                                        "na horizontal, até 10 cores (crosta + material)",
                          tileable=True, tile_axes="x", opaque=True, max_colors=10, shape="full",
                          name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_full{_VAR}(?:_top(?:_b)?|_m14)_v\d{{2}}\.png$"),
    "tile11_edge": _tile11("tile11_edge", "variante de autotile 11x11 com lado exposto (_full_top_l/_r, _full_mNN): "
                                          "não repete no lado exposto (coerência no check_autotile), até 10 cores",
                           tileable=False, opaque=True, max_colors=10,
                           name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_full{_VAR}(?:_top_[lr]|_m\d{{2}})_v\d{{2}}\.png$"),
    "wall11": _tile11("wall11", "parede de fundo 11x11 tileável, mais escura e com pouco contraste",
                      kind="wall", tileable=True, opaque=True, max_colors=8, palette_group="parede/catacumba"),
    **{f"tile11_{s}": _tile11(f"tile11_{s}", desc, tileable=False, opaque=False, max_colors=10, shape=s,
                              name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_{s}(?:_[bc])?_v\d{{2}}\.png$")
       for s, desc in (
           ("slope_down", "ladeira descendo à direita (\\) 11x11: ângulo reto embaixo à esquerda, crosta na hipotenusa"),
           ("slope_up", "ladeira subindo à direita (/) 11x11: ângulo reto embaixo à direita, crosta na hipotenusa"),
           ("peak", "pico/telhado 11x11: vértice em cima no centro (coluna 5), base inteira, crosta nas 2 hipotenusas"),
           ("half", "meio-bloco 11x11: retângulo nas linhas de baixo (ver HALF_ROWS), crosta no topo"),
       )},
}
DEFAULT_SPEC_DATA.update(TILE11_SPEC_DATA)


# ---------------------------------------------------------------- madeira e árvores (spec-blocos-11px.md §6)
# Decisão de arte: a árvore é montada com TILES de 11x11 (tronco da largura de 1 bloco), como o
# mundo por semente já põe os blocos de tronco (id 17), folha (id 18) e madeira (id 9). Sem contorno, alfa
# binário (a folha "translúcida" é feita de furos alfa 0 entre os cachos), sem carmim e sem dourado.
TREE11_SPEC_DATA: dict[str, dict[str, Any]] = {
    "tile11_trunk": _tile11("tile11_trunk", "tronco 11x11 (_b/_c): casca que repete na vertical; margem "
                                            "transparente dos lados (o tronco não ocupa o bloco inteiro)",
                            tileable=True, tile_axes="y", opaque=False, max_colors=8,
                            name_pattern=rf"^tile_[a-z0-9]+_trunk(?:_[bc])?_v\d{{2}}\.png$"),
    "tile11_tree": _tile11("tile11_tree", "peça de árvore 11x11 que não repete: base com raízes, topo do tronco, "
                                          "galhos _l/_r (ponta dentro do tile)",
                           tileable=False, opaque=False, max_colors=8,
                           name_pattern=rf"^tile_[a-z0-9]+_(?:trunk_base|trunk_top|branch_[lr])_v\d{{2}}\.png$"),
    "tile11_leaf": _tile11("tile11_leaf", "folha 11x11 (_b/_c): cachos com furos alfa 0; repete nas 4 direções "
                                          "(copas de vários tiles)",
                           tileable=True, opaque=False, max_colors=8,
                           name_pattern=rf"^tile_[a-z0-9]+_leaf(?:_[bc])?_v\d{{2}}\.png$"),
    "tile11_platform": _tile11("tile11_platform", "plataforma de madeira 11x4, atravessável por baixo; repete em x",
                               canvas=(TILE11, 4), pivot=(TILE11 / 2, 2), tileable=True, tile_axes="x",
                               opaque=False, max_colors=8,
                               name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_platform(?:_[bc])?_v\d{{2}}\.png$"),
    # tiles com transparência e água em 11 px (redesenho dos de 16: areia/cascalho são tile11 comuns)
    "tile11_open": _tile11("tile11_open", "tile 11x11 com transparência declarada (tocha, espinho)", outline="optional",
                           tileable=True, tile_axes="x", opaque=False, max_colors=8),
    "tile11_water": _tile11("tile11_water", "água 11x11 (aceite §8): translúcida, alfa 160..192 em até 4 níveis, "
                                            "sem pixel vazio", tileable=True, opaque=False, alpha="translucent",
                            max_colors=8),
    "tile11_water_surface": _tile11("tile11_water_surface", "superfície da água 11x11 (aceite §8): como tile11_water, "
                                                            "repete só em x", tileable=True, tile_axes="x",
                                    opaque=False, alpha="translucent", max_colors=8),
    "tile11_door": _tile11("tile11_door", "porta 11x33 (1x3 blocos), fechada ou aberta (_open); pivô na base central",
                           canvas=(TILE11, 33), pivot=(TILE11 / 2, 0), tileable=False, opaque=False, max_colors=10,
                           anchor="bottom",
                           name_pattern=rf"^tile_[a-z0-9]+_{_MAT}_door(?:_open)?_v\d{{2}}\.png$"),
}
DEFAULT_SPEC_DATA.update(TREE11_SPEC_DATA)

# prefixo do nome do arquivo -> preset de UI (o mais longo primeiro)
UI_FILE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ui_slot_empty_", "ui_slot_icon"), ("ui_heal_ring", "ui_heal_ring"), ("ui_cooldown_fill", "ui_cooldown_fill"),
    ("ui_minimap_frame", "ui_minimap_frame"), ("ui_mapcell_lg_", "ui_mapcell_lg"), ("ui_mapcell_", "ui_mapcell"),
    ("ui_room_", "ui_room_icon"), ("ui_boss_frame", "ui_boss_frame"), ("ui_boss_poise_fill", "ui_boss_poise_fill"),
    ("ui_slider_knob_", "ui_slider_knob"), ("ui_slider_", "ui_slider"), ("ui_toggle_", "ui_toggle"),
    ("ui_toast", "ui_toast"), ("ui_tooltip", "ui_tooltip"), ("ui_scroll_", "ui_scroll"), ("ui_stepper_", "ui_stepper"),
    ("ui_cursor_arrow", "ui_cursor_pointer"), ("ui_cursor_hand", "ui_cursor_pointer"), ("ui_divider", "ui_divider"),
    ("ui_row_", "ui_row"), ("ui_panel_stone", "ui_panel_stone"), ("ui_slot_tall_", "ui_slot_tall"),
    ("ui_preset_tab_", "ui_preset_tab"), ("ui_preset_plus", "ui_preset_tab"),
)

PART_SPEC_ALIASES: dict[str, str] = {
    "arm_up": "limb", "arm_low": "limb", "thigh": "limb", "shin": "limb", "forearm": "limb", "upper_arm": "limb",
    "boots": "boot", "quiver": "back", "hood": "hat", "ironhelm": "hat", "helm": "hat",
}


def make_specs(overrides: dict[str, Any] | None = None, base: dict[str, dict[str, Any]] | None = None) -> dict[str, AssetSpec]:
    """Presets + sobrescritas do projeto. Uma chave existente mescla campos; uma nova cria spec."""
    data = {k: dict(v) for k, v in (base or DEFAULT_SPEC_DATA).items()}
    for name, ov in (overrides or {}).items():
        if not isinstance(ov, dict):
            raise ValueError(f"spec {name!r} deve ser um objeto")
        merged = {**data.get(name, {}), **ov}
        merged.setdefault("name", name)
        data[name] = merged
    return {k: AssetSpec.model_validate(v) for k, v in data.items()}


def default_specs() -> dict[str, AssetSpec]:
    return make_specs()


def resolve_spec(spec: str | dict[str, Any] | AssetSpec | None, specs: dict[str, AssetSpec] | None = None,
                 canvas: tuple[int, int] | None = None) -> AssetSpec:
    """Nome de preset, dict inline ({"base": "limb", ...} herda de um preset) ou AssetSpec.
    None + canvas = spec genérica livre (contorno opcional, sem área segura)."""
    specs = specs or default_specs()
    if isinstance(spec, AssetSpec):
        return spec
    if spec is None:
        if canvas is None:
            raise ValueError("spec ausente e sem canvas para uma spec genérica")
        return AssetSpec(name="custom", kind="ui", canvas=canvas, outline="optional")
    if isinstance(spec, str):
        key = PART_SPEC_ALIASES.get(spec, spec)
        if key not in specs:
            raise KeyError(f"spec desconhecida: {spec!r}. Disponíveis: {sorted(specs)}")
        return specs[key]
    d = dict(spec)
    base = d.pop("base", None)
    if base:
        b = resolve_spec(str(base), specs)
        d = {**b.model_dump(), **d}
    d.setdefault("name", "custom")
    return AssetSpec.model_validate(d)


# ---------------------------------------------------------------- inferência pelo nome do arquivo

# inimigos da 1ª ala: nome no arquivo (enemy_catacumba_<nome>_vNN) -> preset
ENEMY_FILE_PRESETS: dict[str, str] = {"zumbi": "enemy_zombie", "cao": "enemy_hound", "arqueiro": "enemy_archer",
                                      "armadura": "enemy_armor", "cavaleiro": "enemy_knight"}
# grupo de paleta provisório enquanto a direção de arte não aprova inimigo/<nome> (briefing: "aprovo na primeira
# entrega"): união dos grupos aprovados mais próximos. Ao aprovar, apague a linha e o preset passa a usar o grupo.
PROVISIONAL_ENEMY_GROUPS: dict[str, str] = {
    "zumbi": "inimigo/esqueleto+personagem/legs",      # osso/trapo/couro do esqueleto + cinza-esverdeado das calças
    "cao": "inimigo/esqueleto+personagem/back",        # osso do esqueleto + couro marrom da aljava
    "arqueiro": "inimigo/esqueleto+personagem/back",   # esqueleto + capuz e aljava de couro
    "armadura": "personagem/gloves",                   # aço escurecido (manoplas); carmim do núcleo
    "cavaleiro": "personagem/chest",                   # aço + carmim/dourado do casaco de Vânia
}

def tile11_spec_for_name(n: str) -> str | None:
    """Nome novo com forma (tile_<bioma>_<mat>_<forma>[_<var>]_vNN, sem .png) -> preset de 11 px; None se o
    nome não tem forma (nomes antigos de 16 px)."""
    m = SHAPE_PATTERN.search(n)
    if not n.startswith("tile_") or not m:
        return None
    shape = m.group(1)
    if shape != "full":
        return f"tile11_{shape}"
    mm = re.search(r"_m(\d{2})_v\d{2}$", n)
    if mm:
        return {15: "tile11", 14: "tile11_top"}.get(int(mm.group(1)), "tile11_edge")
    if re.search(r"_top_[lr]_v\d{2}$", n):
        return "tile11_edge"
    if re.search(r"_top(_b)?_v\d{2}$", n):
        return "tile11_top"
    return "tile11"


def spec_name_for_file(filename: str, canvas: tuple[int, int] | None = None) -> str | None:
    """Preset da tabela a partir do nome no padrão do jogo (part_body_arm_up_R_v01.png -> 'limb').
    Tiles com forma no nome (`_full`, `_slope_down`...) são os de 11 px; `canvas` (l, a) desempata o que o
    nome não diz (wall_<bioma>_<mat>_v01 de 11x11 -> 'wall11')."""
    n = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    n = re.sub(r"\.png$", "", n)
    if n.startswith("tile_") and re.search(r"_platform(_[bc])?_v\d{2}$", n):
        return "tile11_platform"
    if n.startswith("tile_") and re.search(r"_door(_open)?_v\d{2}$", n):
        return "tile11_door"
    is11 = canvas is not None and tuple(canvas) == _T11
    if n.startswith("tile_") and "_water" in n and ("_full" in n or is11):   # água de 11 px (aceite §8)
        return "tile11_water_surface" if "_surface" in n else "tile11_water"
    if n.startswith("tile_") and is11 and re.search(r"_(plank|spike|torch)", n):
        return "tile11_open"
    if n.startswith("tile_"):                           # árvore em tiles de 11 px (spec-blocos-11px.md §6)
        if re.search(r"_(trunk_base|trunk_top|branch_[lr])_v\d{2}$", n):
            return "tile11_tree"
        if re.search(r"_trunk(_[bc])?_v\d{2}$", n):
            return "tile11_trunk"
        if re.search(r"_leaf(_[bc])?_v\d{2}$", n):
            return "tile11_leaf"
    t11 = tile11_spec_for_name(n)
    if t11:
        return t11
    if n.startswith("wall_") and canvas is not None and tuple(canvas) == _T11:
        return "wall11"
    if n.startswith("part_"):
        body = re.sub(r"(_[lr])?_v\d{2}$", "", n[5:])
        if body.startswith("cape_"):
            return "cape"
        if body.startswith("back_"):
            return "back"
        if body.startswith("boots_"):
            return "boot"
        if body.startswith("hat_"):
            return "hat"
        if body.endswith("_arm_up") and "steelplate" in body:   # ombreira de metal: área segura maior
            return "limb_metal"
        for suffix, spec in (("arm_up", "limb"), ("arm_low", "limb"), ("thigh", "limb"), ("shin", "limb"),
                             ("hand", "hand"), ("boot", "boot"), ("torso", "torso"), ("pelvis", "pelvis"),
                             ("head", "head"), ("hair", "hair"), ("hat", "hat")):
            if body.endswith("_" + suffix) or body == suffix:
                return spec
        return None
    if n.startswith("weapon_"):
        for w in ("sword", "dagger", "pickaxe"):
            if f"_{w}" in n:
                return w
        return None
    # ferramentas na mão (gen-mining.js): picareta, e machado/martelo da spec-blocos-11px.md §7, que usam o mesmo
    # canvas 16x30 com pivô no topo do cabo
    if n.startswith("tool_") and re.search(r"_(picareta|pickaxe|machado|axe|martelo|hammer)", n):
        return "pickaxe"
    if n.startswith("tile_"):
        if "_water" in n:                                 # água translúcida (aceite §8)
            return "tile_water_surface" if "_surface" in n else "tile_water"
        if re.search(r"_(plank|spike|torch)", n):
            return "tile_open"
        m = re.search(r"_m(\d{2})_v\d{2}$", n)          # autotile: máscara cardinal (ver autotile.py)
        if m:
            return {15: "tile", 14: "tile_top"}.get(int(m.group(1)), "tile_edge")
        if re.search(r"_top_[lr](_[bc])?_v\d{2}$", n):
            return "tile_edge"
        return "tile_top" if re.search(r"_top(_b)?(_[bc])?_v\d{2}$", n) else "tile"
    if n.startswith("wall_"):
        return "wall"
    if n.startswith("icon_"):
        return "icon"
    if n.startswith("fx_alma_"):                        # almas ofensivas: fx_alma_<peça>_vNN[_<quadro>][_hit]
        piece = "fx_alma_" + re.sub(r"_v\d{2}(_[a-z0-9]+)?(_hit)?$", "", n[8:])
        return piece if piece in FX_SOUL_SPEC_DATA else None
    if n.startswith("fx_") and "crack" in n:
        return "crack"
    if n.startswith("fx_") and ("alerta" in n or "alert" in n):
        return "fx_alert"
    if n.startswith("fx_") and ("flecha" in n or "arrow" in n):      # projétil do arqueiro
        return "fx_projectile"
    if n.startswith("ui_"):
        for prefix, spec in UI_FILE_PREFIXES:
            if n.startswith(prefix):
                return spec
    if n.startswith("ui_") and "cursor" in n:
        return "cursor"
    if n.startswith("bg_"):
        return "parallax" if "_far" in n else "parallax_open"     # só a distante é opaca (o céu)
    if n.startswith("enemy_"):
        if "_esqueleto_" in n:
            return "enemy_skeleton"
        if "_morcego_" in n:
            return "enemy_flyer"
        for nome, preset in ENEMY_FILE_PRESETS.items():
            if f"_{nome}_" in n:
                return preset
        return "enemy"
    if n.startswith("npc_"):                            # refugiados da vila (entrega 9)
        return "npc"
    return None


def palette_group_for_file(filename: str) -> str | None:
    """Grupo de paleta no padrão do art/tools/gen-palettes.js (personagem/<conjunto>, tile/<material>, ...)."""
    n = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if n.startswith("part_"):
        m = re.match(r"^part_([a-z]+)_", n)
        return "personagem/" + (m.group(1) if m else n.split("_")[0])
    if n.startswith("weapon_"):
        return "personagem/weapon"
    if n.startswith("tool_"):
        return "personagem/tool"
    if n.startswith("tile_") and re.search(r"_branch_[lr]_v\d{2}\.png$", n):
        return "tile/wood+tile/leaves"                  # galho: madeira com o tufo de folhas na ponta
    if n.startswith("tile_") and re.search(r"_(platform|door|trunk|trunk_base|trunk_top)"
                                           r"(_[a-z0-9]+)?_v\d{2}\.png$", n):
        return "tile/wood"                              # tronco, plataforma e porta: grupo tile/wood
    if n.startswith("tile_") and re.search(r"_leaf(_[bc])?_v\d{2}\.png$", n):
        return "tile/leaves"                            # grupo PROPOSTO (3 verdes da grama), a aprovar
    if n.startswith("tile_"):
        g = re.sub(r"^tile_[a-z0-9]+?_", "", n)
        # nome novo (11 px): tudo a partir da forma sai (tile_hesperia_ore_copper_full_v01 -> tile/ore_copper)
        g = re.sub(r"_(full|slope_down|slope_up|peak|half)(_[a-z0-9]+)*_v\d{2}\.png$", "", g)
        # variação (_b/_c/_d) antes ou depois da máscara/topo: stone_b_m03, stone_b_top_l, dirt_top_b, stone_d
        g = re.sub(r"(_[bcd])?(_m\d{2})?(_top)?(_[lrb])?(_[bcd])?_v\d{2}\.png$", "", g)
        g = re.sub(r"_surface$", "", g)
        return "tile/" + g
    if n.startswith("enemy_"):
        parts = n.split("_")
        if len(parts) > 2 and parts[2] in PROVISIONAL_ENEMY_GROUPS:     # grupo próprio ainda não aprovado
            return PROVISIONAL_ENEMY_GROUPS[parts[2]]
        return "inimigo/" + parts[2] if len(parts) > 2 else None
    if n.startswith("wall_"):
        return "parede/catacumba"
    if n.startswith("npc_"):
        return "personagem/npc"
    m = re.match(r"^icon_(vania_brick|spikes|platform|sand|gravel|wood|trunk|leaf|door)_v\d{2}\.png$", n)
    if m:                                               # ícone de bloco = cores do tile do material (entrega 13)
        return {"vania_brick": "tile/brick", "spikes": "tile/spike", "sand": "tile/sand", "gravel": "tile/gravel",
                "leaf": "tile/leaves"}.get(m.group(1), "tile/wood")
    if n.startswith("icon_soul_"):                      # almas: cores finais por tipo (aceite 13/10)
        return "icons/almas"
    m = re.match(r"^icon_(hat|chest|legs|boots|gloves|cape|back)_", n)
    if m:                                               # ícone de item = cores da peça aprovada do mesmo slot
        return "personagem/" + m.group(1)
    if n.startswith("icon_"):
        return "icons"
    if n.startswith("fx_alma_"):                        # efeitos das almas ofensivas (carmim final)
        return "fx/alma"
    if n.startswith("fx_") or n.startswith("ui_"):
        return "fx"
    return None


def side_of_file(filename: str) -> str | None:
    m = re.search(r"_([LR])_v\d{2}", filename)
    return m.group(1) if m else None

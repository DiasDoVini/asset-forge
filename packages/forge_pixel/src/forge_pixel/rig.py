"""Montagem do personagem por ossos (rig 2D) para testar o encaixe das peças, sem abrir a Unity.

Os dados do esqueleto seguem o layout de um jogo Unity de referência:
- `RigLayout.cs`: ossos (nome, pai, posição em px relativa ao pai, Y para cima; a raiz fica nos pés),
  osso de cada parte e a ordem de desenho (`PartId`, de trás para frente);
- `ClipFactory.cs`: poses (ângulos em graus, +CCW: braço/perna + = para a frente; `hipsY` = altura
  do quadril em px);
- o pivô de cada sprite é o do `.meta` (= tabela de specs do forge_pixel): o pivô fica na origem do osso.

A composição imita o que a Unity faz com sprites de filtro "point": cada pixel de saída (no centro)
é levado ao espaço do sprite pela transformação inversa e pega o texel que cai lá. Em `scale=1` é a
leitura a 1x; em `scale=6` é como a câmera do jogo (~5–6 px de tela por px de arte) mostra a rotação.

    rig = Rig.profile()
    comp = rig.compose(parts, POSES["idle"], scale=1)      # parts: {"thigh_R": Image, ...}
    rep = joint_report(rig, new_parts, cur_parts, POSES)   # emendas: buracos, contorno, largura

Coordenadas: mundo em px de arte, Y para cima, origem na raiz (pés). Sprites com origem no canto
inferior esquerdo (como o `.meta`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image

from .spec import OUTLINE_COLOR, AssetSpec, default_specs
from .palette import hex_to_rgb

Pose = Mapping[str, float]          # osso -> graus (+ "hipsY" -> px)
HIPS_Y = "hipsY"
_EPS = 1e-4                         # desempate de amostras exatamente na borda de um texel


# ---------------------------------------------------------------- dados do jogo

@dataclass(frozen=True)
class Bone:
    name: str
    parent: str
    pos: tuple[float, float]


# RigLayout.Bones (px relativos ao pai; a raiz fica nos pés)
PROFILE_BONES: tuple[Bone, ...] = (
    Bone("hips", "", (0, 14)),
    Bone("thigh_L", "hips", (-1.5, 0)), Bone("shin_L", "thigh_L", (0, -7)),
    Bone("thigh_R", "hips", (1.5, 0)), Bone("shin_R", "thigh_R", (0, -7)),
    Bone("torso", "hips", (0, -1)),
    Bone("head", "torso", (0, 13)),
    Bone("upper_arm_L", "torso", (-1, 12)), Bone("forearm_L", "upper_arm_L", (0, -6)),
    Bone("hand_L", "forearm_L", (0, -6)),
    Bone("upper_arm_R", "torso", (1, 12)), Bone("forearm_R", "upper_arm_R", (0, -6)),
    Bone("hand_R", "forearm_R", (0, -6)),
    Bone("cape_0", "torso", (-3, 12)), Bone("cape_1", "cape_0", (0, -8)),
    Bone("back", "torso", (-4, 6)),
    # soquetes de arma (CharacterRig.MakeSocket): filhos da mão, 1 px abaixo da origem dela
    Bone("weapon_L", "hand_L", (0, -1)), Bone("weapon_R", "hand_R", (0, -1)),
)

# PartId (ordem do enum = ordem de desenho, de trás para frente) -> osso (RigLayout.PartBone)
PROFILE_PARTS: tuple[tuple[str, str], ...] = (
    ("cape_1", "cape_1"), ("cape_0", "cape_0"), ("back", "back"),
    ("arm_up_L", "upper_arm_L"), ("arm_low_L", "forearm_L"), ("hand_L", "hand_L"),
    ("weapon_L", "weapon_L"),          # RigLayout.WeaponSort: logo acima da mão (a de trás fica atrás do tronco)
    ("thigh_L", "thigh_L"), ("shin_L", "shin_L"), ("boot_L", "shin_L"),
    ("pelvis", "hips"), ("thigh_R", "thigh_R"), ("shin_R", "shin_R"), ("boot_R", "shin_R"),
    ("torso", "torso"),
    ("head", "head"), ("hair", "head"), ("hat", "head"),
    ("arm_up_R", "upper_arm_R"), ("arm_low_R", "forearm_R"), ("hand_R", "hand_R"),
    ("weapon_R", "weapon_R"),
)

# parte -> preset de spec (pivô)
PART_SPEC: dict[str, str] = {
    "cape_0": "cape", "cape_1": "cape", "back": "back", "pelvis": "pelvis", "torso": "torso",
    "head": "head", "hair": "hair", "hat": "hat",
    # arma: pivô no topo do cabo (empunhadura), lâmina para baixo; o padrão é a espada, troque com
    # rig.pivots["weapon_R"] = spec.pivot_xy() para adaga/picareta
    "weapon_L": "sword", "weapon_R": "sword",
    **{f"{p}_{s}": ("hand" if p == "hand" else "boot" if p == "boot" else "limb")
       for p in ("arm_up", "arm_low", "hand", "thigh", "shin", "boot") for s in "LR"},
}

# ClipFactory.Idle e quadros-chave de outros clips (com Idle por baixo)
IDLE: dict[str, float] = {
    HIPS_Y: 14, "torso": 0, "head": 0,
    "upper_arm_L": 6, "forearm_L": 4, "hand_L": 75,
    "upper_arm_R": 5, "forearm_R": 25, "hand_R": 120,
    "thigh_L": 4, "shin_L": -4, "thigh_R": -4, "shin_R": 4,
    "cape_0": -4, "cape_1": -6,
}


def _p(**kw: float) -> dict[str, float]:
    d = dict(IDLE)
    d.update({(HIPS_Y if k == "hipsY" else k): v for k, v in kw.items()})
    return d


POSES: dict[str, dict[str, float]] = {
    "idle": dict(IDLE),
    # anim_loco_run_v01 quadro a (contato: perna da frente esticada à frente, a de trás dobrada)
    "run_contact": _p(hipsY=13.2, torso=-8, head=4, thigh_R=38, shin_R=-20, thigh_L=-32, shin_L=-45,
                      upper_arm_R=-20, forearm_R=20, hand_R=110, upper_arm_L=40, forearm_L=-25,
                      cape_0=-28, cape_1=-35),
    # quadro b (passagem: canela de trás bem dobrada)
    "run_pass": _p(hipsY=14.4, torso=-8, head=4, thigh_R=5, shin_R=-55, thigh_L=5, shin_L=-10,
                   upper_arm_R=5, forearm_R=20, hand_R=110, upper_arm_L=5, forearm_L=-20, cape_0=-26, cape_1=-32),
    # anim_loco_fall_v01 (os dois braços erguidos)
    "arms_up": _p(hipsY=14, torso=-2, thigh_R=15, shin_R=-20, thigh_L=-15, shin_L=-15,
                  upper_arm_L=130, forearm_L=10, upper_arm_R=120, forearm_R=20, hand_R=80, cape_0=35, cape_1=50),
    # anim_loco_land_v01 quadro 2 (agachado: joelhos a ~110°, o pior caso do joelho)
    "crouch": _p(hipsY=8, torso=-22, head=11, thigh_R=58, shin_R=-110, thigh_L=50, shin_L=-108,
                 upper_arm_L=35, forearm_L=-10, upper_arm_R=30, forearm_R=30, hand_R=100, cape_0=14, cape_1=22),
}

# braços girados (golpes da espada e picareta): o ombro é a emenda mais exigida (até 205°)
ARM_POSES: dict[str, dict[str, float]] = {
    # anim_sword_slash1_v01 quadro 7 (braço na horizontal, à frente)
    "arm_90": _p(torso=-20, head=6, upper_arm_L=-25, forearm_L=-15, upper_arm_R=106, forearm_R=4, hand_R=0,
                 cape_0=-30, cape_1=-26),
    # slash1 quadro 3 (espada erguida por cima da cabeça)
    "arm_165": _p(torso=10, head=-4, upper_arm_L=25, upper_arm_R=165, forearm_R=20, hand_R=15, cape_0=10, cape_1=6),
    # anim_sword_slash3_v01 quadro 6 (antecipação: braço lá atrás, 205°)
    "arm_205": _p(torso=24, head=-8, upper_arm_L=55, forearm_L=-25, upper_arm_R=205, forearm_R=25, hand_R=15,
                  cape_0=25, cape_1=20),
    # golpe para trás do braço de trás (corrida: braço L à frente, R para trás)
    "arm_back": _p(torso=-8, head=4, upper_arm_R=-40, forearm_R=20, upper_arm_L=130, forearm_L=10, cape_0=-28, cape_1=-35),
}

# capa de −60° a +70° (dash, impulso da parede, queda de costas): a junta capa 0 × capa 1 e o contorno da
# capa contra o torso
CAPE_POSES: dict[str, dict[str, float]] = {
    # anim_loco_dash_v01: corpo em flecha, capa a −60° (segmento 1 a −55° sobre ele)
    "cape_m60": _p(hipsY=11, torso=-32, head=18, thigh_R=55, shin_R=-65, thigh_L=-45, shin_L=-15,
                   upper_arm_L=-70, forearm_L=-10, hand_L=0, upper_arm_R=-68, forearm_R=0, hand_R=10,
                   cape_0=-60, cape_1=-55),
    # anim_loco_walljump_v01: capa a −40°/−45°
    "cape_m40": _p(torso=-18, head=8, upper_arm_L=150, forearm_L=20, upper_arm_R=100, forearm_R=15, hand_R=45,
                   thigh_R=45, shin_R=-60, thigh_L=-40, shin_L=-35, cape_0=-40, cape_1=-45),
    # capa a +70° com o segmento 1 a +10° (valores do quadro "caído" da morte, com o corpo em pé)
    "cape_p70": _p(cape_0=70, cape_1=10),
}

POSE_LABELS: dict[str, str] = {"cape_m60": "capa −60° (dash)", "cape_m40": "capa −40° (impulso da parede)",
                               "cape_p70": "capa +70°","arm_90": "braço a 90° (golpe 1)", "arm_165": "braço a 165° (golpe 1, alto)",
                               "arm_205": "braço a 205° (golpe 3, antecipação)", "arm_back": "braço R para trás, L a 130°",
                               "idle": "parado", "run_contact": "corrida: contato", "run_pass": "corrida: passagem",
                               "arms_up": "braços erguidos (queda)", "crouch": "agachado (pouso)"}


# ---------------------------------------------------------------- layout 3/4 (de frente, virado para a direita)
# spec do personagem 3/4. Os mesmos ossos e PartId do perfil, com offsets
# novos e ordem de desenho nova; o perfil (PROFILE_*) fica intacto. Virar para a esquerda = espelhar (X = −1).
THREEQ_BONES: tuple[Bone, ...] = (
    Bone("hips", "", (0, 14)),
    Bone("thigh_L", "hips", (-3, 0)), Bone("shin_L", "thigh_L", (0, -7)),     # perna de trás
    Bone("thigh_R", "hips", (3, 0)), Bone("shin_R", "thigh_R", (0, -7)),      # perna da frente
    Bone("torso", "hips", (0, -1)),
    Bone("head", "torso", (0, 13)),
    Bone("upper_arm_L", "torso", (-5, 12)), Bone("forearm_L", "upper_arm_L", (0, -6)),
    Bone("hand_L", "forearm_L", (0, -6)),
    Bone("upper_arm_R", "torso", (4, 12)), Bone("forearm_R", "upper_arm_R", (0, -6)),
    Bone("hand_R", "forearm_R", (0, -6)),
    Bone("cape_0", "torso", (0, 12)), Bone("cape_1", "cape_0", (0, -12)),
    Bone("back", "torso", (0, 6)),
    Bone("weapon_L", "hand_L", (0, -1)), Bone("weapon_R", "hand_R", (0, -1)),
)

# ordem de desenho 3/4: capa → braço de trás (e arma secundária) → perna de trás → pelve → tronco → perna da
# frente → cabeça → arma principal → braço da frente (a mão cobre o cabo). A pelve fica atrás do tronco como no
# perfil (a túnica cobre a cintura da calça).
THREEQ_PARTS: tuple[tuple[str, str], ...] = (
    ("cape_1", "cape_1"), ("cape_0", "cape_0"), ("back", "back"),
    ("arm_up_L", "upper_arm_L"), ("arm_low_L", "forearm_L"), ("hand_L", "hand_L"), ("weapon_L", "weapon_L"),
    ("thigh_L", "thigh_L"), ("shin_L", "shin_L"), ("boot_L", "shin_L"),
    ("pelvis", "hips"), ("torso", "torso"),
    ("thigh_R", "thigh_R"), ("shin_R", "shin_R"), ("boot_R", "shin_R"),
    ("head", "head"), ("hair", "head"), ("hat", "head"),
    ("weapon_R", "weapon_R"),
    ("arm_up_R", "upper_arm_R"), ("arm_low_R", "forearm_R"), ("hand_R", "hand_R"),
)

# pivôs que mudam na 3/4 (o resto vem de PART_SPEC): tronco 18x18 (9,0), capa 0 18x16 (9,15), capa 1 18x14 (9,13)
PIVOTS34: dict[str, tuple[float, float]] = {"torso": (9.0, 0.0), "cape_0": (9.0, 15.0), "cape_1": (9.0, 13.0)}

IDLE34: dict[str, float] = {
    HIPS_Y: 14, "torso": 0, "head": 0,
    "upper_arm_L": -3, "forearm_L": 0, "hand_L": 0,      # braços soltos, levemente abertos
    "upper_arm_R": 3, "forearm_R": 4, "hand_R": 20,      # espada da lua apontando para baixo-frente
    "thigh_L": 0, "shin_L": 0, "thigh_R": 0, "shin_R": 0,  # peso nos dois pés, sem inclinação
    "cape_0": 0, "cape_1": 0,
}


def _p34(**kw: float) -> dict[str, float]:
    d = dict(IDLE34)
    d.update({(HIPS_Y if k == "hipsY" else k): v for k, v in kw.items()})
    return d


# poses de teste da spec (§4): parado, passo em tesoura, golpe com o braço a 90°, 165° e 205°.
# Nos golpes o clip troca tronco e cabeça pelas variantes de torção (_b): POSE34_SWAPS.
POSES34: dict[str, dict[str, float]] = {
    "idle": dict(IDLE34),
    "step": _p34(hipsY=13.6, thigh_R=22, shin_R=-12, thigh_L=-18, shin_L=-22,
                 upper_arm_R=-14, forearm_R=10, upper_arm_L=16, forearm_L=-8),
    "strike_90": _p34(torso=-6, head=3, upper_arm_R=90, forearm_R=0, hand_R=0, upper_arm_L=-20, forearm_L=-10),
    "strike_165": _p34(torso=4, head=-2, upper_arm_R=165, forearm_R=10, hand_R=5, upper_arm_L=10),
    "strike_205": _p34(torso=8, head=-4, upper_arm_R=205, forearm_R=20, hand_R=10, upper_arm_L=20, forearm_L=-15),
}
POSE34_SWAPS: dict[str, dict[str, str]] = {p: {"torso": "torso_b", "head": "head_b"}
                                           for p in ("strike_90", "strike_165", "strike_205")}
POSE34_LABELS: dict[str, str] = {"idle": "parado", "step": "passo em tesoura", "strike_90": "golpe: braço a 90°",
                                 "strike_165": "golpe: braço a 165°", "strike_205": "golpe: braço a 205°"}


def sprites_for_pose(sprites: Mapping[str, Image.Image], pose_name: str,
                     swaps: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Image.Image]:
    """Troca peças pelas variantes do quadro (ex.: tronco/cabeça de torção no golpe) quando existirem."""
    out = dict(sprites)
    for part, alt in ((swaps if swaps is not None else POSE34_SWAPS).get(pose_name) or {}).items():
        if alt in sprites:
            out[part] = sprites[alt]
    return out


@dataclass(frozen=True)
class Joint:
    """Emenda entre duas peças: o ponto fica em `offset` (px) no referencial do osso `bone`.
    `parent_end`/`child_end`: qual ponta de cada peça encosta ("top"/"bottom") para comparar a largura;
    None = a peça não tem "ponta" nessa emenda (o ombro sai do meio do torso): só a outra é comparada.
    `rule`: "equal" = as pontas e a largura na emenda ficam iguais às atuais; "cover" = a ponta da filha
    só precisa cobrir a ponta da mãe (barra de casaco sobre a pelve: a silhueta da barra pode mudar),
    e a largura vira só informação (frestas e furos de contorno continuam valendo)."""
    name: str
    parent: str
    child: str
    bone: str
    offset: tuple[float, float] = (0.0, 0.0)
    parent_end: str | None = "bottom"
    child_end: str | None = "top"
    rule: str = "equal"


PROFILE_JOINTS: tuple[Joint, ...] = (
    Joint("cintura", "pelvis", "torso", "torso", parent_end="top", child_end="bottom", rule="cover"),
    Joint("quadril_L", "pelvis", "thigh_L", "thigh_L", parent_end="bottom"),
    Joint("quadril_R", "pelvis", "thigh_R", "thigh_R", parent_end="bottom"),
    Joint("joelho_L", "thigh_L", "shin_L", "shin_L"),
    Joint("joelho_R", "thigh_R", "shin_R", "shin_R"),
    Joint("tornozelo_L", "shin_L", "boot_L", "shin_L", (0, -3)),     # topo do cano da bota
    Joint("tornozelo_R", "shin_R", "boot_R", "shin_R", (0, -3)),
    Joint("cotovelo_L", "arm_up_L", "arm_low_L", "forearm_L"),
    Joint("cotovelo_R", "arm_up_R", "arm_low_R", "forearm_R"),
    Joint("pulso_L", "arm_low_L", "hand_L", "hand_L"),
    Joint("pulso_R", "arm_low_R", "hand_R", "hand_R"),
    # ombro: o braço alto pende do torso (x ±1, y 12); o torso não tem ponta ali
    Joint("ombro_L", "torso", "arm_up_L", "upper_arm_L", parent_end=None),
    Joint("ombro_R", "torso", "arm_up_R", "upper_arm_R", parent_end=None),
    # capa: o segmento 1 pende 8 px abaixo do pivô do segmento 0; o 0 pende do torso (x −3, y 12)
    Joint("capa", "cape_0", "cape_1", "cape_1"),
    Joint("capa_ombro", "torso", "cape_0", "cape_0", parent_end=None),
    # pescoço: a cabeça pende do torso (0, 13); a base do pescoço (y0) termina no pivô
    Joint("pescoco", "torso", "head", "head", parent_end=None, child_end="bottom"),
    # item de costas (aljava): pende do torso em (−4, 6); só frestas/furos e largura na emenda
    Joint("costas", "torso", "back", "back", parent_end=None, child_end=None),
    # empunhadura: o topo do cabo da arma fica no soquete (1 px abaixo da origem da mão)
    Joint("empunhadura_L", "hand_L", "weapon_L", "weapon_L", parent_end=None),
    Joint("empunhadura_R", "hand_R", "weapon_R", "weapon_R", parent_end=None),
)


# ---------------------------------------------------------------- transformações

def _mat(tx: float = 0.0, ty: float = 0.0, deg: float = 0.0) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]])


@dataclass
class Composite:
    """Resultado da montagem. `owner[i, j]` = índice em `order` da parte que pintou o pixel (-1 = vazio)."""
    image: Image.Image
    owner: np.ndarray
    order: list[str]
    x0: float                       # mundo: borda esquerda da imagem
    y1: float                       # mundo: borda de cima da imagem
    scale: int

    def to_pixel(self, x: float, y: float) -> tuple[int, int]:
        """Mundo -> (coluna, linha) da imagem."""
        return int(math.floor((x - self.x0) * self.scale)), int(math.floor((self.y1 - y) * self.scale))


@dataclass
class Rig:
    bones: Sequence[Bone]
    parts: Sequence[tuple[str, str]]                     # (parte, osso) na ordem de desenho
    pivots: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def profile(cls, specs: Mapping[str, AssetSpec] | None = None) -> "Rig":
        specs = specs or default_specs()
        piv = {p: tuple(map(float, specs[s].pivot_xy())) for p, s in PART_SPEC.items()}
        return cls(PROFILE_BONES, PROFILE_PARTS, piv)

    @classmethod
    def three_quarter(cls, specs: Mapping[str, AssetSpec] | None = None) -> "Rig":
        """Layout 3/4 (spec-personagem-3-4.md): mesmos ossos/PartId, offsets e ordem de desenho novos."""
        specs = specs or default_specs()
        piv = {p: tuple(map(float, specs[s].pivot_xy())) for p, s in PART_SPEC.items()}
        piv.update(PIVOTS34)
        return cls(THREEQ_BONES, THREEQ_PARTS, piv)

    @property
    def order(self) -> list[str]:
        return [p for p, _ in self.parts]

    def bone_of(self, part: str) -> str:
        return dict(self.parts)[part]

    # ---- ossos
    def world(self, pose: Pose) -> dict[str, np.ndarray]:
        """Matriz 3x3 (mundo <- osso) de cada osso. O quadril usa pose["hipsY"] no lugar da altura fixa."""
        out: dict[str, np.ndarray] = {}
        for b in self.bones:                      # pais vêm antes dos filhos na tabela
            x, y = b.pos
            if b.name == "hips" and HIPS_Y in pose:
                y = float(pose[HIPS_Y])
            local = _mat(x, y, float(pose.get(b.name, 0.0)))
            out[b.name] = local if not b.parent else out[b.parent] @ local
        return out

    def joint_point(self, joint: Joint, pose: Pose) -> tuple[float, float]:
        m = self.world(pose)[joint.bone]
        p = m @ np.array([joint.offset[0], joint.offset[1], 1.0])
        return float(p[0]), float(p[1])

    def bone_axis(self, bone: str, pose: Pose) -> tuple[float, float]:
        """Direção (unitária, no mundo) do eixo "para baixo" do osso (o segmento pendurado)."""
        m = self.world(pose)[bone]
        v = m[:2, :2] @ np.array([0.0, -1.0])
        return float(v[0]), float(v[1])

    def part_matrix(self, part: str, pose: Pose, world: dict[str, np.ndarray] | None = None) -> np.ndarray:
        """Mundo <- sprite (coordenadas do sprite com origem no canto inferior esquerdo)."""
        w = world or self.world(pose)
        px, py = self.pivots[part]
        return w[self.bone_of(part)] @ _mat(-px, -py)

    # ---- amostragem
    def sample(self, sprites: Mapping[str, Image.Image], pose: Pose, xs: np.ndarray, ys: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray]:
        """Cor RGBA (N, 4) e dono (N,) nos pontos do mundo (xs, ys)."""
        xs = np.asarray(xs, np.float64).ravel()
        ys = np.asarray(ys, np.float64).ravel()
        rgba = np.zeros((xs.size, 4), np.uint8)
        owner = np.full(xs.size, -1, np.int32)
        w = self.world(pose)
        pts = np.stack([xs, ys, np.ones_like(xs)])
        for idx, part in enumerate(self.order):
            img = sprites.get(part)
            if img is None:
                continue
            arr = np.asarray(img.convert("RGBA"))
            h, wd = arr.shape[:2]
            inv = np.linalg.inv(self.part_matrix(part, pose, w))
            sx, sy = (inv @ pts)[:2]
            col = np.floor(sx + _EPS).astype(np.int64)
            rowb = np.floor(sy + _EPS).astype(np.int64)
            ok = (col >= 0) & (col < wd) & (rowb >= 0) & (rowb < h)
            sel = np.nonzero(ok)[0]
            texel = arr[h - 1 - rowb[sel], col[sel]]
            hit = texel[:, 3] > 0
            rgba[sel[hit]] = texel[hit]
            owner[sel[hit]] = idx
        return rgba, owner

    def bounds(self, sprites: Mapping[str, Image.Image], pose: Pose, pad: int = 1) -> tuple[int, int, int, int]:
        """Caixa (x0, y0, x1, y1) inteira, no mundo, que contém todas as peças na pose."""
        w = self.world(pose)
        xs, ys = [], []
        for part in self.order:
            img = sprites.get(part)
            if img is None:
                continue
            m = self.part_matrix(part, pose, w)
            for cx, cy in ((0, 0), (img.width, 0), (0, img.height), (img.width, img.height)):
                p = m @ np.array([cx, cy, 1.0])
                xs.append(p[0])
                ys.append(p[1])
        return (int(math.floor(min(xs))) - pad, int(math.floor(min(ys))) - pad,
                int(math.ceil(max(xs))) + pad, int(math.ceil(max(ys))) + pad)

    def compose(self, sprites: Mapping[str, Image.Image], pose: Pose, scale: int = 1,
                bounds: tuple[int, int, int, int] | None = None) -> Composite:
        """Monta o personagem. `bounds` (x0, y0, x1, y1) no mundo fixa o quadro (todas as poses alinhadas)."""
        x0, y0, x1, y1 = bounds or self.bounds(sprites, pose)
        W, H = (x1 - x0) * scale, (y1 - y0) * scale
        jj, ii = np.meshgrid(np.arange(W), np.arange(H))
        xs = x0 + (jj + 0.5) / scale
        ys = y1 - (ii + 0.5) / scale
        rgba, owner = self.sample(sprites, pose, xs, ys)
        img = Image.fromarray(rgba.reshape(H, W, 4), "RGBA")
        return Composite(img, owner.reshape(H, W), self.order, float(x0), float(y1), scale)


# ---------------------------------------------------------------- medidas de emenda

def end_extent(img: Image.Image, pivot: tuple[float, float], end: str,
               outline: str = OUTLINE_COLOR) -> dict[str, float] | None:
    """Linha de preenchimento (sem contorno) da ponta `end` ("top"/"bottom") da peça: y e x mín/máx
    relativos ao pivô (bordas de pixel: um pixel x ocupa [x, x+1])."""
    arr = np.asarray(img.convert("RGBA"))
    oc = np.all(arr[..., :3] == hex_to_rgb(outline), axis=2)
    fill = (arr[..., 3] > 0) & ~oc
    rows = np.nonzero(fill.any(axis=1))[0]
    if rows.size == 0:
        return None
    r = int(rows[0] if end == "top" else rows[-1])
    xs = np.nonzero(fill[r])[0]
    h = arr.shape[0]
    return {"y": float(h - 1 - r - pivot[1]), "x_min": float(xs[0] - pivot[0]), "x_max": float(xs[-1] + 1 - pivot[0]),
            "width": float(xs[-1] + 1 - xs[0])}


def _border(op: np.ndarray) -> np.ndarray:
    pad = np.pad(op, 1, constant_values=False)
    nb_empty = ~pad[:-2, 1:-1] | ~pad[2:, 1:-1] | ~pad[1:-1, :-2] | ~pad[1:-1, 2:]
    return op & nb_empty


def seam_stats(comp: Composite, point: tuple[float, float], radius: int = 2,
               outline: str = OUTLINE_COLOR) -> dict[str, Any]:
    """Numa janela (2·radius+1)² px em volta do ponto da emenda (montagem a 1x):
    - `gaps`: pixels vazios com opacos dos dois lados (esquerda e direita, ou cima e baixo): fresta;
    - `outline_breaks`: pixels da borda da silhueta que não são da cor do contorno (contorno furado
      ou coberto pelo preenchimento da vizinha)."""
    arr = np.asarray(comp.image)
    op = arr[..., 3] > 0
    oc = np.all(arr[..., :3] == hex_to_rgb(outline), axis=2) & op
    H, W = op.shape
    cx, cy = comp.to_pixel(*point)
    r0, r1 = max(cy - radius, 0), min(cy + radius + 1, H)
    c0, c1 = max(cx - radius, 0), min(cx + radius + 1, W)
    pad = np.pad(op, 1, constant_values=False)
    lr = pad[1:-1, :-2] & pad[1:-1, 2:]
    ud = pad[:-2, 1:-1] & pad[2:, 1:-1]
    gaps = ~op & (lr | ud)
    breaks = _border(op) & ~oc
    win = (slice(r0, r1), slice(c0, c1))
    gy, gx = np.nonzero(gaps[win])
    by, bx = np.nonzero(breaks[win])
    return {"gaps": int(gaps[win].sum()), "outline_breaks": int(breaks[win].sum()),
            "gap_px": [[int(c0 + x), int(r0 + y)] for y, x in zip(gy, gx)],
            "break_px": [[int(c0 + x), int(r0 + y)] for y, x in zip(by, bx)]}


def cross_width(rig: Rig, sprites: Mapping[str, Image.Image], pose: Pose, joint: Joint,
                span: float = 8.0, step: float = 0.125) -> dict[str, float]:
    """Largura da silhueta na emenda: amostra a linha perpendicular ao osso da peça filha, passando
    pelo ponto da emenda, e mede o trecho opaco contínuo que contém o ponto (ou o mais próximo).
    `center` = deslocamento do meio desse trecho em relação ao ponto (px, + = para a frente do osso)."""
    px, py = rig.joint_point(joint, pose)
    ax, ay = rig.bone_axis(rig.bone_of(joint.child), pose)
    nx, ny = -ay, ax                       # perpendicular ("para a frente" quando o osso pende)
    ts = np.arange(-span, span + step / 2, step)
    # só as duas peças da emenda: braço, mão ou a outra perna passando por perto não contam
    pair = {k: v for k, v in sprites.items() if k in (joint.parent, joint.child)}
    rgba, _ = rig.sample(pair, pose, px + ts * nx, py + ts * ny)
    op = rgba[:, 3] > 0
    if not op.any():
        return {"width": 0.0, "center": 0.0}
    mid = int(np.argmin(np.abs(ts)))
    if not op[mid]:
        mid = int(np.nonzero(op)[0][np.argmin(np.abs(np.nonzero(op)[0] - mid))])
    a = b = mid
    while a > 0 and op[a - 1]:
        a -= 1
    while b < op.size - 1 and op[b + 1]:
        b += 1
    lo, hi = ts[a] - step / 2, ts[b] + step / 2
    return {"width": round(float(hi - lo), 3), "center": round(float((hi + lo) / 2), 3)}


def joint_report(rig: Rig, new: Mapping[str, Image.Image], cur: Mapping[str, Image.Image],
                 poses: Mapping[str, Pose], joints: Sequence[Joint] = PROFILE_JOINTS,
                 changed: Sequence[str] | None = None, radius: int = 2, width_tol: float = 0.25
                 ) -> list[dict[str, Any]]:
    """Teste de junção: compara a montagem com as peças novas (`new`) e com as atuais (`cur`) em cada
    pose. Só entram emendas que tocam uma peça trocada (`changed`; padrão = as que diferem).
    Uma emenda passa quando, em toda pose: a largura e o centro da silhueta na emenda ficam a
    ≤ `width_tol` px dos atuais, e não surgem frestas nem furos de contorno a mais que na montagem atual;
    e as pontas das peças (linha de preenchimento que encosta) têm o mesmo x mín/máx das atuais."""
    if changed is None:
        changed = [p for p in new if p in cur and
                   np.asarray(new[p].convert("RGBA")).tobytes() != np.asarray(cur[p].convert("RGBA")).tobytes()]
    out = []
    for j in joints:
        if j.parent not in changed and j.child not in changed:
            continue
        if any(p not in new or p not in cur for p in (j.parent, j.child)):
            continue
        ends = {}
        ends_ok = True
        for role, part, end in (("parent", j.parent, j.parent_end), ("child", j.child, j.child_end)):
            if end is None:
                continue
            en = end_extent(new[part], rig.pivots[part], end)
            ec = end_extent(cur[part], rig.pivots[part], end)
            d = {k: (en[k] - ec[k]) if en and ec else None for k in ("y", "x_min", "x_max")}
            ends[role] = {"part": part, "end": end, "new": en, "cur": ec, "delta": d}
            if j.rule == "equal":
                ends_ok &= all(v == 0 for v in d.values())
        if j.rule == "cover":
            pe = end_extent(new[j.parent], rig.pivots[j.parent], j.parent_end) if j.parent_end else None
            ce = end_extent(new[j.child], rig.pivots[j.child], j.child_end) if j.child_end else None
            ends_ok = bool(pe and ce and ce["x_min"] <= pe["x_min"] and ce["x_max"] >= pe["x_max"])
        per_pose = {}
        ok = ends_ok
        for name, pose in poses.items():
            cn, cc = rig.compose(new, pose), rig.compose(cur, pose)
            pt = rig.joint_point(j, pose)
            sn, sc = seam_stats(cn, pt, radius), seam_stats(cc, pt, radius)
            wn, wc = cross_width(rig, new, pose, j), cross_width(rig, cur, pose, j)
            dw = round(wn["width"] - wc["width"], 3)
            dc = round(wn["center"] - wc["center"], 3)
            same_w = j.rule == "cover" or (abs(dw) <= width_tol and abs(dc) <= width_tol)
            pose_ok = same_w and sn["gaps"] <= sc["gaps"] and sn["outline_breaks"] <= sc["outline_breaks"]
            ok &= pose_ok
            per_pose[name] = {"ok": pose_ok, "width_new": wn["width"], "width_cur": wc["width"], "d_width": dw,
                              "d_center": dc, "gaps_new": sn["gaps"], "gaps_cur": sc["gaps"],
                              "breaks_new": sn["outline_breaks"], "breaks_cur": sc["outline_breaks"],
                              "gap_px_new": sn["gap_px"], "break_px_new": sn["break_px"]}
        out.append({"joint": j.name, "parent": j.parent, "child": j.child, "ok": bool(ok), "ends_ok": bool(ends_ok),
                    "ends": ends, "poses": per_pose})
    return out


def report_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = ["| emenda | pose | largura nova/atual (Δ) | Δ centro | frestas nova/atual | furos de contorno nova/atual | ok |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        for pose, s in r["poses"].items():
            lines.append(f"| {r['joint']} | {pose} | {s['width_new']:.2f}/{s['width_cur']:.2f} ({s['d_width']:+.2f}) | "
                         f"{s['d_center']:+.2f} | {s['gaps_new']}/{s['gaps_cur']} | {s['breaks_new']}/{s['breaks_cur']} | "
                         f"{'sim' if s['ok'] else 'NÃO'} |")
    lines.append("")
    lines.append("| emenda | ponta | peça | x mín..máx nova | atual | Δ |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        for role, e in r["ends"].items():
            n, c = e["new"], e["cur"]
            d = e["delta"]
            lines.append(f"| {r['joint']} | {e['end']} | {e['part']} | {n['x_min']:+g}..{n['x_max']:+g} (y {n['y']:+g}) | "
                         f"{c['x_min']:+g}..{c['x_max']:+g} (y {c['y']:+g}) | "
                         f"{'0' if all(v == 0 for v in d.values()) else d} |")
    return "\n".join(lines)


def covered_outline(rig: Rig, sprites: Mapping[str, Image.Image], pose: Pose, under: str, over: str,
                    max_local_y: float | None = None, outline: str = OUTLINE_COLOR) -> dict[str, Any]:
    """Quantos pixels do contorno da peça `under` ficam cobertos pelo PREENCHIMENTO da peça `over` na
    montagem a 1x (contorno coberto por contorno não conta: a linha continua). `max_local_y` limita aos
    pixels do contorno de `under` com y do sprite ≤ esse valor (ex.: coxa sem a tampa do quadril, y ≤ 8,
    que fica sob o torso por construção). Ex.: barra do casaco (torso) × contorno da coxa R."""
    x0, y0, x1, y1 = rig.bounds(sprites, pose)
    W, H = x1 - x0, y1 - y0
    jj, ii = np.meshgrid(np.arange(W), np.arange(H))
    xs, ys = (x0 + jj + 0.5).ravel(), (y1 - ii - 0.5).ravel()
    full, owner = rig.sample(sprites, pose, xs, ys)
    alone, _ = rig.sample({under: sprites[under]}, pose, xs, ys)
    oc = np.array(hex_to_rgb(outline))
    u_out = (alone[:, 3] > 0) & np.all(alone[:, :3] == oc, axis=1)
    if max_local_y is not None:
        inv = np.linalg.inv(rig.part_matrix(under, pose))
        sy = (inv @ np.stack([xs, ys, np.ones_like(xs)]))[1]
        u_out &= np.floor(sy + _EPS) <= max_local_y
    oi = rig.order.index(over)
    cov = u_out & (owner == oi) & ~np.all(full[:, :3] == oc, axis=1)
    idx = np.nonzero(cov)[0]
    return {"under_outline_px": int(u_out.sum()), "covered_px": int(cov.sum()),
            "covered_at": [[int(x0 + i % W), int(y1 - 1 - i // W)] for i in idx[:16]]}


def visible_area(rig: Rig, sprites: Mapping[str, Image.Image], part: str, poses: Mapping[str, Pose] | Sequence[Pose],
                 ) -> dict[str, Any]:
    """Área visível real de uma peça na montagem (regra do guia de partes: detalhe que o rig cobre não conta).

    Para cada pixel opaco do sprite, leva o CENTRO dele ao mundo em cada pose e pergunta quem é o dono da
    montagem ali: se for a própria peça, o pixel é visível naquela pose. Devolve:
      `mask`    (H, W) bool, linhas de imagem (topo = 0): visível em pelo menos uma pose;
      `always`  (H, W) bool: visível em todas as poses;
      `count`   (H, W) int: em quantas poses;
      `rows`    {y (de baixo): (opacos, visíveis em alguma pose)}, `hidden_rows` = linhas com pixels opacos
                que nunca aparecem; `ratio` = visíveis / opacos."""
    img = np.asarray(sprites[part].convert("RGBA"))
    h, w = img.shape[:2]
    op = img[..., 3] > 0
    rr, cc = np.nonzero(op)
    sx, sy = cc + 0.5, (h - 1 - rr) + 0.5
    pl = list(poses.values()) if isinstance(poses, Mapping) else list(poses)
    idx = rig.order.index(part)
    count = np.zeros((h, w), np.int32)
    for pose in pl:
        m = rig.part_matrix(part, pose)
        wx, wy = (m @ np.stack([sx, sy, np.ones_like(sx)]))[:2]
        _, owner = rig.sample(sprites, pose, wx, wy)
        count[rr[owner == idx], cc[owner == idx]] += 1
    mask = count > 0
    rows = {}
    for r in range(h):
        n = int(op[r].sum())
        if n:
            rows[h - 1 - r] = (n, int(mask[r].sum()))
    return {"mask": mask, "always": count == len(pl), "count": count, "rows": rows,
            "hidden_rows": sorted(y for y, (n, v) in rows.items() if v == 0),
            "ratio": round(float(mask.sum()) / max(1, int(op.sum())), 3)}


def seam_report(rig: Rig, sprites: Mapping[str, Image.Image], poses: Mapping[str, Pose],
                joints: Sequence[Joint] = PROFILE_JOINTS, radius: int = 2,
                swaps: Mapping[str, Mapping[str, str]] | None = None) -> list[dict[str, Any]]:
    """Emendas de um conjunto NOVO sem montagem de referência (ex.: a visão 3/4): para cada junta presente,
    a diferença entre as pontas das duas peças ("degrau", x mín/máx do preenchimento relativo ao pivô; só quando
    as duas têm ponta) e, em cada pose (a 1x), as frestas e os furos de contorno na janela da emenda.
    `swaps` = POSE34_SWAPS para trocar tronco/cabeça nos golpes."""
    out = []
    for j in joints:
        if j.parent not in sprites or j.child not in sprites:
            continue
        step = None
        if j.parent_end and j.child_end:
            pe = end_extent(sprites[j.parent], rig.pivots[j.parent], j.parent_end)
            ce = end_extent(sprites[j.child], rig.pivots[j.child], j.child_end)
            if pe and ce:
                step = {"left": ce["x_min"] - pe["x_min"], "right": ce["x_max"] - pe["x_max"],
                        "parent": [pe["x_min"], pe["x_max"]], "child": [ce["x_min"], ce["x_max"]]}
        per = {}
        for name, pose in poses.items():
            sp = sprites_for_pose(sprites, name, swaps if swaps is not None else {})
            comp = rig.compose(sp, pose)
            st = seam_stats(comp, rig.joint_point(j, pose), radius)
            per[name] = {"gaps": st["gaps"], "outline_breaks": st["outline_breaks"],
                         "gap_px": st["gap_px"], "break_px": st["break_px"]}
        out.append({"joint": j.name, "parent": j.parent, "child": j.child, "step": step, "poses": per,
                    "gaps": sum(v["gaps"] for v in per.values()),
                    "outline_breaks": sum(v["outline_breaks"] for v in per.values())})
    return out

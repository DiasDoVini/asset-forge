"""Presets de sistemas de partículas (funcionam sem LLM) e seleção por prompt."""
from __future__ import annotations

import copy
import re
from typing import Any

from .library import _stem, prompt_colors
from .particles import ParticleSystem, tint_gradient

E = dict  # atalho de legibilidade

PRESETS: dict[str, dict[str, Any]] = {
    "fire": {"keywords": ["fire", "flame", "campfire", "torch", "burning", "fogo", "chama", "fogueira", "tocha", "queimando"],
             "description": "Fogueira com chamas, brasas e um pouco de fumaça.", "duration": 2.0, "loop": True,
             "emitters": [
                 E(name="flames", texture="soft", blend="add", rate=60, shape="line", width=40, position=[0, -80], direction=90, spread=12,
                   speed=[40, 80], lifetime=[0.6, 1.0], gravity=[0, 60], size=[26, 40], size_curve=[[0, 0.6], [0.3, 1], [1, 0.1]],
                   turbulence=30, color=[[0, "#fff2b0ff"], [0.25, "#ffb030e0"], [0.6, "#ff4010a0"], [1, "#60100000"]]),
                 E(name="embers", texture="circle", blend="add", rate=8, shape="line", width=40, position=[0, -75], direction=90, spread=25,
                   speed=[60, 120], lifetime=[0.8, 1.6], size=[2, 4], turbulence=60, size_curve=[[0, 1], [1, 0.3]],
                   color=[[0, "#ffd070ff"], [0.7, "#ff6010ff"], [1, "#ff200000"]]),
                 E(name="smoke", texture="smoke", blend="alpha", rate=5, position=[0, 0], direction=90, spread=10, speed=[20, 35],
                   lifetime=[1.5, 2.2], size=[30, 45], size_curve=[[0, 0.6], [1, 1.6]], spin=[-30, 30], start_delay=0.3,
                   color=[[0, "#40383000"], [0.2, "#40383060"], [1, "#30303000"]]),
             ]},
    "smoke": {"keywords": ["smoke", "fog", "mist", "steam", "chimney", "fumaça", "névoa", "neblina", "vapor", "chaminé"],
              "description": "Coluna de fumaça subindo e se espalhando.", "duration": 3.0, "loop": True,
              "emitters": [E(name="smoke", texture="smoke", blend="alpha", rate=10, shape="circle", radius=10, position=[0, -90],
                             direction=90, spread=12, speed=[30, 50], lifetime=[2.0, 3.0], size=[36, 50], size_curve=[[0, 0.5], [1, 2.0]],
                             spin=[-25, 25], rotation=[0, 360], turbulence=12, drag=0.3,
                             color=[[0, "#8a8a9000"], [0.15, "#8a8a90b0"], [1, "#6a6a7000"]])]},
    "sparks": {"keywords": ["spark", "sparks", "welding", "grind", "metal hit", "faísca", "faíscas", "solda", "metal"],
               "description": "Chuva de faíscas alongadas caindo com gravidade.", "duration": 1.0, "loop": True,
               "emitters": [E(name="sparks", texture="spark", blend="add", rate=0, bursts=[{"time": 0, "count": 40}], direction=60,
                              spread=50, speed=[120, 260], lifetime=[0.4, 0.8], gravity=[0, -500], drag=1.0, size=[6, 9],
                              stretch=1.5, size_curve=[[0, 1], [1, 0.4]],
                              color=[[0, "#ffffffff"], [0.3, "#ffe080ff"], [1, "#ff600000"]])]},
    "explosion": {"keywords": ["explosion", "explode", "blast", "boom", "grenade", "detonation", "explosão", "explodir", "bomba", "granada"],
                  "description": "Explosão: flash, bola de fogo, faíscas e fumaça (one-shot).", "duration": 1.6, "loop": False,
                  "emitters": [
                      E(name="flash", texture="flare", blend="add", rate=0, bursts=[{"time": 0, "count": 1}], speed=[0, 0],
                        lifetime=[0.25, 0.25], size=[160, 160], size_curve=[[0, 0.4], [0.3, 1], [1, 1.2]],
                        color=[[0, "#ffffffff"], [1, "#ffd08000"]]),
                      E(name="fireball", texture="soft", blend="add", rate=0, bursts=[{"time": 0, "count": 40}], shape="circle", radius=10,
                        spread=180, speed=[40, 140], drag=3.0, lifetime=[0.4, 0.8], size=[30, 55], size_curve=[[0, 0.5], [0.3, 1], [1, 0.6]],
                        color=[[0, "#fff4c0ff"], [0.3, "#ffa020f0"], [0.7, "#d03008a0"], [1, "#30100000"]]),
                      E(name="sparks", texture="spark", blend="add", rate=0, bursts=[{"time": 0.02, "count": 30}], spread=180,
                        speed=[200, 380], drag=2.0, gravity=[0, -200], lifetime=[0.3, 0.7], size=[6, 8], stretch=2.0,
                        color=[[0, "#ffffffff"], [0.4, "#ffc050ff"], [1, "#ff500000"]]),
                      E(name="smoke", texture="smoke", blend="alpha", rate=0, bursts=[{"time": 0.15, "count": 18}], shape="circle",
                        radius=20, spread=180, speed=[20, 60], drag=1.5, gravity=[0, 25], lifetime=[0.9, 1.4], size=[45, 70],
                        size_curve=[[0, 0.6], [1, 1.5]], spin=[-40, 40], rotation=[0, 360],
                        color=[[0, "#3a302800"], [0.2, "#3a3028c0"], [1, "#30303000"]]),
                  ]},
    "magic_burst": {"keywords": ["magic", "spell", "cast", "arcane", "mana", "burst", "magia", "feitiço", "arcano", "mana", "conjurar"],
                    "description": "Explosão de estrelas mágicas girando para fora (one-shot).", "duration": 1.2, "loop": False,
                    "emitters": [
                        E(name="ring", texture="ring", blend="add", rate=0, bursts=[{"time": 0, "count": 1}], speed=[0, 0], lifetime=[0.5, 0.5],
                          size=[40, 40], size_curve=[[0, 0.3], [1, 4]], color=[[0, "#d0a0ffff"], [1, "#8040ff00"]]),
                        E(name="stars", texture="star", blend="add", rate=0, bursts=[{"time": 0, "count": 35}], spread=180, speed=[80, 180],
                          drag=2.5, tangential_accel=200, lifetime=[0.6, 1.1], size=[10, 18], spin=[-180, 180],
                          color=[[0, "#ffffffff"], [0.3, "#d090ffff"], [1, "#6020ff00"]]),
                        E(name="dust", texture="soft", blend="add", rate=0, bursts=[{"time": 0.05, "count": 25}], shape="circle", radius=30,
                          spread=180, speed=[10, 40], gravity=[0, 30], lifetime=[0.6, 1.0], size=[4, 8], brightness_variation=0.5,
                          color=[[0, "#ffffffff"], [1, "#a060ff00"]]),
                    ]},
    "heal": {"keywords": ["heal", "healing", "regen", "regeneration", "buff", "restore", "cura", "curar", "regeneração", "restaurar"],
             "description": "Partículas verdes subindo em volta do personagem (loop).", "duration": 2.0, "loop": True,
             "emitters": [
                 E(name="plus", texture="star", blend="add", rate=10, shape="box", width=80, height=20, position=[0, -70], direction=90,
                   spread=5, speed=[40, 70], lifetime=[1.0, 1.5], size=[10, 16], size_curve=[[0, 0], [0.2, 1], [1, 0]],
                   color=[[0, "#c0ffc0ff"], [1, "#40ff6000"]]),
                 E(name="glow", texture="soft", blend="add", rate=20, shape="box", width=90, height=10, position=[0, -80], direction=90,
                   spread=3, speed=[20, 50], lifetime=[0.8, 1.4], size=[6, 12], brightness_variation=0.4,
                   color=[[0, "#80ff90c0"], [1, "#20ff4000"]]),
             ]},
    "poison": {"keywords": ["poison", "toxic", "acid", "bubbles", "swamp", "venom", "veneno", "tóxico", "ácido", "bolhas", "pântano"],
               "description": "Bolhas tóxicas subindo e estourando (loop).", "duration": 2.0, "loop": True,
               "emitters": [E(name="bubbles", texture="ring", blend="add", rate=12, shape="box", width=90, height=10, position=[0, -80],
                              direction=90, spread=10, speed=[25, 50], turbulence=40, lifetime=[0.9, 1.6], size=[8, 18],
                              size_curve=[[0, 0.3], [0.8, 1], [1, 1.4]],
                              color=[[0, "#a0ff4000"], [0.2, "#a0ff40e0"], [0.85, "#60d020c0"], [1, "#60d02000"]]),
                            E(name="mist", texture="smoke", blend="alpha", rate=4, shape="box", width=90, height=10, position=[0, -85],
                              direction=90, spread=20, speed=[8, 15], lifetime=[1.5, 2.0], size=[40, 60], spin=[-20, 20],
                              color=[[0, "#50a02000"], [0.3, "#50a02060"], [1, "#40801000"]])]},
    "rain": {"keywords": ["rain", "drizzle", "storm", "chuva", "garoa", "tempestade"],
             "description": "Chuva caindo inclinada (loop, cobre o canvas).", "duration": 1.0, "loop": True,
             "emitters": [E(name="drops", texture="spark", blend="add", rate=180, shape="line", width=320, position=[20, 140], direction=-100,
                            spread=2, speed=[520, 620], lifetime=[0.5, 0.6], size=[6, 8], stretch=2.5,
                            color=[[0, "#a0c8ff90"], [1, "#a0c8ff90"]])]},
    "snow": {"keywords": ["snow", "snowfall", "blizzard", "winter", "neve", "nevasca", "inverno"],
             "description": "Flocos de neve caindo devagar com vento (loop).", "duration": 4.0, "loop": True,
             "emitters": [E(name="flakes", texture="soft", blend="add", rate=30, shape="line", width=300, position=[0, 140], direction=-90,
                            spread=10, speed=[20, 45], turbulence=25, lifetime=[5.0, 7.0], size=[5, 10], brightness_variation=0.3,
                            color=[[0, "#ffffff00"], [0.1, "#ffffffe0"], [0.9, "#ffffffe0"], [1, "#ffffff00"]])]},
    "dust": {"keywords": ["dust", "dirt", "landing", "footstep", "debris", "poeira", "terra", "pouso", "passo", "detritos"],
             "description": "Nuvem de poeira no chão (pouso/passo, one-shot).", "duration": 1.0, "loop": False,
             "emitters": [E(name="puffs", texture="smoke", blend="alpha", rate=0, bursts=[{"time": 0, "count": 14}], shape="line", width=30,
                            position=[0, -90], direction=90, spread=80, speed=[40, 110], drag=4.0, gravity=[0, 20], lifetime=[0.5, 0.9],
                            size=[20, 34], size_curve=[[0, 0.5], [1, 1.4]], rotation=[0, 360], spin=[-60, 60],
                            color=[[0, "#b09070d0"], [1, "#a0886000"]])]},
    "coins": {"keywords": ["coin", "coins", "gold", "loot", "treasure", "reward", "moeda", "moedas", "ouro", "tesouro", "recompensa"],
              "description": "Moedas/brilhos dourados saltando e caindo (one-shot).", "duration": 1.4, "loop": False,
              "emitters": [E(name="coins", texture="circle", blend="alpha", rate=0, bursts=[{"time": 0, "count": 16}], direction=90, spread=40,
                             speed=[160, 260], gravity=[0, -600], lifetime=[0.9, 1.2], size=[10, 12], size_curve=[[0, 1], [1, 1]],
                             color=[[0, "#ffd040ff"], [0.85, "#ffb020ff"], [1, "#ffb02000"]]),
                           E(name="glints", texture="star", blend="add", rate=0, bursts=[{"time": 0.05, "count": 12}], spread=180,
                             speed=[40, 120], drag=2.0, lifetime=[0.4, 0.8], size=[10, 16], spin=[-90, 90],
                             color=[[0, "#ffffffff"], [1, "#ffe08000"]])]},
    "hit": {"keywords": ["hit", "impact", "slash", "strike", "punch", "damage", "acerto", "impacto", "golpe", "soco", "dano", "corte"],
            "description": "Impacto de golpe: estrela de faíscas curta (one-shot).", "duration": 0.5, "loop": False,
            "emitters": [E(name="flash", texture="flare", blend="add", rate=0, bursts=[{"time": 0, "count": 1}], speed=[0, 0],
                           lifetime=[0.12, 0.12], size=[70, 70], color=[[0, "#ffffffff"], [1, "#ffffff00"]]),
                         E(name="streaks", texture="spark", blend="add", rate=0, bursts=[{"time": 0, "count": 10}], spread=180,
                           speed=[250, 400], drag=8.0, lifetime=[0.15, 0.3], size=[10, 14], stretch=1.2,
                           color=[[0, "#ffffffff"], [1, "#ffd08000"]])]},
    "fireflies": {"keywords": ["firefly", "fireflies", "wisp", "forest", "ambient light", "vaga-lume", "vaga-lumes", "floresta", "luzes"],
                  "description": "Vaga-lumes flutuando e piscando (ambiente, loop).", "duration": 4.0, "loop": True,
                  "emitters": [E(name="flies", texture="soft", blend="add", rate=4, shape="box", width=220, height=180, spread=180,
                                 speed=[5, 15], turbulence=30, lifetime=[3.0, 5.0], size=[8, 14],
                                 size_curve=[[0, 0], [0.2, 1], [0.4, 0.5], [0.6, 1], [0.8, 0.6], [1, 0]],
                                 color=[[0, "#e0ff80ff"], [1, "#a0ff4000"]])]},
    "portal_swirl": {"keywords": ["portal", "vortex", "swirl", "warp", "black hole", "vórtice", "redemoinho", "buraco negro"],
                     "description": "Partículas espiralando para dentro de um ponto (loop).", "duration": 2.0, "loop": True,
                     "emitters": [E(name="swirl", texture="soft", blend="add", rate=60, shape="ring", radius=100, direction=0, spread=180,
                                    speed=[0, 10], radial_accel=-180, tangential_accel=260, drag=0.8, lifetime=[1.0, 1.4], size=[5, 10],
                                    color=[[0, "#6040ff00"], [0.2, "#8060ffff"], [0.8, "#40e0ffff"], [1, "#ffffff00"]])]},
    "confetti": {"keywords": ["confetti", "celebration", "party", "victory", "level up", "confete", "festa", "vitória", "comemoração"],
                 "description": "Confete colorido explodindo e caindo (one-shot).", "duration": 2.5, "loop": False,
                 "emitters": [E(name="confetti", texture="square", blend="alpha", rate=0, bursts=[{"time": 0, "count": 60}], position=[0, -60],
                                direction=90, spread=35, speed=[250, 420], gravity=[0, -350], drag=1.5, turbulence=60, lifetime=[1.8, 2.4],
                                size=[6, 10], rotation=[0, 360], spin=[-400, 400], brightness_variation=0.3, random_color=True,
                                color=[[0, "#ff4080ff"], [0.33, "#40c0ffff"], [0.66, "#ffd040ff"], [0.95, "#60ff80ff"], [1, "#60ff8000"]])]},
}


def pick_preset(prompt: str) -> str:
    text = prompt.lower()
    best, score = "magic_burst", 0
    for name, p in PRESETS.items():
        s = sum(len(k) for k in p["keywords"] if re.search(rf"\b{re.escape(_stem(k))}", text))
        if s > score:
            best, score = name, s
    return best


def from_preset(name: str, prompt: str = "", *, colors: list[list[float]] | None = None, scale: float = 1.0,
                speed: float = 1.0, amount: float = 1.0) -> ParticleSystem:
    if name not in PRESETS:
        raise ValueError(f"preset desconhecido: {name} (tem: {', '.join(PRESETS)})")
    d = copy.deepcopy(PRESETS[name])
    d.pop("keywords")
    cols = colors if colors is not None else prompt_colors(prompt)
    for i, em in enumerate(d["emitters"]):
        if cols and em.get("blend", "add") == "add":
            em["color"] = tint_gradient(em["color"], cols[i % len(cols)])
        for k in ("size", "speed"):
            if k in em:
                em[k] = [x * scale * (speed if k == "speed" else 1) for x in em[k]]
        for k in ("radius", "width", "height"):
            if k in em:
                em[k] = em[k] * scale
        if "position" in em:
            em["position"] = [x * scale for x in em["position"]]
        if "gravity" in em:
            em["gravity"] = [x * scale for x in em["gravity"]]
        if amount != 1.0:
            if "rate" in em:
                em["rate"] = em["rate"] * amount
            for b in em.get("bursts", []):
                b["count"] = max(1, round(b["count"] * amount))
    return ParticleSystem(**d, name=name, preset=name)

"""Biblioteca de shaders prontos (funcionam sem LLM) + seleção por palavras-chave e cores do prompt."""
from __future__ import annotations

import re
from typing import Any

from .shaders import ShaderDef


def _u(name: str, type_: str, default: Any, mn: float | None = None, mx: float | None = None, label: str = "") -> dict:
    return {"name": name, "type": type_, "default": default if isinstance(default, list) else [default],
            "min": mn, "max": mx, "label": label or name[1:]}


TEMPLATES: dict[str, dict[str, Any]] = {}


def _t(name: str, description: str, keywords: list[str], code: str, uniforms: list[dict], *, blend: str = "add",
       mode: str = "overlay", uses_texture: bool = False, one_shot: bool = False, duration: float = 2.0) -> None:
    TEMPLATES[name] = {"name": name, "description": description, "keywords": keywords, "blend": blend, "mode": mode,
                       "uses_texture": uses_texture, "one_shot": one_shot, "duration": duration,
                       "uniforms": uniforms, "code": code.strip() + "\n", "template": name}


_t("fire", "Chama de fogueira/tocha subindo, com núcleo quente.", ["fire", "flame", "torch", "burn", "fogo", "chama", "tocha", "fogueira", "inferno"], """
vec4 effect(vec2 uv) {
    float t = uTime * uSpeed;
    float n = fbm(vec2(uv.x * 3.0, uv.y * 2.0 - t * 1.8));
    float n2 = fbm(vec2(uv.x * 6.0 + 3.0, uv.y * 5.0 - t * 3.5));
    float x = (uv.x - 0.5 + (n - 0.5) * 0.45 * uv.y) * 2.0 / uSize;
    float y = uv.y + (n2 - 0.5) * 0.2;
    float width = 0.55 * sqrt(max(0.0, 1.0 - y)) * smoothstep(-0.05, 0.25, y);
    float d = abs(x) - width;
    float body = smoothstep(0.08, -0.08, d) * smoothstep(0.0, 0.06, uv.y);
    float heat = clamp((width - abs(x)) * 2.4 * (1.15 - uv.y) + n2 * 0.25, 0.0, 1.0);
    vec3 col = mix(uColorOuter.rgb, uColorInner.rgb, smoothstep(0.2, 0.7, heat));
    col = mix(col, vec3(1.0, 1.0, 0.9), smoothstep(0.8, 1.0, heat));
    return vec4(col * uIntensity, body);
}
""", [_u("uColorInner", "color", [1.0, 0.82, 0.3, 1.0], label="Núcleo"), _u("uColorOuter", "color", [1.0, 0.25, 0.04, 1.0], label="Borda"),
      _u("uSpeed", "float", 1.0, 0.0, 4.0, "Velocidade"), _u("uSize", "float", 1.0, 0.3, 1.6, "Largura"),
      _u("uIntensity", "float", 1.2, 0.0, 3.0, "Intensidade")])

_t("energy_shield", "Escudo/bolha de energia com borda brilhante, células e pulso.", ["shield", "barrier", "bubble", "force field", "forcefield", "escudo", "barreira", "bolha", "proteção"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - 0.5) * 2.0;
    float r = length(p);
    float inside = smoothstep(1.0, 0.97, r);
    float rim = pow(clamp(r, 0.0, 1.0), 5.0);
    float cells = voronoi(p * uCells + vec2(0.0, uTime * 0.3));
    float edges = smoothstep(0.32, 0.5, cells);
    float wave = smoothstep(0.1, 0.0, abs(r - fract(uTime * 0.6)));
    float a = (rim * 1.1 + edges * 0.3 * (0.3 + rim) + wave * 0.35 + 0.06) * inside;
    vec3 col = uColor.rgb * (0.7 + rim * 0.8) + vec3(1.0) * wave * 0.3;
    return vec4(col, clamp(a * uIntensity, 0.0, 1.0));
}
""", [_u("uColor", "color", [0.3, 0.75, 1.0, 1.0], label="Cor"), _u("uCells", "float", 4.0, 1.0, 12.0, "Células"),
      _u("uIntensity", "float", 1.0, 0.0, 3.0, "Intensidade")])

_t("portal", "Portal/vórtice girando com braços espirais e núcleo claro.", ["portal", "vortex", "warp", "wormhole", "swirl", "vórtice", "redemoinho", "teleporte", "fenda"], """
vec4 effect(vec2 uv) {
    vec2 pc = polar(uv);
    float r = pc.x * 2.0;
    float t = uTime * uSpeed;
    float swirl = pc.y + uTwist / (r + 0.2) - t;
    vec2 q = rot2(vec2(r * 3.0, 0.0), swirl);
    float n = fbm(q * 1.4 + vec2(10.0, -t * 0.5));
    float ring = smoothstep(1.0, 0.7, r) * smoothstep(0.05, 0.35, r);
    float arms = smoothstep(0.4, 0.75, n) * ring;
    float core = smoothstep(0.45, 0.0, r);
    float rim = smoothstep(0.08, 0.0, abs(r - 0.9));
    vec3 col = mix(uColorA.rgb, uColorB.rgb, smoothstep(0.3, 0.8, n));
    col = mix(col, vec3(1.0), core * 0.7) + rim * uColorB.rgb;
    float a = clamp(arms + core + rim + ring * 0.15, 0.0, 1.0) * smoothstep(1.0, 0.93, r);
    return vec4(col, a);
}
""", [_u("uColorA", "color", [0.45, 0.1, 0.9, 1.0], label="Cor externa"), _u("uColorB", "color", [0.2, 0.9, 1.0, 1.0], label="Cor interna"),
      _u("uSpeed", "float", 1.5, 0.0, 6.0, "Velocidade"), _u("uTwist", "float", 1.2, 0.0, 4.0, "Torção")], duration=4.0)

_t("magic_circle", "Círculo mágico girando com anéis, runas e hexagrama.", ["magic circle", "rune", "summon", "spell circle", "sigil", "círculo mágico", "runa", "invocação", "selo"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - 0.5) * 2.0;
    float r = length(p);
    float t = uTime * uSpeed;
    vec2 q = rot2(p, t * 0.5);
    float a = atan(q.y, q.x);
    float ring1 = smoothstep(0.02, 0.0, abs(r - 0.9));
    float ring2 = smoothstep(0.015, 0.0, abs(r - 0.72));
    float ring3 = smoothstep(0.012, 0.0, abs(r - 0.4));
    float cellsN = 24.0;
    float s = (a + 3.14159265) / 6.2831853 * cellsN;
    float h = hash11(floor(s) + 7.0);
    float fa = fract(s);
    float ry = (r - 0.75) / 0.12;
    float band = step(0.75, r) * step(r, 0.87);
    float bar = step(abs(fa - 0.5), 0.07 + 0.18 * h) * step(abs(ry - 0.5), 0.07);
    float stem = step(abs(fa - 0.5), 0.05) * step(abs(ry - 0.5), 0.3 + 0.15 * h);
    float glyph = band * max(bar, stem);
    vec2 sp = rot2(p, -t * 0.8);
    float star = 0.0;
    for (int i = 0; i < 6; i++) {
        vec2 d = rot2(sp, float(i) * 1.0471976);
        star = max(star, smoothstep(0.012, 0.0, abs(d.y - 0.36)) * step(abs(d.x), 0.62));
    }
    star *= step(r, 0.72);
    float m = max(max(ring1, ring2), max(ring3, max(glyph, star)));
    float halo = exp(-abs(r - 0.9) * 30.0) * 0.35;
    float pulse = 0.8 + 0.2 * sin(uTime * 3.0);
    float alpha = clamp((m + halo) * uIntensity * pulse, 0.0, 1.0) * step(r, 1.0);
    return vec4(mix(uColor.rgb, vec3(1.0), m * 0.35), alpha);
}
""", [_u("uColor", "color", [1.0, 0.75, 0.25, 1.0], label="Cor"), _u("uSpeed", "float", 0.6, 0.0, 3.0, "Rotação"),
      _u("uIntensity", "float", 1.0, 0.0, 3.0, "Intensidade")], duration=4.0)

_t("lightning", "Raio/arco elétrico vertical com tremulação.", ["lightning", "electric", "thunder", "bolt", "zap", "shock", "raio", "elétrico", "trovão", "choque"], """
float boltDist(vec2 p, float seed, float t) {
    float x = 0.0;
    float amp = 0.2;
    float freq = 3.0;
    for (int i = 0; i < 5; i++) {
        x += (vnoise(vec2(p.y * freq + seed * 13.0, t + seed * 5.0)) - 0.5) * amp;
        amp *= 0.55;
        freq *= 2.1;
    }
    return abs(p.x - 0.5 - x);
}

vec4 effect(vec2 uv) {
    float t = floor(uTime * uFlicker);
    float d = boltDist(uv, 1.0, t);
    float d2 = boltDist(uv, 2.0, t + 3.0);
    float w = 0.006 * uWidth;
    float core = smoothstep(w, 0.0, d);
    float g = w * 2.0 / (d + w * 2.0);
    float g2 = w / (d2 + w) * 0.45;
    float fade = smoothstep(0.0, 0.08, uv.y) * smoothstep(1.0, 0.92, uv.y);
    float flash = 0.55 + 0.45 * hash11(t);
    float i = (core + g * 0.55 + g2 * 0.5) * fade * flash;
    return vec4(mix(uColor.rgb, vec3(1.0), core), clamp(i, 0.0, 1.0));
}
""", [_u("uColor", "color", [0.55, 0.75, 1.0, 1.0], label="Cor"), _u("uFlicker", "float", 14.0, 1.0, 40.0, "Trocas por segundo"),
      _u("uWidth", "float", 1.0, 0.3, 4.0, "Espessura")], duration=1.0)

_t("plasma_orb", "Orbe de energia/plasma com veios e halo.", ["orb", "plasma", "energy ball", "fireball", "sphere", "core", "orbe", "esfera", "plasma", "bola de energia"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - 0.5) * 2.0;
    float r = length(p);
    float t = uTime * uSpeed;
    float n = fbm(rot2(p, t * 0.6) * 2.5 + vec2(t, -t * 0.7));
    float n2 = fbm(rot2(p, -t * 0.4) * 4.0 - vec2(t * 0.5, t * 0.3));
    float radius = uRadius + (n - 0.5) * 0.12;
    float body = smoothstep(radius, radius - 0.12, r);
    float veins = smoothstep(0.55, 0.72, n2) * body;
    float halo = exp(-max(r - radius * 0.85, 0.0) * 5.0) * 0.55 * (1.0 - body);
    vec3 col = mix(uColorA.rgb, uColorB.rgb, n) * (0.7 + body * 0.5) + veins * 0.8;
    float a = clamp(body * 0.95 + halo + veins, 0.0, 1.0);
    return vec4(col, a);
}
""", [_u("uColorA", "color", [0.15, 0.4, 1.0, 1.0], label="Cor A"), _u("uColorB", "color", [0.6, 0.95, 1.0, 1.0], label="Cor B"),
      _u("uRadius", "float", 0.55, 0.2, 0.9, "Raio"), _u("uSpeed", "float", 1.0, 0.0, 4.0, "Velocidade")])

_t("shockwave", "Onda de choque circular que expande e some (one-shot).", ["shockwave", "shock wave", "ring", "pulse", "impact wave", "nova", "onda de choque", "anel", "pulso", "impacto"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - 0.5) * 2.0;
    float r = length(p);
    float pr = uProgress;
    float radius = (1.0 - pow(1.0 - pr, 2.0)) * 0.95;
    float width = mix(0.2, 0.025, pr) * uThickness;
    float ring = smoothstep(width, 0.0, abs(r - radius));
    float n = vnoise(p / max(r, 0.001) * 3.0 + vec2(pr * 2.0, 0.0));
    float fade = 1.0 - smoothstep(0.5, 1.0, pr);
    float a = ring * fade * (0.65 + 0.35 * n);
    vec3 col = mix(uColor.rgb, vec3(1.0), ring * fade * 0.5);
    return vec4(col, clamp(a * uIntensity, 0.0, 1.0));
}
""", [_u("uColor", "color", [0.6, 0.85, 1.0, 1.0], label="Cor"), _u("uThickness", "float", 1.0, 0.2, 3.0, "Espessura"),
      _u("uIntensity", "float", 1.2, 0.0, 3.0, "Intensidade")], one_shot=True, duration=0.8)

_t("explosion", "Explosão com bola de fogo e fumaça (one-shot).", ["explosion", "explode", "blast", "boom", "detonation", "grenade", "explosão", "explodir", "bomba", "detonação"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - 0.5) * 2.0;
    float pr = uProgress;
    float r = length(p);
    float grow = 1.0 - pow(1.0 - pr, 3.0);
    float radius = mix(0.1, 0.85, grow);
    vec2 dir = p / max(r, 0.001);
    float n = fbm(dir * 2.0 + p * 2.5 + vec2(pr * 2.0, pr));
    float edge = radius * (0.75 + 0.5 * n);
    float body = smoothstep(edge, edge - 0.25, r);
    float heat = clamp((1.0 - r / max(edge, 0.001)) * 1.5 - pr * 1.3 + n * 0.4, 0.0, 1.0);
    vec3 col = mix(uColorFire.rgb, uColorHot.rgb, smoothstep(0.3, 0.7, heat));
    col = mix(col, vec3(1.0, 1.0, 0.85), smoothstep(0.75, 1.0, heat));
    col = mix(uColorSmoke.rgb, col, smoothstep(0.05, 0.35, heat));
    float alpha = body * (1.0 - smoothstep(0.6, 1.0, pr));
    return vec4(col * (0.7 + heat * 0.6), clamp(alpha, 0.0, 1.0));
}
""", [_u("uColorHot", "color", [1.0, 0.85, 0.3, 1.0], label="Núcleo"), _u("uColorFire", "color", [0.95, 0.35, 0.05, 1.0], label="Fogo"),
      _u("uColorSmoke", "color", [0.18, 0.16, 0.15, 1.0], label="Fumaça")], blend="alpha", one_shot=True, duration=1.2)

_t("dissolve", "Sprite se desintegrando com borda incandescente (one-shot, usa textura).", ["dissolve", "disintegrate", "burn away", "fade out", "death", "vanish", "dissolver", "desintegrar", "sumir", "morte"], """
vec4 effect(vec2 uv) {
    vec4 tex = sampleTex(uv);
    float n = fbm(uv * uScale);
    float th = uProgress * 1.1 - 0.05;
    float alive = smoothstep(th - 0.01, th + 0.01, n);
    float band = alive * (1.0 - smoothstep(th, th + uEdgeWidth, n));
    vec3 col = mix(tex.rgb, uEdgeColor.rgb * 1.6, band);
    return vec4(col, tex.a * alive);
}
""", [_u("uEdgeColor", "color", [1.0, 0.5, 0.1, 1.0], label="Borda"), _u("uEdgeWidth", "float", 0.06, 0.0, 0.3, "Largura da borda"),
      _u("uScale", "float", 6.0, 1.0, 20.0, "Escala do ruído")], blend="alpha", mode="sprite", uses_texture=True, one_shot=True, duration=1.5)

_t("outline_glow", "Contorno brilhante pulsando em volta do sprite (seleção, buff, item raro).", ["outline", "glow", "highlight", "selected", "rare", "aura outline", "contorno", "brilho", "destaque", "selecionado", "raro"], """
vec4 effect(vec2 uv) {
    vec4 tex = sampleTex(uv);
    vec2 px = vec2(uWidth) / uResolution;
    float a = 0.0;
    for (int i = 0; i < 12; i++) {
        float ang = float(i) * 0.5235988;
        a = max(a, sampleTex(uv + vec2(cos(ang), sin(ang)) * px).a);
    }
    float outline = clamp(a - tex.a, 0.0, 1.0);
    float pulse = mix(1.0 - uPulse, 1.0, 0.5 + 0.5 * sin(uTime * 4.0));
    vec3 col = mix(uColor.rgb, tex.rgb, tex.a);
    return vec4(col, max(tex.a, outline * pulse));
}
""", [_u("uColor", "color", [1.0, 0.85, 0.2, 1.0], label="Cor"), _u("uWidth", "float", 4.0, 1.0, 16.0, "Espessura (px)"),
      _u("uPulse", "float", 0.5, 0.0, 1.0, "Pulso")], blend="alpha", mode="sprite", uses_texture=True, duration=1.5708)

_t("hologram", "Holograma com linhas de varredura, tremulação e glitch (usa textura).", ["hologram", "holo", "glitch", "scanline", "projection", "ghost", "holograma", "projeção", "fantasma"], """
vec4 effect(vec2 uv) {
    float t = uTime;
    float g = step(0.9, hash11(floor(t * 12.0))) * (hash11(floor(uv.y * 30.0) + floor(t * 12.0)) - 0.5) * 0.08;
    vec4 tex = sampleTex(vec2(uv.x + g, uv.y));
    float lum = dot(tex.rgb, vec3(0.299, 0.587, 0.114));
    float scan = 0.65 + 0.35 * sin(uv.y * uLines * 6.2831853 - t * 8.0);
    float band = 1.0 - smoothstep(0.0, 0.06, abs(fract(uv.y - t * 0.35) - 0.5));
    float flicker = 0.85 + 0.15 * hash11(floor(t * 20.0));
    vec3 col = uColor.rgb * (0.4 + lum * 1.2) * scan * flicker + uColor.rgb * band * 0.6;
    return vec4(col, tex.a * uOpacity * (0.6 + 0.4 * scan));
}
""", [_u("uColor", "color", [0.3, 0.9, 1.0, 1.0], label="Cor"), _u("uLines", "float", 60.0, 10.0, 200.0, "Linhas"),
      _u("uOpacity", "float", 0.85, 0.0, 1.0, "Opacidade")], blend="alpha", mode="sprite", uses_texture=True)

_t("heal_aura", "Coluna de luz com brilhos subindo (cura, buff, regeneração).", ["heal", "healing", "regen", "buff", "aura", "holy", "blessing", "cura", "curar", "bênção", "sagrado", "regeneração"], """
vec4 effect(vec2 uv) {
    float t = uTime * uSpeed;
    float x = uv.x - 0.5;
    float column = exp(-x * x * 18.0) * smoothstep(0.0, 0.15, uv.y) * smoothstep(1.0, 0.4, uv.y);
    float waves = 0.5 + 0.5 * sin(uv.y * 18.0 - t * 5.0);
    float base = exp(-pow((uv.y - 0.08) * 9.0, 2.0)) * exp(-x * x * 6.0);
    vec2 g = vec2(uv.x * 8.0, uv.y * 6.0 - t * 1.5);
    vec2 id = floor(g);
    vec2 f = fract(g) - 0.5;
    vec2 o = (hash22(id) - 0.5) * 0.6;
    float h = hash21(id + 3.0);
    vec2 d = f - o;
    float sp = (smoothstep(0.1, 0.0, length(d)) + 0.003 / (abs(d.x * d.y) + 0.003) * smoothstep(0.25, 0.0, length(d)) * 0.5)
        * step(0.55, h) * exp(-x * x * 8.0);
    float twinkle = 0.5 + 0.5 * sin(t * 8.0 + h * 40.0);
    float i = column * (0.35 + 0.25 * waves) + base * 0.8 + sp * twinkle * 1.5;
    vec3 col = mix(uColor.rgb, vec3(1.0), clamp(sp * twinkle, 0.0, 1.0) * 0.6);
    return vec4(col, clamp(i * uIntensity, 0.0, 1.0));
}
""", [_u("uColor", "color", [0.4, 1.0, 0.5, 1.0], label="Cor"), _u("uSpeed", "float", 1.0, 0.0, 4.0, "Velocidade"),
      _u("uIntensity", "float", 1.0, 0.0, 3.0, "Intensidade")])

_t("smoke", "Coluna de fumaça/névoa subindo.", ["smoke", "fog", "mist", "steam", "dust cloud", "vapor", "fumaça", "névoa", "neblina", "poeira"], """
vec4 effect(vec2 uv) {
    vec2 p = (uv - vec2(0.5, 0.3)) * vec2(2.0, 1.6);
    float t = uTime * uSpeed;
    float warp = fbm(uv * 2.0 + vec2(t * 0.2, -t * 0.1));
    float n = fbm(vec2(uv.x * 3.0, uv.y * 2.5 - t) + warp);
    float plume = exp(-p.x * p.x * mix(9.0, 3.0, uv.y)) * smoothstep(0.0, 0.2, uv.y) * smoothstep(1.0, 0.6, uv.y);
    float d = clamp(plume * (n * 1.8 - 0.3), 0.0, 1.0) * uDensity;
    vec3 col = mix(uColor.rgb * 0.6, uColor.rgb, n);
    return vec4(col, d);
}
""", [_u("uColor", "color", [0.62, 0.62, 0.66, 1.0], label="Cor"), _u("uSpeed", "float", 0.4, 0.0, 2.0, "Velocidade"),
      _u("uDensity", "float", 1.0, 0.0, 2.0, "Densidade")], blend="alpha", duration=5.0)

_t("water_caustics", "Água com cáusticas animadas (fundo/superfície).", ["water", "caustics", "ocean", "pool", "underwater", "river", "água", "cáusticas", "oceano", "piscina", "rio"], """
// distance to the border between the two nearest cells (F2 - F1): thin bright network
float cellEdge(vec2 p, float t) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float d1 = 8.0;
    float d2 = 8.0;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 g = vec2(float(x), float(y));
            vec2 o = 0.5 + 0.4 * sin(t + 6.2831853 * hash22(i + g));
            float d = length(g + o - f);
            if (d < d1) { d2 = d1; d1 = d; } else if (d < d2) { d2 = d; }
        }
    }
    return d2 - d1;
}

vec4 effect(vec2 uv) {
    vec2 p = uv * uScale;
    float t = uTime * uSpeed;
    vec2 w = vec2(fbm(p * 0.4 + vec2(t * 0.2, 0.0)), fbm(p * 0.4 + vec2(4.0, -t * 0.2))) - 0.5;
    float e1 = cellEdge(p + w * 1.2, t);
    float e2 = cellEdge(p * 1.6 - w + 7.0, t * 1.3);
    float c = smoothstep(0.12, 0.0, e1) + smoothstep(0.1, 0.0, e2) * 0.5;
    vec3 col = mix(uDeep.rgb, uShallow.rgb, 0.5 + w.x) + vec3(c) * uShine;
    return vec4(col, 1.0);
}
""", [_u("uDeep", "color", [0.02, 0.2, 0.35, 1.0], label="Fundo"), _u("uShallow", "color", [0.05, 0.5, 0.65, 1.0], label="Raso"),
      _u("uScale", "float", 5.0, 1.0, 20.0, "Escala"), _u("uSpeed", "float", 1.0, 0.0, 4.0, "Velocidade"),
      _u("uShine", "float", 0.8, 0.0, 2.0, "Brilho")], blend="alpha", mode="fullscreen", duration=4.0)

_t("sparkles", "Estrelinhas cintilando (tesouro, magia, poeira mágica).", ["sparkle", "sparkles", "twinkle", "stars", "glitter", "fairy", "treasure", "brilhos", "estrelas", "cintilar", "purpurina", "fada", "tesouro"], """
vec4 effect(vec2 uv) {
    float t = uTime * uSpeed;
    float i = 0.0;
    for (int k = 0; k < 3; k++) {
        float s = 6.0 + float(k) * 5.0;
        vec2 g = uv * s + float(k) * 13.7;
        vec2 id = floor(g);
        vec2 f = fract(g) - 0.5;
        float h = hash21(id);
        vec2 d = f - (hash22(id + 1.7) - 0.5) * 0.7;
        float tw = max(0.0, sin(t * (2.0 + h * 4.0) + hash21(id + 5.3) * 6.2831853));
        float star = 0.004 / (abs(d.x * d.y) + 0.004) * smoothstep(0.35, 0.0, length(d));
        float core = smoothstep(0.08, 0.0, length(d));
        i += (star * 0.5 + core) * tw * tw * step(1.0 - uDensity, h);
    }
    float edge = smoothstep(0.5, 0.35, length(uv - 0.5));
    return vec4(mix(uColor.rgb, vec3(1.0), clamp(i - 0.5, 0.0, 1.0)), clamp(i * edge, 0.0, 1.0));
}
""", [_u("uColor", "color", [1.0, 0.85, 0.35, 1.0], label="Cor"), _u("uDensity", "float", 0.35, 0.05, 1.0, "Densidade"),
      _u("uSpeed", "float", 1.0, 0.0, 4.0, "Velocidade")], duration=3.1416)

_t("hit_flash", "Flash de dano no sprite com tremida (one-shot, usa textura).", ["hit", "damage", "hurt", "flash", "impact flash", "dano", "acerto", "machucar", "piscar"], """
vec4 effect(vec2 uv) {
    float k = 1.0 - uProgress;
    vec2 shake = (hash22(vec2(floor(uProgress * 20.0), 1.0)) - 0.5) * uShake * k * 0.04;
    vec4 tex = sampleTex(uv + shake);
    return vec4(mix(tex.rgb, uColor.rgb, k * k), tex.a);
}
""", [_u("uColor", "color", [1.0, 1.0, 1.0, 1.0], label="Cor do flash"), _u("uShake", "float", 1.0, 0.0, 4.0, "Tremida")],
   blend="alpha", mode="sprite", uses_texture=True, one_shot=True, duration=0.35)


# ---------------------------------------------------------------- seleção por prompt

COLOR_WORDS: dict[str, list[float]] = {
    "red": [1.0, 0.15, 0.1], "vermelho": [1.0, 0.15, 0.1], "vermelha": [1.0, 0.15, 0.1], "crimson": [0.8, 0.05, 0.15],
    "orange": [1.0, 0.5, 0.1], "laranja": [1.0, 0.5, 0.1], "yellow": [1.0, 0.9, 0.2], "amarelo": [1.0, 0.9, 0.2],
    "amarela": [1.0, 0.9, 0.2], "gold": [1.0, 0.78, 0.25], "golden": [1.0, 0.78, 0.25], "dourado": [1.0, 0.78, 0.25],
    "dourada": [1.0, 0.78, 0.25], "holy": [1.0, 0.9, 0.55], "sagrado": [1.0, 0.9, 0.55], "green": [0.25, 1.0, 0.35],
    "verde": [0.25, 1.0, 0.35], "poison": [0.55, 1.0, 0.15], "veneno": [0.55, 1.0, 0.15], "toxic": [0.55, 1.0, 0.15],
    "tóxico": [0.55, 1.0, 0.15], "cyan": [0.2, 0.95, 1.0], "ciano": [0.2, 0.95, 1.0], "ice": [0.6, 0.9, 1.0],
    "frost": [0.6, 0.9, 1.0], "gelo": [0.6, 0.9, 1.0], "blue": [0.2, 0.45, 1.0], "azul": [0.2, 0.45, 1.0],
    "purple": [0.6, 0.2, 1.0], "roxo": [0.6, 0.2, 1.0], "roxa": [0.6, 0.2, 1.0], "violet": [0.55, 0.3, 1.0],
    "violeta": [0.55, 0.3, 1.0], "arcane": [0.65, 0.35, 1.0], "arcano": [0.65, 0.35, 1.0], "pink": [1.0, 0.4, 0.8],
    "rosa": [1.0, 0.4, 0.8], "magenta": [1.0, 0.2, 0.9], "white": [1.0, 1.0, 1.0], "branco": [1.0, 1.0, 1.0],
    "branca": [1.0, 1.0, 1.0], "black": [0.08, 0.06, 0.1], "preto": [0.08, 0.06, 0.1], "shadow": [0.25, 0.1, 0.35],
    "sombra": [0.25, 0.1, 0.35], "dark": [0.3, 0.1, 0.45], "sombrio": [0.3, 0.1, 0.45], "blood": [0.6, 0.0, 0.05],
    "sangue": [0.6, 0.0, 0.05], "lava": [1.0, 0.35, 0.05], "fire": [1.0, 0.45, 0.1],
}


def _stem(k: str) -> str:
    """"dissolve" casa "dissolving"/"dissolves"."""
    return k[:-1] if k.endswith("e") and len(k) > 4 else k


def pick_template(prompt: str, sprite: bool = False) -> str:
    text = prompt.lower()
    best, score = ("outline_glow" if sprite else "plasma_orb"), 0
    for name, t in TEMPLATES.items():
        if sprite and not t["uses_texture"]:
            continue
        s = sum(len(k) for k in t["keywords"] if re.search(rf"\b{re.escape(_stem(k))}", text))
        if s > score:
            best, score = name, s
    return best


def prompt_colors(prompt: str) -> list[list[float]]:
    text = prompt.lower()
    found = []
    for m in re.finditer(r"[a-zà-ú]+", text):
        c = COLOR_WORDS.get(m.group(0))
        if c and c not in found:
            found.append(c)
    return found


def from_template(name: str, prompt: str = "", colors: list[list[float]] | None = None) -> ShaderDef:
    if name not in TEMPLATES:
        raise ValueError(f"template desconhecido: {name} (tem: {', '.join(TEMPLATES)})")
    t = dict(TEMPLATES[name])
    t.pop("keywords")
    sd = ShaderDef(**t)
    cols = colors if colors is not None else prompt_colors(prompt)
    if cols:
        slots = [u for u in sd.uniforms if u.type == "color" and "Smoke" not in u.name]
        values = {}
        for u, c in zip(slots, cols):
            values[u.name] = list(c[:3]) + [1.0]
        if len(cols) == 1 and len(slots) > 1:  # uma cor só: deriva a 2ª mais clara
            c = cols[0]
            values[slots[1].name] = [min(1.0, x * 0.5 + 0.5) for x in c[:3]] + [1.0]
        sd = sd.with_values(values)
    return sd

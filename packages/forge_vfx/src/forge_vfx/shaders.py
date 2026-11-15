"""Shaders portáveis: modelo do asset, montagem do GLSL e tradução para engines.

Convenção do código de efeito (o que o usuário, o template ou o LLM escreve):

    vec4 effect(vec2 uv) { ... return vec4(rgb, alpha); }   // alpha "reto" (não pré-multiplicado)

Disponível: uTime (s), uProgress (0..1, efeitos one-shot), uResolution (px), sampleTex(uv) (sprite de entrada),
os uniforms declarados no asset e as funções do prelude (hash21, vnoise, fbm, voronoi, rot2, polar, hsv2rgb,
sdCircle, sdBox, sdRing, glow, remap, emissive). uv vai de 0 a 1 com origem embaixo à esquerda.
Regras de portabilidade: sem texture() direto (use sampleTex), sem matrizes (use rot2), sem arrays, literais
float com ponto (2.0, não 2), loops com limite constante.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GLSL_DIR = Path(__file__).parent / "glsl"


@cache
def prelude() -> str:
    return (GLSL_DIR / "prelude.glsl").read_text(encoding="utf-8")


UniformType = Literal["float", "color", "vec2", "bool"]


class Uniform(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., pattern=r"^u[A-Z][A-Za-z0-9]*$", description="uCamelCase")
    type: UniformType = "float"
    default: list[float] = Field(default_factory=lambda: [1.0])
    min: float | None = None
    max: float | None = None
    label: str = ""

    @field_validator("default", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [float(v)] if isinstance(v, (int, float, bool)) else v

    def size(self) -> int:
        return {"float": 1, "bool": 1, "vec2": 2, "color": 4}[self.type]

    def values(self) -> list[float]:
        d = list(self.default) + [1.0] * 4
        if self.type == "color" and len(self.default) == 3:
            d = list(self.default) + [1.0]
        return [float(x) for x in d[: self.size()]]

    def glsl_type(self) -> str:
        return {"float": "float", "bool": "float", "vec2": "vec2", "color": "vec4"}[self.type]


Blend = Literal["alpha", "add"]
Mode = Literal["sprite", "overlay", "fullscreen"]

RESERVED = {"uTime", "uProgress", "uResolution", "uDuration", "uTexture"}


class ShaderDef(BaseModel):
    """Conteúdo de shader.json (o arquivo primário do asset)."""

    model_config = ConfigDict(extra="ignore")
    format: str = "asset-forge-shader/1"
    name: str = "effect"
    description: str = ""
    blend: Blend = "alpha"
    mode: Mode = Field("overlay", description="sprite: aplicado sobre um sprite; overlay: quad com o efeito; fullscreen: tela")
    uses_texture: bool = False
    one_shot: bool = Field(False, description="usa uProgress (efeito que começa e termina, ex.: explosão)")
    duration: float = Field(2.0, gt=0, le=60, description="período do loop / duração do one-shot (s)")
    uniforms: list[Uniform] = Field(default_factory=list)
    code: str
    template: str | None = None

    @field_validator("uniforms")
    @classmethod
    def _names(cls, v: list[Uniform]) -> list[Uniform]:
        seen = set()
        for u in v:
            if u.name in RESERVED:
                raise ValueError(f"uniform reservado: {u.name}")
            if u.name in seen:
                raise ValueError(f"uniform duplicado: {u.name}")
            seen.add(u.name)
        return v

    def with_values(self, values: dict[str, Any] | None) -> "ShaderDef":
        if not values:
            return self
        us = []
        names = {u.name for u in self.uniforms}
        for k in values:
            if k not in names:
                raise ValueError(f"uniform inexistente: {k} (tem: {sorted(names)})")
        for u in self.uniforms:
            if u.name in values:
                u = u.model_copy(update={"default": Uniform._listify(values[u.name])})
            us.append(u)
        return self.model_copy(update={"uniforms": us})


def _fmt(x: float) -> str:
    s = f"{float(x):.6g}"
    return s if any(c in s for c in ".e") else s + ".0"


def _vec(vals: list[float], prefix: str = "vec") -> str:
    if len(vals) == 1:
        return _fmt(vals[0])
    return f"{prefix}{len(vals)}({', '.join(_fmt(v) for v in vals)})"


# ---------------------------------------------------------------- GLSL (prévia, validação, WebGL2)

def glsl_es(sd: ShaderDef) -> str:
    """Fragment shader GLSL ES 3.00 completo (WebGL2 e validação estrita no servidor)."""
    us = "\n".join(f"uniform {u.glsl_type()} {u.name};" for u in sd.uniforms)
    return f"""#version 300 es
precision highp float;
uniform float uTime;
uniform float uProgress;
uniform vec2 uResolution;
uniform sampler2D uTexture;
uniform float uHasTexture;
{us}
in vec2 vUv;
out vec4 fragColor;
vec4 sampleTex(vec2 uv) {{
    if (uHasTexture < 0.5 || uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return vec4(0.0);
    return texture(uTexture, uv);
}}
{prelude()}
#line 1
{sd.code}
void main() {{
    fragColor = effect(vUv);
}}
"""


VERTEX_ES = """#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() {
    vUv = aPos * 0.5 + 0.5;
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""


def error_line_offset(sd: ShaderDef) -> int:
    """Linhas antes do código do usuário (para quando o driver ignora #line)."""
    src = glsl_es(sd)
    return src[: src.index("#line 1")].count("\n") + 1


# ---------------------------------------------------------------- Shadertoy

def shadertoy(sd: ShaderDef) -> str:
    consts = "\n".join(f"#define {u.name} {_vec(u.values())}" for u in sd.uniforms)
    return f"""// {sd.name} — exported from Asset Forge (paste into shadertoy.com; iChannel0 = sprite texture)
#define uTime iTime
#define uResolution iResolution.xy
#define uProgress fract(iTime / {_fmt(sd.duration)})
{consts}
vec4 sampleTex(vec2 uv) {{ return texture(iChannel0, uv); }}
{prelude()}
{sd.code}
void mainImage(out vec4 fragColor, in vec2 fragCoord) {{
    vec4 c = effect(fragCoord / iResolution.xy);
    fragColor = vec4(mix(vec3(0.05, 0.05, 0.08), c.rgb, c.a), 1.0);
}}
"""


# ---------------------------------------------------------------- Godot 4 (canvas_item)

def godot(sd: ShaderDef) -> str:
    lines = [f"// {sd.name} — exported from Asset Forge", "shader_type canvas_item;",
             "render_mode blend_add;" if sd.blend == "add" else "render_mode blend_mix;", ""]
    for u in sd.uniforms:
        v = u.values()
        if u.type == "color":
            lines.append(f"uniform vec4 {u.name} : source_color = {_vec(v)};")
        elif u.type == "float" and u.min is not None and u.max is not None:
            lines.append(f"uniform float {u.name} : hint_range({_fmt(u.min)}, {_fmt(u.max)}) = {_fmt(v[0])};")
        else:
            lines.append(f"uniform {u.glsl_type()} {u.name} = {_vec(v)};")
    lines += [
        f"uniform float duration = {_fmt(sd.duration)};",
        "// one-shot: set auto_progress = false and animate `progress` from code/AnimationPlayer",
        "uniform bool auto_progress = true;",
        "uniform float progress : hint_range(0.0, 1.0) = 0.0;",
        "uniform vec2 uResolution = vec2(256.0, 256.0);",
        "",
        "#define uTime TIME",
        "#define uProgress (auto_progress ? fract(TIME / duration) : progress)",
        "",
    ]
    tex = "texture(TEXTURE, vec2(uv.x, 1.0 - uv.y))" if sd.uses_texture else "vec4(0.0)"
    body = [
        f"vec4 sampleTex(vec2 uv) {{ return {tex}; }}",
        prelude(),
        sd.code,
        "",
        "void fragment() {",
        "    COLOR = effect(vec2(UV.x, 1.0 - UV.y));",
        "}",
    ]
    return "\n".join(lines + body) + "\n"


# ---------------------------------------------------------------- GLSL → HLSL

_TYPE_MAP = {
    "vec2": "float2", "vec3": "float3", "vec4": "float4",
    "ivec2": "int2", "ivec3": "int3", "ivec4": "int4",
    "bvec2": "bool2", "bvec3": "bool3", "bvec4": "bool4",
    "mat2": "float2x2", "mat3": "float3x3", "mat4": "float4x4",
}
_FUNC_MAP = {"mix": "lerp", "fract": "frac", "inversesqrt": "rsqrt", "dFdx": "ddx", "dFdy": "ddy"}


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _rewrite_calls(code: str, name: str, fn) -> str:
    """Reescreve chamadas `name(args)` (respeitando parênteses aninhados), de dentro para fora."""
    pat = re.compile(rf"\b{re.escape(name)}\s*\(")
    pos = 0
    while True:
        m = pat.search(code, pos)
        if not m:
            return code
        start, i, depth = m.start(), m.end(), 1
        while i < len(code) and depth:
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                depth -= 1
            i += 1
        inner = code[m.end():i - 1]
        inner = _rewrite_calls(inner, name, fn)
        repl = fn(_split_args(inner))
        if repl is None:
            repl = f"{name}({inner})"
        code = code[:start] + repl + code[i:]
        pos = start + len(repl)


def to_hlsl(glsl: str) -> str:
    code = glsl
    # construtores de 1 argumento (vec3(1.0)) não existem em HLSL: vira cast
    for n in (2, 3, 4):
        code = _rewrite_calls(code, f"vec{n}", lambda a, n=n: f"((float{n})({a[0]}))" if len(a) == 1 else None)
    code = _rewrite_calls(code, "atan", lambda a: f"atan2({a[0]}, {a[1]})" if len(a) == 2 else None)
    code = _rewrite_calls(code, "mod", lambda a: f"glsl_mod({a[0]}, {a[1]})" if len(a) == 2 else None)
    for g, h in {**_TYPE_MAP, **_FUNC_MAP}.items():
        code = re.sub(rf"\b{g}\b", h, code)
    # const global → static const (const global em HLSL vira uniform)
    code = re.sub(r"^const\s", "static const ", code, flags=re.M)
    return code


HLSL_HELPERS = """#define glsl_mod(x, y) ((x) - (y) * floor((x) / (y)))
"""


def _hlsl_body(sd: ShaderDef, sample: str) -> str:
    return "\n".join([HLSL_HELPERS, f"float4 sampleTex(float2 uv) {{ return {sample}; }}",
                      to_hlsl(prelude()), to_hlsl(sd.code)])


def unity(sd: ShaderDef) -> str:
    """ShaderLab (Built-in RP; também funciona em URP para sprites/quads unlit)."""
    props, decls = [], []
    for u in sd.uniforms:
        v = u.values()
        label = (u.label or u.name).replace('"', "'")
        if u.type == "color":
            props.append(f'        {u.name} ("{label}", Color) = ({", ".join(_fmt(x) for x in v)})')
            decls.append(f"            float4 {u.name};")
        elif u.type == "vec2":
            props.append(f'        {u.name} ("{label}", Vector) = ({_fmt(v[0])}, {_fmt(v[1])}, 0, 0)')
            decls.append(f"            float4 {u.name};  // .xy")
        elif u.type == "float" and u.min is not None and u.max is not None:
            props.append(f'        {u.name} ("{label}", Range({_fmt(u.min)}, {_fmt(u.max)})) = {_fmt(v[0])}')
            decls.append(f"            float {u.name};")
        else:
            props.append(f'        {u.name} ("{label}", Float) = {_fmt(v[0])}')
            decls.append(f"            float {u.name};")
    blend = "Blend SrcAlpha One" if sd.blend == "add" else "Blend SrcAlpha OneMinusSrcAlpha"
    body = _hlsl_body(sd, "tex2D(_MainTex, uv)" if sd.uses_texture else "float4(0, 0, 0, 0)")
    body = "\n".join("            " + ln if ln.strip() else "" for ln in body.splitlines())
    name = re.sub(r"[^A-Za-z0-9 _-]", "", sd.name) or "Effect"
    return f"""// {sd.name} — exported from Asset Forge
Shader "AssetForge/{name}"
{{
    Properties
    {{
        [PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {{}}
{chr(10).join(props)}
        _Duration ("Loop / one-shot duration (s)", Float) = {_fmt(sd.duration)}
        [Toggle] _AutoProgress ("Auto progress", Float) = 1
        _Progress ("Progress (when auto is off)", Range(0, 1)) = 0
        _Resolution ("Resolution (px)", Vector) = (256, 256, 0, 0)
    }}
    SubShader
    {{
        Tags {{ "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "PreviewType"="Plane" "CanUseSpriteAtlas"="True" }}
        Cull Off
        Lighting Off
        ZWrite Off
        {blend}
        Pass
        {{
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            float _Duration;
            float _AutoProgress;
            float _Progress;
            float4 _Resolution;
{chr(10).join(decls)}
            #define uTime _Time.y
            #define uProgress (_AutoProgress > 0.5 ? frac(_Time.y / _Duration) : _Progress)
            #define uResolution (_Resolution.xy)
{body}
            struct appdata {{ float4 vertex : POSITION; float2 uv : TEXCOORD0; float4 color : COLOR; }};
            struct v2f {{ float4 pos : SV_POSITION; float2 uv : TEXCOORD0; float4 color : COLOR; }};
            v2f vert(appdata v) {{ v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.uv = v.uv; o.color = v.color; return o; }}
            float4 frag(v2f i) : SV_Target {{ return effect(i.uv) * i.color; }}
            ENDCG
        }}
    }}
}}
"""


def xna_fx(sd: ShaderDef) -> str:
    """Effect .fx (XNA/FNA, tModLoader): compile com fxc / EasyXnb. Parâmetros com os nomes do asset."""
    decls = [f"{to_hlsl(u.glsl_type())} {u.name} = {to_hlsl(_vec(u.values()))};" for u in sd.uniforms]
    body = _hlsl_body(sd, "tex2D(uImage0, uv)")
    return f"""// {sd.name} — exported from Asset Forge for XNA/FNA (tModLoader).
// tModLoader: compile to .xnb (ps_3_0), load with ModContent.Request<Effect>(), set uTime each frame
// (Main.GlobalTimeWrappedHourly) and uProgress for one-shot effects. Texture uv: (0,0) = bottom-left here.
sampler uImage0 : register(s0);
float uTime;
float uProgress;
float2 uResolution = float2(256, 256);
{chr(10).join(decls)}
{body}
float4 PixelMain(float2 coords : TEXCOORD0, float4 color : COLOR0) : COLOR0
{{
    return effect(float2(coords.x, 1.0 - coords.y)) * color;
}}
technique Technique1
{{
    pass {re.sub(r'[^A-Za-z0-9]', '', sd.name.title()) or 'Effect'}Pass
    {{
        PixelShader = compile ps_3_0 PixelMain();
    }}
}}
"""


def hlsl_raw(sd: ShaderDef) -> str:
    """Só as funções em HLSL (para colar em s&box, Unreal custom node, Stride...)."""
    us = "\n".join(f"{to_hlsl(u.glsl_type())} {u.name}; // default {_vec(u.values())}" for u in sd.uniforms)
    return f"""// {sd.name} — exported from Asset Forge. Provide uTime, uProgress, uResolution and implement sampleTex.
float uTime;
float uProgress;
float2 uResolution;
{us}
{HLSL_HELPERS}
{to_hlsl(prelude())}
{to_hlsl(sd.code)}
"""

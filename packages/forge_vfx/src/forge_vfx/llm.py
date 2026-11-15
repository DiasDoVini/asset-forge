"""Geração de shaders/partículas com o Claude (opcional).

Usa as mesmas credenciais e modelo do crítico (forge_core.critic). Sem credenciais, os pipelines usam a
biblioteca de templates/presets — e agentes (Claude Code via MCP) podem mandar o código pronto em `code`
/ `definition`.
"""
from __future__ import annotations

import json
import os
from typing import Any

from forge_core.critic import MODEL, api_available

from .shaders import __doc__ as SHADER_CONVENTION
from .shaders import prelude

EFFORT = os.environ.get("FORGE_VFX_EFFORT", "medium")


class LLMError(RuntimeError):
    pass


SHADER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "blend": {"type": "string", "enum": ["alpha", "add"]},
        "mode": {"type": "string", "enum": ["sprite", "overlay", "fullscreen"]},
        "uses_texture": {"type": "boolean"},
        "one_shot": {"type": "boolean"},
        "duration": {"type": "number"},
        "uniforms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "uCamelCase"},
                    "type": {"type": "string", "enum": ["float", "color", "vec2", "bool"]},
                    "default": {"type": "array", "items": {"type": "number"}},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "label": {"type": "string"},
                },
                "required": ["name", "type", "default", "min", "max", "label"],
                "additionalProperties": False,
            },
        },
        "code": {"type": "string", "description": "GLSL: helper functions + vec4 effect(vec2 uv)"},
    },
    "required": ["name", "description", "blend", "mode", "uses_texture", "one_shot", "duration", "uniforms", "code"],
    "additionalProperties": False,
}


def _client():
    ok, why = api_available()
    if not ok:
        raise LLMError(why)
    import anthropic

    try:
        return anthropic.Anthropic(max_retries=3, timeout=600)
    except Exception as e:
        raise LLMError(f"Claude API indisponível: {e}") from e


def _ask(system: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None) -> str:
    import anthropic

    client = _client()
    kwargs: dict[str, Any] = dict(model=MODEL, max_tokens=16000, system=system, messages=messages,
                                  thinking={"type": "adaptive"}, output_config={"effort": EFFORT})
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.APIError as e:
        raise LLMError(f"Claude API: {e}") from e
    if resp.stop_reason == "refusal":
        raise LLMError("o modelo recusou o pedido")
    return next((b.text for b in resp.content if b.type == "text"), "")


SHADER_SYSTEM = f"""You are a senior technical artist who writes real-time VFX shaders for 2D games.
Write ONE effect following this convention exactly:
{SHADER_CONVENTION}
The prelude below is already included before your code — call its functions, do not redefine them:
```glsl
{prelude()}
```
Rules: GLSL ES 3.00 strict typing (float literals like 1.0; no implicit int→float), no `texture()` (use sampleTex),
no matrices, no arrays, constant loop bounds, no #version/#define/uniform declarations (uniforms come from the
`uniforms` list; names must be uCamelCase and must not be uTime/uProgress/uResolution). Return straight (not
premultiplied) alpha. Prefer `blend: add` for glowing energy/fire/light, `alpha` for smoke/solid/sprite effects.
Use one_shot=true and uProgress (0→1) for effects that start and end (explosions, hits, dissolves); otherwise loop
with uTime and make it look good when looping every `duration` seconds. Expose 2–6 meaningful uniforms (colors,
speed, intensity, size) with sensible ranges. Keep it efficient (fbm octaves are already capped at 5)."""


def generate_shader(prompt: str, *, base: dict[str, Any] | None = None, error: str | None = None,
                    sprite: bool = False) -> dict[str, Any]:
    parts = [f"Effect request: {prompt}"]
    if sprite:
        parts.append("The effect is applied ON a sprite: set uses_texture=true, mode=sprite and read it with sampleTex(uv).")
    if base:
        parts.append("Modify this existing effect (keep what is not asked to change):\n" + json.dumps(base, ensure_ascii=False))
    if error:
        parts.append(f"The previous attempt failed to compile. Fix it. Compiler output:\n{error}")
    text = _ask(SHADER_SYSTEM, [{"role": "user", "content": "\n\n".join(parts)}], SHADER_SCHEMA)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"resposta não é JSON: {e}") from e


PARTICLES_SYSTEM = """You are a senior technical artist designing 2D particle systems for games.
Return ONLY a JSON object (no markdown) matching this pydantic schema (units: pixels, origin at the canvas
center, y up, angles in degrees CCW, 90 = up; canvas is 256x256 by default so keep effects within ±120 px):
{schema}
Guidelines: combine 1–4 emitters (e.g. core + sparks + smoke). Use blend "add" for light/fire/magic and "alpha"
for smoke/dust/solid debris. Textures: soft, circle, spark (use with stretch for streaks), star, smoke, ring,
square, flare. Bursts for one-shots (loop=false), rate for continuous loops. Colors are gradients over life
['#rrggbbaa'] — fade alpha to 00 at the end."""


def generate_particles(prompt: str, schema: dict[str, Any], *, base: dict[str, Any] | None = None,
                       error: str | None = None) -> dict[str, Any]:
    parts = [f"Effect request: {prompt}"]
    if base:
        parts.append("Modify this existing system (keep what is not asked to change):\n" + json.dumps(base, ensure_ascii=False))
    if error:
        parts.append(f"The previous JSON was invalid. Fix it:\n{error}")
    text = _ask(PARTICLES_SYSTEM.replace("{schema}", json.dumps(schema)), [{"role": "user", "content": "\n\n".join(parts)}], None)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"resposta não é JSON: {e}") from e

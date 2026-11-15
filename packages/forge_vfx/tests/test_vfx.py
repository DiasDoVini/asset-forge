import json
import re

import numpy as np
import pytest
from PIL import Image

from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_vfx import library, modality, particle_presets, particles, render, shaders
from forge_vfx.shaders import ShaderDef

gl_ok, _ = render.available()
needs_gl = pytest.mark.skipif(not gl_ok, reason="sem OpenGL")


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield r
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def test_hlsl_translation():
    src = "vec3 a = vec3(1.0); float b = atan(a.y, a.x); vec2 c = mod(vec2(3.0, 1.0), 2.0); float d = mix(fract(b), 1.0, 0.5);"
    h = shaders.to_hlsl(src)
    assert "((float3)(1.0))" in h and "atan2(a.y, a.x)" in h and "glsl_mod(float2(3.0, 1.0), 2.0)" in h
    assert "lerp(frac(b)" in h
    assert not re.search(r"\b(vec[234]|mix|fract)\b", h)
    full = shaders.to_hlsl(shaders.prelude())
    assert not re.search(r"\b(vec[234]|mix|fract|mat2)\b", full)


def test_template_selection_and_colors():
    assert library.pick_template("a swirling purple portal") == "portal"
    assert library.pick_template("escudo de energia azul") == "energy_shield"
    assert library.pick_template("character dissolving", sprite=True) == "dissolve"
    sd = library.from_template("fire", "green fire")
    inner = next(u for u in sd.uniforms if u.name == "uColorInner")
    assert inner.values()[:3] == [0.25, 1.0, 0.35]


@needs_gl
def test_all_templates_compile_strict():
    for name in library.TEMPLATES:
        render.compile_check(library.from_template(name))


@needs_gl
def test_compile_error_points_to_user_line():
    sd = ShaderDef(code="vec4 effect(vec2 uv) {\n    float x = 1;\n    return vec4(uv, x, 1.0) * 2;\n}\n")
    with pytest.raises(render.ShaderCompileError) as e:
        render.compile_check(sd)
    assert "3" in str(e.value)


@needs_gl
def test_shader_pipeline_and_exports(project, tmp_path):
    av = run_pipeline(project, "vfx.shader", {"prompt": "red magic portal", "generator": "template", "frames": 8})
    assert av.kind == "shader" and av.extra["shader"]["template"] == "portal"
    sd = ShaderDef.model_validate_json((project.store.version_dir(av.slug, av.version) / "shader.json").read_text())
    assert sd.uniforms[0].values()[:3] == [1.0, 0.15, 0.1]
    out = tmp_path / "out"
    out.mkdir()
    files = run_export(project, av.slug, "vfx.shader_code", {"target": "all"}, out)
    names = {f.name for f in files}
    assert {f"{av.slug}.gdshader", f"{av.slug}.shader", f"{av.slug}.fx", f"{av.slug}.frag"} <= names
    gd = (out / f"{av.slug}.gdshader").read_text()
    assert "shader_type canvas_item" in gd and "uniform vec4 uColorA : source_color" in gd
    fb = run_export(project, av.slug, "vfx.flipbook", {"frames": 8, "size": 64}, tmp_path / "fb")
    sheet = Image.open(next(f for f in fb if f.name.endswith("_sheet.png")))
    assert sheet.size == (64 * 3, 64 * 3)


@needs_gl
def test_shader_on_sprite_and_edit(project):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(16, 48):
            img.putpixel((x, y), (200, 50, 50, 255))
    sprite = project.store.import_file(_save(img, project), slug="hero")
    av = run_pipeline(project, "vfx.shader", {"input_asset": sprite.slug, "prompt": "dissolve", "frames": 8})
    assert av.extra["shader"]["template"] == "dissolve" and any(f.role == "texture" for f in av.files)
    ed = run_pipeline(project, "vfx.shader_edit", {"input_asset": av.slug, "uniforms": {"uEdgeWidth": 0.2}, "frames": 4})
    assert ed.version == 2 and any(f.role == "texture" for f in ed.files)
    with pytest.raises(Exception):
        run_pipeline(project, "vfx.shader_edit", {"input_asset": av.slug, "code": "vec4 effect(vec2 uv) { return 1; }"})


def _save(img, project):
    p = project.root / "sprite.png"
    img.save(p)
    return p


def test_particles_presets_render():
    for name in particle_presets.PRESETS:
        ps = particle_presets.from_preset(name)
        frames = particles.render(ps, 4, size=(64, 64))
        assert any(np.asarray(f)[..., 3].max() > 0 for f in frames), name


def test_particles_pipeline_and_engines(project, tmp_path):
    av = run_pipeline(project, "vfx.particles", {"prompt": "campfire", "frames": 6})
    assert av.kind == "particles" and av.extra["particles"]["preset"] == "fire"
    files = run_export(project, av.slug, "vfx.particles_engine", {"target": "godot"}, tmp_path / "g")
    tscn = next(f for f in files if f.suffix == ".tscn").read_text()
    assert tscn.startswith("[gd_scene") and tscn.count('type="CPUParticles2D"') == 3
    files = run_export(project, av.slug, "vfx.particles_engine", {"target": "unity"}, tmp_path / "u")
    data = json.loads(next(f for f in files if f.name.endswith(".particles.json")).read_text())
    assert data["emitters"][0]["color_c"][0].startswith("#")
    ed = run_pipeline(project, "vfx.particles_edit", {"input_asset": av.slug, "scale": 2, "colors": ["#40a0ff"]})
    ps = particles.ParticleSystem.model_validate_json((project.store.version_dir(av.slug, ed.version) / "particles.json").read_text())
    assert ps.emitters[0].size[0] == 52


def test_particles_definition_from_agent(project):
    d = {"name": "custom", "duration": 1, "loop": False,
         "emitters": [{"texture": "star", "rate": 0, "bursts": [{"time": 0, "count": 5}], "spread": 180}]}
    av = run_pipeline(project, "vfx.particles", {"definition": d, "frames": 4})
    assert av.extra["generator"] == "definition"

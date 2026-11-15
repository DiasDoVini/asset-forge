import json

import pytest

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_vfx import modality, particle_presets, render

gl_ok, _ = render.available()
needs_gl = pytest.mark.skipif(not gl_ok, reason="sem OpenGL")


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def test_import_particles_json(project, tmp_path):
    ps = particle_presets.from_preset("sparks")
    p = tmp_path / "sparks.json"
    p.write_text(ps.model_dump_json(), encoding="utf-8")
    av = project.store.import_file(p)
    assert (av.modality, av.kind) == ("vfx", "particles")
    assert av.primary().path == "particles.json"


def test_particles_json_saved_as_cp1252(project, tmp_path):
    ps = particle_presets.from_preset("sparks").model_copy(update={"name": "faíscas"})
    p = tmp_path / "f.json"
    p.write_bytes(ps.model_dump_json().replace("\u00ed", "í").encode("cp1252"))
    assert project.store.import_file(p).kind == "particles"


def test_unrelated_json_stays_raw(project, tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"hello": "world"}))
    av = project.store.import_file(p)
    assert av.pipeline == "core.import" and "import" not in av.extra


@needs_gl
def test_import_glsl_effect_with_uniforms(project, tmp_path):
    p = tmp_path / "pulse.glsl"
    p.write_text("uniform float uSpeed;\nvec4 effect(vec2 uv) {\n  float d = length(uv - 0.5);\n"
                 "  float a = smoothstep(0.5, 0.0, d) * (0.5 + 0.5 * sin(uTime * uSpeed));\n  return vec4(1.0, 0.4, 0.1, a);\n}\n")
    av = project.store.import_file(p)
    assert (av.kind, av.primary().path) == ("shader", "shader.json")
    assert av.extra["shader"]["uniforms"] == ["uSpeed"]


@needs_gl
def test_import_broken_glsl_is_rejected(project, tmp_path):
    p = tmp_path / "bad.glsl"
    p.write_text("vec4 effect(vec2 uv) { return vec4(nope); }\n")
    with pytest.raises(ValueError, match="compil"):
        project.store.import_file(p)
    assert not (project.store.root / "bad").exists()

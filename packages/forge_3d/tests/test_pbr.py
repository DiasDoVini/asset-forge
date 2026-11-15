import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry
from PIL import Image

import forge_3d
from forge_3d import geometry as g
from forge_3d.geometry import pbr
from forge_3d.geometry import texture as tx


def _bumpy(subdiv=5, amp=0.06):
    s = trimesh.creation.icosphere(subdivisions=subdiv)
    v = s.vertices
    bump = amp * np.sin(12 * v[:, 0]) * np.sin(12 * v[:, 1])
    return trimesh.Trimesh(v * (1 + bump)[:, None], s.faces)


def _uv_sphere():
    un = tx.unwrap(trimesh.creation.icosphere(subdivisions=2), 256)
    return un.mesh, un.size


def test_normal_map_is_flat_for_the_same_surface_and_detailed_for_a_bumpy_one():
    low, size = _uv_sphere()
    flat = pbr.bake_normal_map(low, trimesh.creation.icosphere(subdivisions=5), size)
    covered = tx.rasterize_uv(low, size).mask
    a = np.asarray(flat).astype(float)[covered]
    assert a[:, 2].mean() > 235 and np.abs(a[:, :2] - 128).mean() < 12   # mostly (128, 128, 255)
    bumpy = np.asarray(pbr.bake_normal_map(low, _bumpy(), size)).astype(float)[covered]
    assert np.abs(bumpy[:, :2] - 128).mean() > np.abs(a[:, :2] - 128).mean() + 8


def test_align_highres_recovers_scale_and_place():
    low = trimesh.creation.capsule(height=1.0, radius=0.3)
    high = trimesh.creation.capsule(height=1.0, radius=0.3, count=[64, 64])
    high.apply_scale(3.0)
    high.apply_translation((5, -2, 1))
    aligned, cost = pbr.align_highres(high, low)
    assert aligned is not None and cost < 1e-3
    assert np.allclose(aligned.bounds, low.bounds, atol=0.02)


def test_pbr_maps_roundtrip_and_painting_keeps_them(tmp_path):
    low, size = _uv_sphere()
    base = Image.new("RGBA", size, (200, 50, 50, 255))
    maps = pbr.PBRMaps(base, pbr.uniform(size, 0.9), pbr.uniform(size, 0.3), Image.new("RGB", size, (128, 128, 255)))
    g.save_mesh(pbr.with_maps(low, maps), tmp_path / "m.glb")
    back = pbr.maps_of(g.load_mesh(tmp_path / "m.glb"))
    assert back.normal is not None
    assert np.asarray(back.metallic).mean() == pytest.approx(0.9 * 255, abs=2)
    assert np.asarray(back.roughness).mean() == pytest.approx(0.3 * 255, abs=2)
    repainted = tx.with_texture(g.load_mesh(tmp_path / "m.glb"), Image.new("RGBA", size, (0, 0, 200, 255)))
    kept = pbr.maps_of(repainted)
    assert kept.normal is not None and np.asarray(kept.metallic).mean() == pytest.approx(0.9 * 255, abs=2)


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="PBR")
    yield proj
    set_registry(None)


def test_from_raw_rebuilds_from_the_dense_surface(project, tmp_path):
    model = trimesh.creation.icosphere(subdivisions=2)            # the "bad" simplified model
    model.apply_translation((0, 1, 0))
    g.save_mesh(model, tmp_path / "model.glb")
    raw = _bumpy(subdiv=5)                                        # the raw: detailed, other scale/place
    raw.apply_scale(0.5)
    raw.apply_translation((3, 3, 3))
    g.save_mesh(raw, tmp_path / "raw.glb")
    av = project.store.import_file(tmp_path / "model.glb", slug="thing", modality="3d", kind="mesh")
    vdir = project.store.version_dir(av.slug, av.version)
    (vdir / "raw.glb").write_bytes((tmp_path / "raw.glb").read_bytes())
    av.files.append(type(av.files[0])(path="raw.glb", role="source", mime="model/gltf-binary"))
    project.store.write_meta(av)
    out = run_pipeline(project, "3d.from_raw", {"input_asset": "thing", "cleanup": "none", "target_faces": 3000})
    mesh = g.load_mesh(project.store.files_of(out, "primary")[0])
    assert 2500 <= len(mesh.faces) <= 3000
    assert np.allclose(mesh.bounds.mean(axis=0), model.bounds.mean(axis=0), atol=0.05)   # placed where the model was
    assert out.extra["from_raw"]["source"].endswith("raw.glb")

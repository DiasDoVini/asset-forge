import pytest
import trimesh
from forge_core import Project
from forge_core.pipeline import Registry, set_registry
from forge_core.refine import Brief, PreviewContext, RefineSession, session_dir

import forge_3d
from forge_3d import geometry as g


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="R")
    yield proj
    set_registry(None)


def test_mesh_refiner_catalog_and_previews(project, tmp_path):
    refiner = next(r for r in forge_3d.modality.refiners if r.id == "3d.model")
    info = refiner.describe()
    assert all(a["available"] for a in info["actions"]), [a["pipeline"] for a in info["actions"] if not a["available"]]
    assert refiner.initial_action(Brief(prompt="a chest", references=["img@v001"])).pipeline == "3d.image_to_3d"
    assert refiner.initial_action(Brief(prompt="a chest")).params["prompt"] == "a chest"

    g.save_mesh(trimesh.creation.capsule(height=1.0, radius=0.3), tmp_path / "m.glb")
    av = project.store.import_file(tmp_path / "m.glb", slug="thing", modality="3d", kind="mesh")
    session = RefineSession(id="s1", project=project.slug, refiner="3d.model", brief=Brief(prompt="x"), asset="thing")
    out = session_dir(project, "s1") / "iter_01"
    out.mkdir(parents=True)
    previews = refiner.previews(PreviewContext(project=project, session=session, asset=av, out_dir=out))
    labels = [p.label for p in previews]
    assert len(previews) == 4 and labels[0].startswith("vistas") and "wireframe" in labels[3]
    for p in previews:
        assert (session_dir(project, "s1") / p.path).stat().st_size > 1000

import pytest
import trimesh

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_3d import modality


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


@pytest.mark.parametrize("ext", [".obj", ".stl", ".ply", ".glb"])
def test_import_mesh_formats_become_glb(project, tmp_path, ext):
    p = tmp_path / f"box{ext}"
    trimesh.creation.box((1, 2, 3)).export(p)
    av = project.store.import_file(p)
    assert (av.modality, av.kind) == ("3d", "mesh")
    assert av.primary().path == "model.glb"
    assert av.extra["mesh"]["faces"] == 12 and av.extra["mesh"]["watertight"]
    assert (av.extra["original_format"], any(f.role == "source" for f in av.files)) == (ext[1:].upper(), ext != ".glb")
    again = trimesh.load(project.store.files_of(av, "primary")[0], force="mesh")
    assert sorted(round(x, 3) for x in again.extents) == [1.0, 2.0, 3.0]


def test_import_empty_mesh_rejected(project, tmp_path):
    p = tmp_path / "empty.obj"
    p.write_text("# nada\n")
    with pytest.raises(ValueError):
        project.store.import_file(p)
    assert not (project.store.root / "empty").exists()

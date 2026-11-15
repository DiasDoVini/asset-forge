import numpy as np
import json

import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.pipeline import Registry, set_registry

import forge_3d
from forge_3d import geometry as g

from .test_geometry import textured_sphere


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="Test")
    proj.set_value("style.palette", ["#d03030", "#3030d0", "#30d030"])
    proj.set_value("3d.printer", {"name": "tiny", "build_volume_mm": [120, 120, 120]})
    src = tmp_path / "sphere.glb"
    g.save_mesh(textured_sphere(), src)
    proj.store.import_file(src, slug="ball", modality="3d", kind="mesh")
    yield proj
    set_registry(None)


def test_simplify_creates_new_version_of_same_asset(project):
    av = run_pipeline(project, "3d.simplify", {"input_asset": "ball", "target_faces": 300})
    assert av.ref == "ball@v002" and av.parent == "ball@v001"
    assert av.extra["stats"]["after"]["faces"] <= 300
    assert av.extra["stats"]["after"]["has_texture"]


def test_lowpoly_uses_project_palette_and_new_asset(project):
    av = run_pipeline(project, "3d.lowpoly", {"input_asset": "ball@v001", "target_faces": 150})
    assert av.slug == "ball-lowpoly"
    assert set(av.extra["lowpoly"]["palette"]) <= {"#d03030", "#3030d0", "#30d030"}


def test_print_then_split_chain(project):
    printed = run_pipeline(project, "3d.print", {"input_asset": "ball", "target_max_dim_mm": 200})
    assert printed.kind == "print"
    roles = {f.role for f in printed.files}
    assert {"primary", "print", "report"} <= roles
    assert any("split" in w for w in printed.extra["print"]["warnings"])

    parts = run_pipeline(project, "3d.split", {"input_asset": printed.ref})
    info = parts.extra["split"]
    assert info["all_fit"] and info["parts"] >= 2
    part_files = [f for f in parts.files if f.role == "part"]
    assert len(part_files) == info["parts"] + 1  # + pin
    report = json.loads(project.store.files_of(parts, "report")[0].read_text())
    assert report["pin"]["count"] >= 1


def test_transform_bottom_center(project):
    av = run_pipeline(project, "3d.transform", {"input_asset": "ball", "pivot": "bottom_center", "target_height": 2.0})
    mesh = g.load_mesh(project.store.files_of(av, "primary")[0])
    assert mesh.bounds[0][1] == pytest.approx(0, abs=1e-5)  # glTF is Y-up
    assert mesh.extents[1] == pytest.approx(2.0, rel=1e-3)


def test_convert_to_stl(project):
    av = run_pipeline(project, "3d.convert", {"input_asset": "ball", "format": "stl"})
    assert project.store.files_of(av, "primary")[0].suffix == ".stl"


def test_repair_make_solid(project):
    av = run_pipeline(project, "3d.repair", {"input_asset": "ball", "make_solid": True})
    assert av.extra["stats"]["after"]["watertight"]


def test_params_schema_is_serializable():
    for p in forge_3d.modality.pipelines:
        json.dumps(p.describe())


def test_print_without_size_shrinks_to_fit_printer(project):
    # the textured sphere is 2 units across: in meters that's 2000 mm, far above the 120 mm test printer
    av = run_pipeline(project, "3d.print", {"input_asset": "ball", "check_walls": False})
    assert av.extra["print"]["fits_build_volume"]
    assert 0 < av.extra["print"]["scaled_to_fit"] < 0.06
    kept = run_pipeline(project, "3d.print", {"input_asset": "ball", "fit_to_printer": False, "check_walls": False})
    assert not kept.extra["print"]["fits_build_volume"]


def test_edit_pipeline_in_viewer_coordinates_keeps_format(project, tmp_path):
    # a Z-up STL 10 x 10 x 40 mm tall: in the Y-up viewer it is 40 tall along Y
    tower = trimesh.creation.box([10, 10, 40])
    tower.apply_translation([0, 0, 20])
    g.save_mesh(tower, tmp_path / "tower.stl")
    project.store.import_file(tmp_path / "tower.stl", slug="tower", modality="3d", kind="print")
    ops = [
        {"op": "cut", "point": [0, 30, 0], "normal": [0, 1, 0], "keep": "back"},  # chop the top 10 mm (viewer Y)
        {"op": "primitive", "shape": "cylinder", "mode": "subtract", "size": [4, 100, 4]},  # vertical hole
    ]
    av = run_pipeline(project, "3d.edit", {"input_asset": "tower", "ops": ops})
    assert av.ref == "tower@v002" and av.kind == "print"
    out_file = project.store.files_of(av, "primary")[0]
    assert out_file.suffix == ".stl"
    out = g.load_mesh(out_file)
    assert out.extents[2] == pytest.approx(30)  # still Z-up on disk, 30 mm tall
    assert out.is_watertight and out.euler_number == 0  # through hole


def test_edit_mesh_op_combines_assets(project, tmp_path):
    g.save_mesh(trimesh.creation.icosphere(radius=0.3), tmp_path / "knob.glb")
    project.store.import_file(tmp_path / "knob.glb", slug="knob", modality="3d", kind="mesh")
    m = np.eye(4)
    m[1, 3] = 1.0
    av = run_pipeline(project, "3d.edit", {"input_asset": "ball", "ops": [{"op": "mesh", "asset": "knob", "matrix": m.tolist()}]})
    out = g.load_mesh(project.store.files_of(av, "primary")[0])
    assert out.bounds[1][1] == pytest.approx(1.3, abs=0.02)


def test_ai_edit_without_source_image_explains(project):
    from forge_core import PipelineError

    with pytest.raises(Exception, match="no source image"):
        run_pipeline(project, "3d.ai_edit", {"input_asset": "ball", "instruction": "make it spiky"})


def test_mesh_exporter_units_axis_and_obj_zip(project):
    from forge_core.export import run_export

    files = run_export(project, "ball", "3d.mesh", {"format": "stl", "units": "mm"})
    stl = g.load_mesh(files[0])
    assert stl.extents.max() == pytest.approx(2000, rel=0.01)  # 2 m sphere -> mm
    obj_files = run_export(project, "ball", "3d.mesh", {"format": "obj", "pivot": "bottom_center"})
    names = {f.suffix for f in obj_files}
    assert ".obj" in names and ".mtl" in names  # textured sphere -> material + texture alongside
    assert g.load_mesh([f for f in obj_files if f.suffix == ".obj"][0]).bounds[0][1] == pytest.approx(0, abs=1e-4)


def test_print_kit_lists_parts_and_pins(project):
    from forge_core.export import run_export

    printed = run_pipeline(project, "3d.print", {"input_asset": "ball", "target_max_dim_mm": 200, "check_walls": False})
    parts = run_pipeline(project, "3d.split", {"input_asset": printed.ref})
    files = run_export(project, parts.ref, "3d.print_kit", {"format": "3mf"})
    readme = [f for f in files if f.name == "LEIA-ME.txt"][0].read_text(encoding="utf-8")
    assert sum(f.suffix == ".3mf" for f in files) == parts.extra["split"]["parts"] + 1
    assert "Pinos" in readme and "imprimir" in readme


def test_derived_assets_record_their_base(project):
    av = run_pipeline(project, "3d.lowpoly", {"input_asset": "ball", "target_faces": 100})
    assert av.extra["base"] == "ball@v001"

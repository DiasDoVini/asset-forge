import json
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from forge_core import Project, run_pipeline
from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from PIL import Image

import forge_3d
from forge_3d import geometry as g
from forge_3d.planner import MissingParts, clip_from_plan, plan_clip


def T(x=0, y=0, z=0):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m.tolist()


@pytest.fixture
def project(tmp_path):
    reg = Registry()
    reg.add(forge_3d.modality)
    set_registry(reg)
    proj = Project.create(tmp_path / "proj", name="Anim")
    # a "rifle": long body box with a magazine box sticking out below it
    body = trimesh.creation.box((2.0, 0.3, 0.2), transform=trimesh.transformations.translation_matrix((0, 1, 0)))
    mag = trimesh.creation.box((0.2, 0.5, 0.15), transform=trimesh.transformations.translation_matrix((0.2, 0.7, 0)))
    rifle = g.solidify(trimesh.util.concatenate([body, mag]))[0]
    g.save_mesh(rifle, tmp_path / "rifle.glb")
    proj.store.import_file(tmp_path / "rifle.glb", slug="rifle", modality="3d", kind="mesh")
    g.save_mesh(trimesh.creation.box((0.3, 0.3, 0.3)), tmp_path / "crate.glb")
    proj.store.import_file(tmp_path / "crate.glb", slug="crate", modality="3d", kind="mesh")
    yield proj
    set_registry(None)


MAG_REGION = {"shape": "box", "size": [0.4, 0.62, 0.4], "matrix": T(0.2, 0.62, 0)}


def make_rig(project, **extra):
    params = {"input_asset": "rifle", "parts": [{"name": "magazine", "region": MAG_REGION, "pivot": [0.2, 0.85, 0]}], **extra}
    return run_pipeline(project, "3d.rig", params)


def test_rig_carves_parts_solid(project):
    av = make_rig(project, props=[{"asset": "crate"}])
    assert av.slug == "rifle-anim" and av.kind == "animation"
    info = av.extra["animation"]
    names = [p["name"] for p in info["parts"]]
    assert names == ["body", "magazine", "crate"]
    assert info["parts"][1]["parent"] == "body" and info["parts"][2]["parent"] is None
    vdir = project.store.version_dir(av.slug, av.version)
    mag = g.load_mesh(vdir / "parts" / "magazine.glb")
    body = g.load_mesh(vdir / "parts" / "body.glb")
    assert mag.is_watertight and body.is_watertight
    assert mag.bounds[0][1] == pytest.approx(0.45, abs=0.01)  # bottom of the magazine
    assert body.bounds[0][1] == pytest.approx(0.85, abs=0.01)  # the body no longer reaches down
    assert av.extra["base"] == "rifle@v001"


def test_rig_faces_mode_keeps_open_meshes(project, tmp_path):
    open_box = trimesh.creation.box((1, 1, 1))
    open_box.update_faces(open_box.face_normals[:, 1] < 0.5)  # remove the top -> open
    g.save_mesh(open_box, tmp_path / "open.glb")
    project.store.import_file(tmp_path / "open.glb", slug="openbox", modality="3d", kind="mesh")
    av = run_pipeline(project, "3d.rig", {"input_asset": "openbox", "parts": [
        {"name": "front", "region": {"shape": "box", "size": [2, 2, 0.4], "matrix": T(0, 0, 0.5)}}]})
    assert [p["name"] for p in av.extra["animation"]["parts"]] == ["body", "front"]


def test_animate_with_presets_then_explicit_clip_and_delete(project):
    rig = make_rig(project)
    a1 = run_pipeline(project, "3d.animate", {"input_asset": rig.ref, "name": "eject", "duration": 1.0,
                                              "presets": [{"part": "magazine", "preset": "slide", "axis": "y", "distance": -0.4}]})
    assert a1.ref == "rifle-anim@v002"
    clip = {"name": "tilt", "duration": 0.5, "tracks": [{"part": "body", "keys": [
        {"t": 0}, {"t": 0.5, "rotation_deg": [0, 0, 15]}]}]}
    a2 = run_pipeline(project, "3d.animate", {"input_asset": a1.ref, "clip": clip})
    assert [c["name"] for c in a2.extra["animation"]["clips"]] == ["eject", "tilt"]
    vdir = project.store.version_dir(a2.slug, a2.version)
    raw = (vdir / "animated.glb").read_bytes()
    doc = json.loads(raw[20:20 + int.from_bytes(raw[12:16], "little")])
    assert [a["name"] for a in doc["animations"]] == ["eject", "tilt"]
    a3 = run_pipeline(project, "3d.animate", {"input_asset": a2.ref, "name": "eject", "delete": True})
    assert [c["name"] for c in a3.extra["animation"]["clips"]] == ["tilt"]


def test_animate_rejects_unknown_parts(project):
    rig = make_rig(project)
    with pytest.raises(Exception, match="unknown part"):
        run_pipeline(project, "3d.animate", {"input_asset": rig.ref, "presets": [{"part": "trigger", "preset": "spin"}]})


class FakeClient:
    """Stands in for anthropic.Anthropic(): returns a canned structured-output response."""

    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.plan))])


def test_planner_builds_clip_and_reports_missing_parts(project):
    from forge_3d.anim_pipelines import load_rig  # noqa: F401  (import check)
    from forge_3d.geometry.anim import Part, Rig

    rig = Rig(parts=[Part(name="body", file="b.glb"), Part(name="magazine", file="m.glb", parent="body")])
    meshes = {"body": trimesh.creation.box(), "magazine": trimesh.creation.box()}
    plan = {"missing_parts": [], "notes": "drops the magazine", "duration": 1.2, "loop": False,
            "tracks": [{"part": "magazine", "keys": [{"t": 0, "position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]},
                                                    {"t": 1.2, "position": [0, -0.5, 0], "rotation_deg": [0, 0, 10], "scale": [1, 1, 1]}]}],
            "attach": []}
    fake = FakeClient(plan)
    clip, notes = plan_clip(rig, meshes, "reload the rifle", name="reload", client=fake)
    assert clip.name == "reload" and clip.duration == pytest.approx(1.2) and notes == ["drops the magazine"]
    sent = fake.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["output_config"]["format"]["type"] == "json_schema"
    assert "magazine" in sent["messages"][0]["content"]
    with pytest.raises(MissingParts, match="bolt"):
        clip_from_plan({"missing_parts": [{"name": "bolt", "description": "the charging handle"}], "notes": ""}, "x")


def test_prompt_mentions_add_props_before_planning(project, monkeypatch):
    rig = make_rig(project)
    seen = {}

    def fake_plan(rig_, meshes, prompt, name, duration, loop):
        seen["parts"] = [p.name for p in rig_.parts]
        from forge_3d.geometry.anim import Attach, Clip

        return Clip(name=name, duration=2, attach=[Attach(part="crate", to="body", start=0.5)]), ["ok"]

    monkeypatch.setattr("forge_3d.planner.plan_clip", fake_plan)
    av = run_pipeline(project, "3d.animate", {"input_asset": rig.ref, "name": "carry", "prompt": "pick up the @crate"})
    assert "crate" in seen["parts"]
    assert [p["name"] for p in av.extra["animation"]["parts"]][-1] == "crate"


def test_animation_exporters(project, tmp_path):
    rig = make_rig(project)
    av = run_pipeline(project, "3d.animate", {"input_asset": rig.ref, "name": "spin", "duration": 1,
                                              "presets": [{"part": "body", "preset": "spin", "amount": 1}]})
    glb = run_export(project, av.ref, "3d.animation_glb", {})
    assert glb[0].suffix == ".glb"
    with pytest.raises(Exception, match="unknown clips"):
        run_export(project, av.ref, "3d.animation_glb", {"clips": ["nope"]})
    # a 4-frame sheet (2 columns) -> gif and mp4
    sheet = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    for i, color in enumerate([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]):
        sheet.paste(color, ((i % 2) * 64 + 16, (i // 2) * 64 + 16, (i % 2) * 64 + 48, (i // 2) * 64 + 48))
    sheet.save(tmp_path / "sheet.png")
    gif = run_export(project, av.ref, "3d.animation_media", {"sheet": str(tmp_path / "sheet.png"), "frames": 4, "columns": 2, "fps": 8})[0]
    assert Image.open(gif).n_frames == 4
    mp4 = run_export(project, av.ref, "3d.animation_media", {"sheet": str(tmp_path / "sheet.png"), "frames": 4, "columns": 2, "format": "mp4"})[0]
    assert mp4.stat().st_size > 500


def test_rig_extension_splits_more_parts_and_keeps_clips(project):
    rig = run_pipeline(project, "3d.rig", {"input_asset": "rifle"})
    anim = run_pipeline(project, "3d.animate", {"input_asset": rig.ref, "name": "idle", "presets": [{"part": "body", "preset": "bob"}]})
    more = run_pipeline(project, "3d.rig", {"input_asset": anim.ref, "parts": [{"name": "magazine", "region": MAG_REGION}]},
                        asset=anim.slug)
    assert more.ref == "rifle-anim@v003"
    info = more.extra["animation"]
    assert [p["name"] for p in info["parts"]] == ["body", "magazine"]
    assert [c["name"] for c in info["clips"]] == ["idle"]
    with pytest.raises(Exception, match="already exists"):
        run_pipeline(project, "3d.rig", {"input_asset": more.ref, "parts": [{"name": "magazine", "region": MAG_REGION}]}, asset=anim.slug)

"""Inimigos em N poses (aceite §C): preset enemy_skeleton, folha de poses (pose_sheet), silhueta de dano,
e pipeline pixel.poses."""
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_pixel import modality
from forge_pixel import validators as V
from forge_pixel.poses import animation_strip, hit_silhouette, onion_skin, pose_sheet
from forge_pixel.spec import PaletteSet, default_specs, palette_group_for_file, spec_name_for_file

K = "#0b0a12"
PAL = PaletteSet.from_dict({"core": [K, "#ffffff"],
                            "groups": {"inimigo/teste": ["#d9d0bf", "#9f9686", "#c2263f", "#3a3447"]}})
SPECS = default_specs()


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield r
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p", pixel={"palette": PAL.model_dump()})


# ---------------------------------------------------------------- spec

def test_enemy_skeleton_preset():
    s = SPECS["enemy_skeleton"]
    assert s.kind == "enemy" and s.canvas == (40, 60) and s.pivot == (10, 0)
    assert (s.pivot[0] / s.width, s.pivot[1] / s.height) == (0.25, 0.0)      # spritePivot do .meta
    assert s.safe_area == (1, 0, 38, 59) and s.outline == "required" and s.check_anchor
    assert s.palette_group == "inimigo/esqueleto" and "danger" in s.tags
    assert spec_name_for_file("enemy_catacumba_esqueleto_v02_3.png") == "enemy_skeleton"
    assert spec_name_for_file("enemy_catacumba_morcego_v01_0.png") == "enemy_flyer"
    assert spec_name_for_file("enemy_catacumba_rato_v01_0.png") == "enemy"
    assert palette_group_for_file("enemy_catacumba_esqueleto_v02_0.png") == "inimigo/esqueleto"
    for ok in ("enemy_catacumba_esqueleto_v02_0.png", "enemy_catacumba_esqueleto_v02_7_hit.png",
               "enemy_catacumba_esqueleto_v01.png"):
        assert V.check_name(ok, s).ok, ok
    assert not V.check_name("enemy_catacumba_esqueleto_v02_0_hit_x.png", s).ok


# ---------------------------------------------------------------- folha e silhueta

def _frame(x, w=12, h=16, color="#d9d0bf"):
    a = np.zeros((h, w, 4), np.uint8)
    a[h - 6:, x:x + 3] = (*bytes.fromhex(color[1:]), 255)
    return Image.fromarray(a, "RGBA")


def test_pose_sheet_strip_and_grid():
    imgs = [_frame(i) for i in range(5)]
    sheet, meta = pose_sheet(imgs, names=[f"f{i}.png" for i in range(5)], classes=["a", "a", "b", "b", "c"],
                             pivot=(3, 0))
    assert sheet.size == (60, 16) and meta["columns"] == 5 and meta["rows"] == 1
    assert meta["frames"][2]["rect"] == [24, 0, 12, 16] and meta["frames"][2]["class"] == "b"
    assert meta["frames"][2]["pivot_sheet"] == [27, 16]
    assert meta["pivot"]["px"] == [3, 0] and meta["pivot"]["norm"] == [0.25, 0.0]
    assert meta["classes"] == {"a": [0, 1], "b": [2, 3], "c": [4]}
    assert np.array_equal(np.asarray(sheet)[:, 24:36], np.asarray(imgs[2]))
    grid, gm = pose_sheet(imgs, columns=2, padding=1)
    assert grid.size == (25, 50) and gm["rows"] == 3 and gm["frames"][3]["rect"] == [13, 17, 12, 16]
    assert gm["frames"][0]["class"] is None
    with pytest.raises(ValueError, match="mesmo canvas"):
        pose_sheet([_frame(0), _frame(0, w=10)])


def test_hit_silhouette_and_review_images():
    img = _frame(2)
    hit = np.asarray(hit_silhouette(img))
    op = np.asarray(img)[..., 3] > 0
    assert np.array_equal(hit[..., 3] > 0, op) and set(np.unique(hit[..., 3])) <= {0, 255}
    assert (hit[op][:, :3] == 255).all()
    assert onion_skin([_frame(0), _frame(3)], scale=2).size == (24, 32)
    assert animation_strip([_frame(0), _frame(3)], scale=2, labels=["a", "b"], pivot=(3, 0)).width > 48


# ---------------------------------------------------------------- pipeline pixel.poses

def _pose_map(arm_dx: int, eye: str = "R") -> dict:
    """Bonequinho 12x16: corpo fixo, braço/arma que avança `arm_dx` px."""
    rows = ["." * 12 for _ in range(16)]
    g = [list(r) for r in rows]
    for y in range(4, 15):
        g[y][2] = "s"
        g[y][3] = "b"
    g[5][3] = eye
    for x in range(4, 4 + arm_dx):
        g[8][x] = "b"
    g[15][2] = "s"
    g[15][3] = "b"
    return {"width": 12, "height": 16, "legend": {"b": "#d9d0bf", "s": "#9f9686", "R": "#c2263f"},
            "rows": ["".join(r) for r in g], "outline": {"auto": True, "color": K}}


SPEC_SMALL = {"base": "enemy", "canvas": [12, 16], "pivot": [3, 0], "palette_group": "inimigo/teste",
              "safe_area": None, "check_anchor": True}


def test_pixel_poses_pipeline(project):
    maps = [_pose_map(2), _pose_map(3), _pose_map(5), _pose_map(7)]
    maps[1]["legend"]["b"] = "#dad0be"                               # fora da paleta: quantiza
    params = {"maps": maps, "classes": ["loco", "loco", "ataque", "ataque"], "labels": ["a", "b", "c", "d"],
              "spec": SPEC_SMALL, "prefix": "enemy_catacumba_teste_v02"}
    av = run_pipeline(project, "pixel.poses", params)
    store = project.store
    by_name = {f.name: f for f in store.files_of(av)}
    for i in range(4):
        assert f"enemy_catacumba_teste_v02_{i}.png" in by_name and f"enemy_catacumba_teste_v02_{i}_hit.png" in by_name
    roles = {r: [f.name for f in store.files_of(av, r)] for r in ("primary", "source", "preview", "report", "part")}
    assert roles["primary"] == ["sheet.png"] and sorted(roles["source"]) == ["pixelmaps.json", "sheet.json"]
    assert len(roles["part"]) == 8 and roles["preview"] == ["preview.png"]
    assert av.kind == "spritesheet"
    meta = json.loads(by_name["sheet.json"].read_text(encoding="utf-8"))
    assert meta["frame_size"] == [12, 16] and meta["count"] == 4 and meta["pivot"]["norm"] == [0.25, 0.0]
    assert [f["class"] for f in meta["frames"]] == ["loco", "loco", "ataque", "ataque"]
    assert meta["frames"][1]["name"] == "enemy_catacumba_teste_v02_1.png" and meta["hit_suffix"] == "_hit"
    with Image.open(by_name["sheet.png"]) as im:
        assert im.size == (48, 16)
    hit = np.asarray(Image.open(by_name["enemy_catacumba_teste_v02_0_hit.png"]).convert("RGBA"))
    assert set(map(tuple, hit[hit[..., 3] > 0][:, :3])) == {(255, 255, 255)}
    ex = av.extra
    assert ex["pixel"]["quantized"] == {"#dad0be": "#d9d0bf"}
    assert ex["validation"]["pose_set"]["ground_line"] and ex["validation"]["pose_set"]["pose_palette"]
    rep = json.loads(by_name["report.json"].read_text(encoding="utf-8"))
    assert set(rep["frames"]) == {f"enemy_catacumba_teste_v02_{i}.png" for i in range(4)}
    assert all(r["ok"] for r in rep["frames"].values()), [r["checks"] for r in rep["frames"].values() if not r["ok"]]
    pairs = {(p["from"], p["to"]) for c in rep["pose_set"]["checks"] if c["id"] == "pose_change" for p in c["data"]["pairs"]}
    assert pairs == {(0, 1), (2, 3)}                                  # só dentro da classe
    # determinismo: mesmas poses = mesmos bytes
    av2 = run_pipeline(project, "pixel.poses", params)
    a = {f.name: f.read_bytes() for f in store.files_of(av, "part")}
    b = {f.name: f.read_bytes() for f in store.files_of(av2, "part")}
    assert a == b
    assert store.files_of(av, "primary")[0].read_bytes() == store.files_of(av2, "primary")[0].read_bytes()


def test_pixel_poses_rejects_bad_input(project):
    with pytest.raises(ValueError, match="classes tem 1"):
        run_pipeline(project, "pixel.poses", {"maps": [_pose_map(2), _pose_map(3)], "classes": ["a"],
                                              "spec": SPEC_SMALL})
    with pytest.raises(ValueError, match="canvas da spec"):
        run_pipeline(project, "pixel.poses", {"maps": [_pose_map(2)], "spec": "enemy"})


def test_fx_alert_preset():
    s = SPECS["fx_alert"]
    assert s.kind == "fx" and s.canvas == (5, 10) and s.pivot == (2.5, 0) and s.outline == "required"
    assert s.palette_group == "fx" and "danger" in s.tags
    assert spec_name_for_file("fx_catacumba_alerta_v01.png") == "fx_alert"
    assert spec_name_for_file("fx_mining_crack_1_v01.png") == "crack"
    assert V.check_name("fx_catacumba_alerta_v01.png", s).ok


def test_pose_set_cyclic_compares_last_to_first():
    imgs = [_frame(0), _frame(1), _frame(2), _frame(9)]               # 2 -> 9 muda tudo; 9 -> 0 também
    base = {c.id: c for c in V.check_pose_set(imgs[:3] + [_frame(3)], classes=["a"] * 4)}
    loop = {c.id: c for c in V.check_pose_set(imgs[:3] + [_frame(3)], classes=["a"] * 4, cyclic=["a"])}
    assert [(p["from"], p["to"]) for p in base["pose_change"].data["pairs"]] == [(0, 1), (1, 2), (2, 3)]
    assert [(p["from"], p["to"]) for p in loop["pose_change"].data["pairs"]][-1] == (3, 0)
    assert loop["pose_change"].data["cyclic"] == ["a"]
    assert not loop["pose_change"].ok                                   # 3 -> 0 anda 3 px: mais de 60%
    two = {c.id: c for c in V.check_pose_set(imgs[:2], classes=["a", "a"], cyclic=["a"])}
    assert len(two["pose_change"].data["pairs"]) == 1                   # 2 quadros: o laço é o mesmo par


def _bat_map(wing_up: bool, head_extra: int = 0) -> dict:
    """Voador 12x12: corpo fixo (x 4..7, y 2..9 de baixo) e uma asa que sobe ou desce."""
    g = [["."] * 12 for _ in range(12)]
    for y in range(2, 10):
        for x in range(4, 8):
            g[11 - y][x] = "b"
    g[11 - 8][6] = "R"                                                  # olho em y=8
    for x in range(8, 8 + head_extra):
        g[11 - 8][x] = "b"
    for x in range(1, 4):
        g[11 - (8 if wing_up else 4)][x] = "m"
        g[11 - (7 if wing_up else 3)][x] = "m"
    return {"width": 12, "height": 12, "legend": {"b": "#d9d0bf", "R": "#c2263f", "m": "#3a3447"},
            "rows": ["".join(r) for r in g], "outline": {"auto": True, "color": K}}


FLYER_SMALL = {"base": "enemy_flyer", "canvas": [12, 12], "pivot": [6, 6], "palette_group": "inimigo/teste"}
BODY = {"box": [4, 1, 9, 10], "body_colors": ["#d9d0bf", "#c2263f", K], "wing_colors": ["#3a3447"],
        "compare": [0, 1], "eye_color": "#c2263f", "head_poses": [0, 1]}


def test_pixel_poses_body_coherence(project):
    params = {"maps": [_bat_map(True), _bat_map(False)], "classes": ["voo_a", "voo_b"], "spec": FLYER_SMALL,
              "mode": "flyer", "prefix": "enemy_catacumba_teste_v02", "body": BODY,
              "sheet_meta": {"animations": {"voo": [0, 1]}}}
    av = run_pipeline(project, "pixel.poses", params)
    rep = json.loads(next(f for f in project.store.files_of(av, "report") if f.suffix == ".json").read_text("utf-8"))
    ids = {c["id"]: c for c in rep["pose_set"]["checks"]}
    assert {"center_of_mass", "body_mask", "body_palette", "body_head"} <= set(ids)
    assert ids["body_head"]["ok"] and ids["body_mask"]["ok"] and rep["ok"], ids
    assert av.extra["validation"]["pose_set"]["body_head"] is True
    assert av.extra["pixel"]["body"]["box"] == [4, 1, 9, 10]
    meta = json.loads(next(f for f in project.store.files_of(av, "source") if f.name == "sheet.json").read_text("utf-8"))
    assert meta["animations"] == {"voo": [0, 1]} and meta["pivot"]["norm"] == [0.5, 0.5]
    # cabeça 2 px mais larga numa pose do ciclo: a coerência do corpo reprova o conjunto
    bad = run_pipeline(project, "pixel.poses", {**params, "maps": [_bat_map(True), _bat_map(False, head_extra=2)]})
    assert not bad.extra["validation"]["ok_all"] and bad.extra["validation"]["pose_set"]["body_head"] is False
    # head_offsets aceita chaves em texto (JSON) e libera a pose à parte
    ok2 = run_pipeline(project, "pixel.poses", {**params, "maps": [_bat_map(True), _bat_map(False, head_extra=2)],
                                                "body": {**BODY, "compare": [0, 1], "head_poses": [0],
                                                         "head_offsets": {"1": [0, 2]}}})
    assert ok2.extra["validation"]["pose_set"]["body_head"] is True
    with pytest.raises(ValueError, match="reservada"):
        run_pipeline(project, "pixel.poses", {**params, "sheet_meta": {"frames": []}})
    with pytest.raises(ValueError, match="fora de 0..1"):
        run_pipeline(project, "pixel.poses", {**params, "body": {**BODY, "compare": [0, 5]}})

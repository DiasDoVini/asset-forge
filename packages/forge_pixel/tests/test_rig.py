"""Rig 2D (forge_pixel.rig): ossos, composição com pivô na origem do osso e teste de junção."""
import numpy as np
import pytest
from PIL import Image

from forge_pixel import pixelmap as pmod
from forge_pixel import rig as R

LIMB = {"format": "asset-forge-pixelmap/1", "width": 8, "height": 12, "legend": {"a": "#5a5a88", "b": "#7a7aae"},
        "rows": ["........", "........"] + ["..aabb.."] * 7 + ["........"] * 3}
SHIN = {**LIMB, "rows": ["........", "........"] + ["...abb.."] * 7 + ["........"] * 3}
PELVIS = {"format": "asset-forge-pixelmap/1", "width": 14, "height": 8, "legend": {"a": "#3e3e66"},
          "rows": ["......"] * 0 + ["." * 14] * 4 + ["...aaaaaaaa..."] * 4}


def _legs(thigh=LIMB, shin=SHIN):
    t, s = pmod.render(thigh), pmod.render(shin)
    return {"pelvis": pmod.render(PELVIS), "thigh_L": t, "thigh_R": t, "shin_L": s, "shin_R": s}


@pytest.fixture(scope="module")
def rig():
    return R.Rig.profile()


def test_bone_chain_at_rest(rig):
    w = rig.world({"hipsY": 14})
    assert np.allclose(w["shin_R"][:2, 2], [1.5, 7])
    assert np.allclose(w["hand_R"][:2, 2], [1, 13 + 12 - 12])        # torso -1, braço +12, -6, -6
    assert rig.pivots["thigh_R"] == (4.0, 10.0) and rig.pivots["boot_R"] == (4.0, 10.0)
    assert rig.pivots["pelvis"] == (7.0, 4.0) and rig.pivots["hand_L"] == (4.0, 6.0)


def test_rotation_is_ccw_forward(rig):
    # coxa a +90°: a perna aponta para a frente (+x)
    ax, ay = rig.bone_axis("thigh_R", {"hipsY": 14, "thigh_R": 90})
    assert ax == pytest.approx(1.0) and ay == pytest.approx(0.0, abs=1e-9)
    assert rig.joint_point(R.Joint("j", "thigh_R", "shin_R", "shin_R"), {"hipsY": 14, "thigh_R": 90}) == \
        pytest.approx((1.5 + 7, 14))


def test_compose_puts_pivot_on_bone(rig):
    # sprite 8x12 com um único pixel logo abaixo-à-direita do pivô (4,10) da coxa
    img = Image.new("RGBA", (8, 12))
    img.putpixel((4, 12 - 1 - 9), (255, 0, 0, 255))
    comp = rig.compose({"thigh_R": img}, {"hipsY": 14}, bounds=(-4, 0, 6, 20))
    ys, xs = np.nonzero(np.asarray(comp.image)[..., 3])
    assert len(xs) == 1
    # pixel do sprite [4,5]x[9,10] -> mundo x [1.5, 2.5]: o centro de amostra 1.5 (pixel do mundo [1,2])
    # cai exatamente na borda esquerda dele e o desempate pega o texel da direita
    col, row = int(xs[0]), int(ys[0])
    assert comp.x0 + col == 1 and comp.y1 - row - 1 == 13
    assert comp.order[comp.owner[row, col]] == "thigh_R"


def test_draw_order_front_wins(rig):
    a = Image.new("RGBA", (8, 12), (10, 20, 30, 255))
    b = Image.new("RGBA", (8, 12), (200, 0, 0, 255))
    comp = rig.compose({"thigh_L": a, "thigh_R": b}, {"hipsY": 14}, bounds=(-1, 6, 1, 13))
    assert tuple(np.asarray(comp.image)[3, 1, :3]) == (200, 0, 0)     # R vem depois de L


def test_end_extent():
    e = R.end_extent(pmod.render(SHIN), (4, 10), "top")
    assert e == {"y": -1.0, "x_min": -1.0, "x_max": 2.0, "width": 3.0}
    e = R.end_extent(pmod.render(LIMB), (4, 10), "bottom")
    assert e["y"] == -7.0 and e["width"] == 4.0


def test_joint_report_same_parts_pass(rig):
    parts = _legs()
    rows = R.joint_report(rig, parts, parts, R.POSES, changed=["thigh_R", "shin_R"])
    names = {r["joint"] for r in rows}
    assert {"joelho_R", "quadril_R"} <= names
    assert all(r["ok"] for r in rows)
    s = rows[0]["poses"]["idle"]
    assert s["d_width"] == 0 and s["gaps_new"] == s["gaps_cur"]


def test_joint_report_flags_narrow_knee(rig):
    narrow = {**LIMB, "rows": ["........", "........"] + ["..aabb.."] * 6 + ["...ab..."] + ["........"] * 3}
    cur, new = _legs(), _legs(thigh=narrow)
    rows = {r["joint"]: r for r in R.joint_report(rig, new, cur, {"idle": R.POSES["idle"]})}
    knee = rows["joelho_R"]
    assert not knee["ok"] and not knee["ends_ok"]
    assert knee["ends"]["parent"]["delta"]["x_min"] == 1.0          # ponta de baixo da coxa 1 px mais estreita
    assert rows["quadril_R"]["ends_ok"]                              # o topo não mudou


def test_seam_stats_counts_gap_and_break():
    img = Image.new("RGBA", (7, 5))
    k, f = (11, 10, 18, 255), (90, 90, 136, 255)
    for x in range(7):
        img.putpixel((x, 2), f)
    img.putpixel((3, 2), (0, 0, 0, 0))                               # fresta de 1 px
    comp = R.Composite(img, np.zeros((5, 7), int), ["x"], 0.0, 5.0, 1)
    s = R.seam_stats(comp, (3.5, 2.5), radius=3)
    assert s["gaps"] == 1
    assert s["outline_breaks"] == 6                                  # sem contorno nenhum
    img2 = img.copy()
    for x in range(7):
        img2.putpixel((x, 1), k)
        img2.putpixel((x, 3), k)
    s2 = R.seam_stats(R.Composite(img2, np.zeros((5, 7), int), ["x"], 0.0, 5.0, 1), (3.5, 2.5), radius=3)
    assert s2["gaps"] == 1 and s2["outline_breaks"] < s["outline_breaks"]


def test_report_markdown(rig):
    parts = _legs()
    md = R.report_markdown(R.joint_report(rig, parts, parts, {"idle": R.POSES["idle"]}, changed=["shin_R"]))
    assert "joelho_R" in md and "| sim |" in md


# ---------------------------------------------------------------- lote 2: ombro, capa, regra "cover", barra × coxa

ARM = {**LIMB, "rows": ["........", "........"] + ["...abb.."] * 6 + ["...aaa.."] + ["........"] * 3}
TORSO = {"format": "asset-forge-pixelmap/1", "width": 16, "height": 18, "legend": {"a": "#7e1a2f"},
         "rows": ["." * 16] * 4 + ["....aaaaaaaa...."] * 11 + ["...aaaaaaaaaa..."] * 3}
CAPE = {"format": "asset-forge-pixelmap/1", "width": 12, "height": 14, "legend": {"a": "#7e1a2f"},
        "rows": ["." * 12] * 2 + ["....aaaaa..."] * 9 + ["." * 12] * 3}


def _upper(torso=TORSO, arm=ARM, cape=CAPE):
    t, a, c = pmod.render(torso), pmod.render(arm), pmod.render(cape)
    return {**_legs(), "torso": t, "arm_up_L": a, "arm_up_R": a, "cape_0": c, "cape_1": c}


def test_shoulder_and_cape_joints_exist(rig):
    names = {j.name: j for j in R.PROFILE_JOINTS}
    assert names["ombro_R"].bone == "upper_arm_R" and names["ombro_R"].parent_end is None
    assert names["capa"].parent == "cape_0" and names["capa"].child == "cape_1"
    assert names["cintura"].rule == "cover"
    # ombro R parado: braço pende do torso em (1, 12) acima do quadril-1
    assert rig.joint_point(names["ombro_R"], R.POSES["idle"]) == pytest.approx((1, 14 - 1 + 12))
    # capa: segmento 1 a 8 px abaixo do pivô do segmento 0
    w = rig.world({"hipsY": 14})
    assert np.allclose(w["cape_1"][:2, 2], [-3, 14 - 1 + 12 - 8])


def test_arm_poses_reach_205(rig):
    assert R.ARM_POSES["arm_205"]["upper_arm_R"] == 205
    assert set(R.POSE_LABELS) >= set(R.POSES) | set(R.ARM_POSES)


def test_shoulder_and_cape_same_parts_pass(rig):
    parts = _upper()
    rows = {r["joint"]: r for r in R.joint_report(rig, parts, parts, {**R.POSES, **R.ARM_POSES},
                                                  changed=["torso", "cape_1"])}
    assert {"ombro_L", "ombro_R", "capa", "cintura"} <= set(rows)
    assert all(r["ok"] for r in rows.values())
    assert "parent" not in rows["ombro_R"]["ends"]          # o torso não tem ponta no ombro


def test_shoulder_flags_narrow_arm_top_and_cape_step(rig):
    narrow = {**ARM, "rows": ["........", "........"] + ["....bb.."] + ["...abb.."] * 5 + ["...aaa.."] + ["........"] * 3}
    step = {**CAPE, "rows": ["." * 12] * 2 + [".....aaaa..."] + ["....aaaaa..."] * 8 + ["." * 12] * 3}
    cur, new = _upper(), _upper(arm=narrow, cape=step)
    rows = {r["joint"]: r for r in R.joint_report(rig, new, cur, {"idle": R.POSES["idle"]})}
    assert not rows["ombro_R"]["ok"] and rows["ombro_R"]["ends"]["child"]["delta"]["x_min"] == 1.0
    assert not rows["capa"]["ok"]


def test_waist_cover_rule_allows_hem_change_but_needs_cover(rig):
    longer = {**TORSO, "rows": TORSO["rows"][:15] + ["..aaaaaaaaaaa..."] * 3}           # barra mais larga: ok
    short = {**TORSO, "rows": TORSO["rows"][:15] + [".....aaaaaa....."] * 3}            # não cobre a pelve: falha
    cur = _upper()
    ok = {r["joint"]: r for r in R.joint_report(rig, _upper(torso=longer), cur, {"idle": R.POSES["idle"]})}
    bad = {r["joint"]: r for r in R.joint_report(rig, _upper(torso=short), cur, {"idle": R.POSES["idle"]})}
    assert ok["cintura"]["ok"] and ok["cintura"]["ends"]["child"]["delta"]["x_min"] == -1.0
    assert not bad["cintura"]["ends_ok"]


def test_covered_outline_hem_over_thigh(rig):
    flare = {**TORSO, "rows": TORSO["rows"][:15] + ["aaaaaaaaaaaaaaaa"] * 3}             # barra enorme
    pose = R.POSES["crouch"]
    base = R.covered_outline(rig, _upper(), pose, "thigh_R", "torso", max_local_y=7)
    big = R.covered_outline(rig, _upper(torso=flare), pose, "thigh_R", "torso", max_local_y=7)
    assert big["under_outline_px"] > 0 and big["covered_px"] > base["covered_px"]
    assert R.covered_outline(rig, _upper(), R.POSES["idle"], "thigh_R", "torso", max_local_y=7)["covered_px"] == 0


def test_elbow_joint(rig):
    j = {j.name: j for j in R.PROFILE_JOINTS}["cotovelo_R"]
    assert (j.parent, j.child, j.bone) == ("arm_up_R", "arm_low_R", "forearm_R")
    # cotovelo 6 px abaixo do ombro com o braço pendurado
    assert rig.joint_point(j, {"hipsY": 14}) == pytest.approx((1, 13 + 12 - 6))
    a = pmod.render(ARM)
    parts = {**_upper(), "arm_low_L": a, "arm_low_R": a}
    rows = {r["joint"]: r for r in R.joint_report(rig, parts, parts, R.ARM_POSES, changed=["arm_low_R"])}
    assert rows["cotovelo_R"]["ok"]


def test_visible_area_shin_under_boot(rig):
    boot = {"format": "asset-forge-pixelmap/1", "width": 12, "height": 12, "legend": {"a": "#4a4578"},
            "rows": ["." * 12] * 5 + ["..aaaaa....."] * 2 + ["..aaaaaaaa.."] * 4 + ["." * 12]}
    parts = {**_legs(), "boot_R": pmod.render(boot)}
    va = R.visible_area(rig, parts, "shin_R", {"idle": R.POSES["idle"]})
    # o cano da bota (topo do contorno em y7) cobre a canela de y7 para baixo; y9/y8 aparecem
    assert va["rows"][9][1] > 0 and va["rows"][8][1] > 0
    assert set(va["hidden_rows"]) >= {3, 4, 5, 6}
    assert 0 < va["ratio"] < 1 and va["mask"].shape == (12, 8)
    alone = R.visible_area(rig, {"shin_R": parts["shin_R"]}, "shin_R", [R.POSES["idle"]])
    assert alone["ratio"] == 1.0 and alone["always"][alone["mask"]].all()


def test_neck_joint(rig):
    j = {j.name: j for j in R.PROFILE_JOINTS}["pescoco"]
    assert (j.parent, j.child, j.parent_end, j.child_end) == ("torso", "head", None, "bottom")
    assert rig.joint_point(j, {"hipsY": 14}) == pytest.approx((0, 13 + 13))


def test_weapon_socket_and_back_joint(rig):
    w = rig.world({"hipsY": 14})
    assert np.allclose(w["weapon_R"][:2, 2], [1, 13 + 12 - 12 - 1])          # 1 px abaixo da mão
    order = rig.order
    assert order.index("weapon_R") == order.index("hand_R") + 1
    assert order.index("weapon_L") == order.index("hand_L") + 1
    assert rig.pivots["weapon_R"] == (4.0, 30.0)
    names = {j.name for j in R.PROFILE_JOINTS}
    assert {"costas", "empunhadura_L", "empunhadura_R"} <= names


# ---------------------------------------------------------------- layout 3/4 (spec-personagem-3-4.md)

def test_layout34_offsets_order_and_pivots():
    r34 = R.Rig.three_quarter()
    bones = {b.name: b for b in r34.bones}
    assert bones["thigh_R"].pos == (3, 0) and bones["thigh_L"].pos == (-3, 0)
    assert bones["upper_arm_R"].pos == (4, 12) and bones["upper_arm_L"].pos == (-5, 12)
    assert bones["cape_0"].pos == (0, 12) and bones["cape_1"].pos == (0, -12) and bones["head"].pos == (0, 13)
    o = r34.order
    # capa → braço de trás → perna de trás → (pelve) tronco → perna da frente → cabeça → arma → braço da frente
    seq = ["cape_1", "cape_0", "arm_up_L", "thigh_L", "torso", "thigh_R", "head", "weapon_R", "arm_up_R", "hand_R"]
    assert [o.index(p) for p in seq] == sorted(o.index(p) for p in seq)
    assert o.index("weapon_L") < o.index("torso")
    assert r34.pivots["torso"] == (9.0, 0.0) and r34.pivots["cape_0"] == (9.0, 15.0) and r34.pivots["cape_1"] == (9.0, 13.0)
    # o perfil continua intacto
    prof = R.Rig.profile()
    assert {b.name: b.pos for b in prof.bones}["thigh_R"] == (1.5, 0) and prof.pivots["torso"] == (8.0, 0.0)
    assert prof.order.index("torso") < prof.order.index("head") and prof.order.index("pelvis") < prof.order.index("thigh_R")


def test_layout34_poses_and_swaps():
    assert set(R.POSES34) == {"idle", "step", "strike_90", "strike_165", "strike_205"}
    assert R.POSES34["strike_205"]["upper_arm_R"] == 205 and R.POSES34["idle"]["thigh_R"] == 0
    a, b = Image.new("RGBA", (18, 18)), Image.new("RGBA", (18, 18), (1, 2, 3, 255))
    sp = R.sprites_for_pose({"torso": a, "torso_b": b}, "strike_90")
    assert sp["torso"] is b and R.sprites_for_pose({"torso": a, "torso_b": b}, "idle")["torso"] is a
    w = R.Rig.three_quarter().world(R.POSES34["idle"])
    assert np.allclose(w["upper_arm_R"][:2, 2], [4, 14 - 1 + 12])


def test_seam_report_flags_step_and_gap():
    r34 = R.Rig.three_quarter()
    t = pmod.render(LIMB)
    s4 = pmod.render({**SHIN, "rows": ["........", "........"] + ["..aabb.."] * 7 + ["........"] * 3})
    rows = {r["joint"]: r for r in R.seam_report(r34, {"thigh_R": t, "shin_R": s4}, {"idle": R.POSES34["idle"]})}
    assert rows["joelho_R"]["step"]["left"] == 0 and rows["joelho_R"]["gaps"] == 0
    narrow = pmod.render(SHIN)                                              # canela 3 px sob coxa 4 px: degrau à esquerda
    rows = {r["joint"]: r for r in R.seam_report(r34, {"thigh_R": t, "shin_R": narrow}, {"idle": R.POSES34["idle"]})}
    assert rows["joelho_R"]["step"]["left"] == 1.0

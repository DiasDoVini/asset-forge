"""Exportação de sistemas de partículas para engines.

Godot 4: cena .tscn com um CPUParticles2D por emissor (y para baixo: o y é invertido).
Unity: o JSON + texturas + um script de editor (AssetForgeParticles.cs) que cria o GameObject com
ParticleSystems pelo menu Tools ▸ Asset Forge ▸ Build Particle System (1 unidade = 100 px).
"""
from __future__ import annotations

import math
import re

from .particles import Emitter, ParticleSystem, parse_color

TEX_SIZE = 64


def _f(x: float) -> str:
    s = f"{float(x):.5g}"
    return s


def _curve_points(em: Emitter) -> tuple[list[list[float]], float]:
    """Pontos da curva de tamanho normalizados para 0..1 (Godot/Unity) + fator máximo."""
    mx = max(1e-6, max(v for _, v in em.size_curve))
    return [[t, v / mx] for t, v in em.size_curve], mx


def _avg(v: list[float]) -> float:
    return (v[0] + v[1]) / 2


def _amount(em: Emitter, duration: float) -> int:
    n = em.rate * em.lifetime[1] + sum(b.count for b in em.bursts)
    return max(1, min(em.max_particles, math.ceil(n)))


def godot_tscn(ps: ParticleSystem, tex_paths: dict[str, str], res_dir: str) -> str:
    """tex_paths: textura do emissor → nome do arquivo PNG; res_dir: pasta res:// onde ficam os PNGs."""
    ext, subs, nodes = [], [], []
    tex_ids: dict[str, str] = {}
    for i, (tex, fname) in enumerate(sorted(tex_paths.items())):
        tid = f"{i + 1}_tex"
        tex_ids[tex] = tid
        ext.append(f'[ext_resource type="Texture2D" path="res://{res_dir.strip("/")}/{fname}" id="{tid}"]')
    root = re.sub(r"[^A-Za-z0-9_]", "_", ps.name.title()) or "Particles"
    nodes.append(f'[node name="{root}" type="Node2D"]')
    for i, em in enumerate(ps.emitters):
        sid = f"em{i}"
        pts, mx = _curve_points(em)
        data = ", ".join(f"Vector2({_f(t)}, {_f(v)}), 0.0, 0.0, 0, 0" for t, v in pts)
        subs.append(f'[sub_resource type="Curve" id="curve_{sid}"]\n_data = [{data}]\npoint_count = {len(pts)}')
        offs = ", ".join(_f(t) for t, _ in em.color)
        cols = ", ".join(", ".join(_f(c) for c in parse_color(col)) for _, col in em.color)
        subs.append(f'[sub_resource type="Gradient" id="grad_{sid}"]\noffsets = PackedFloat32Array({offs})\n'
                    f'colors = PackedColorArray({cols})')
        if em.blend == "add":
            subs.append(f'[sub_resource type="CanvasItemMaterial" id="mat_{sid}"]\nblend_mode = 1')
        one_burst = em.rate == 0 and em.bursts
        d = math.radians(em.direction)
        shape_lines = []
        if em.shape in ("circle",):
            shape_lines = ["emission_shape = 1", f"emission_sphere_radius = {_f(em.radius)}"]
        elif em.shape == "ring":
            shape_lines = ["emission_shape = 2", f"emission_sphere_radius = {_f(em.radius)}"]
        elif em.shape in ("box", "line"):
            shape_lines = ["emission_shape = 3", f"emission_rect_extents = Vector2({_f(em.width / 2)}, {_f(max(0.5, em.height / 2))})"]
        damping = em.drag * _avg(em.speed)
        lines = [
            f'[node name="{re.sub(r"[^A-Za-z0-9_]", "_", em.name)}" type="CPUParticles2D" parent="."]',
            *([f'material = SubResource("mat_{sid}")'] if em.blend == "add" else []),
            f"position = Vector2({_f(em.position[0])}, {_f(-em.position[1])})",
            f"amount = {_amount(em, ps.duration)}",
            f"lifetime = {_f(em.lifetime[1])}",
            f"one_shot = {'true' if (one_burst and not ps.loop) else 'false'}",
            f"explosiveness = {'1.0' if one_burst else '0.0'}",
            f"randomness = {_f(1 - em.lifetime[0] / max(em.lifetime[1], 1e-6))}",
            f"preprocess = {_f(em.lifetime[1]) if ps.loop and not one_burst else '0.0'}",
            f'texture = ExtResource("{tex_ids.get(em.texture, next(iter(tex_ids.values())))}")',
            *shape_lines,
            f"direction = Vector2({_f(math.cos(d))}, {_f(-math.sin(d))})",
            f"spread = {_f(em.spread)}",
            f"gravity = Vector2({_f(em.gravity[0])}, {_f(-em.gravity[1])})",
            f"initial_velocity_min = {_f(em.speed[0])}",
            f"initial_velocity_max = {_f(em.speed[1])}",
            f"damping_min = {_f(damping * 0.8)}",
            f"damping_max = {_f(damping)}",
            f"radial_accel_min = {_f(em.radial_accel)}",
            f"radial_accel_max = {_f(em.radial_accel)}",
            f"tangential_accel_min = {_f(-em.tangential_accel)}",
            f"tangential_accel_max = {_f(-em.tangential_accel)}",
            f"angle_min = {_f(-em.rotation[1])}",
            f"angle_max = {_f(-em.rotation[0])}",
            f"angular_velocity_min = {_f(-em.spin[1])}",
            f"angular_velocity_max = {_f(-em.spin[0])}",
            f"scale_amount_min = {_f(em.size[0] * mx / TEX_SIZE)}",
            f"scale_amount_max = {_f(em.size[1] * mx / TEX_SIZE)}",
            f'scale_amount_curve = SubResource("curve_{sid}")',
            f'color_ramp = SubResource("grad_{sid}")',
            *(["particle_flag_align_y = true"] if (em.align_to_velocity or em.stretch) else []),
        ]
        nodes.append("\n".join(lines))
    head = f"[gd_scene load_steps={len(ext) + len(subs) + 1} format=3]"
    return "\n\n".join([head, "\n".join(ext), *subs, *nodes]) + "\n"


UNITY_SCRIPT = r'''// Asset Forge particle importer for Unity (put this file in an "Editor" folder).
// Select the exported .particles.json (TextAsset) and run Tools > Asset Forge > Build Particle System.
// Textures are loaded from the same folder as the JSON (soft.png, spark.png, ...). 1 unit = 100 px.
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class AssetForgeParticles
{
    [System.Serializable] public class Burst { public float time; public int count; }
    [System.Serializable] public class Emitter
    {
        public string name; public string texture; public string blend; public float rate; public Burst[] bursts;
        public int max_particles; public float start_delay; public string shape; public float radius, width, height;
        public float[] position; public float direction, spread; public float[] speed, lifetime, gravity;
        public float drag, radial_accel, tangential_accel, turbulence; public float[] size; public float[] size_curve_t, size_curve_v;
        public float[] rotation, spin; public bool align_to_velocity; public float stretch;
        public float[] color_t; public string[] color_c; public bool random_color;
    }
    [System.Serializable] public class System { public string name; public float duration; public bool loop; public Emitter[] emitters; }

    const float PPU = 100f;

    [MenuItem("Tools/Asset Forge/Build Particle System")]
    static void Build()
    {
        var json = Selection.activeObject as TextAsset;
        if (json == null) { EditorUtility.DisplayDialog("Asset Forge", "Select the exported .particles.json first.", "OK"); return; }
        var data = JsonUtility.FromJson<System>(json.text);
        string dir = Path.GetDirectoryName(AssetDatabase.GetAssetPath(json));
        var root = new GameObject(data.name);
        foreach (var e in data.emitters) BuildEmitter(root.transform, e, data, dir);
        Selection.activeGameObject = root;
        Undo.RegisterCreatedObjectUndo(root, "Asset Forge particles");
    }

    static Color Hex(string h)
    {
        Color c; ColorUtility.TryParseHtmlString(h.Length == 9 || h.Length == 7 ? h : "#ffffffff", out c); return c;
    }

    static void BuildEmitter(Transform parent, Emitter e, System sys, string dir)
    {
        var go = new GameObject(e.name);
        go.transform.SetParent(parent, false);
        go.transform.localPosition = new Vector3(e.position[0] / PPU, e.position[1] / PPU, 0);
        var ps = go.AddComponent<ParticleSystem>();
        ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
        var main = ps.main;
        main.duration = sys.duration; main.loop = sys.loop; main.startDelay = e.start_delay;
        main.startLifetime = new ParticleSystem.MinMaxCurve(e.lifetime[0], e.lifetime[1]);
        main.startSpeed = new ParticleSystem.MinMaxCurve(e.speed[0] / PPU, e.speed[1] / PPU);
        main.startSize = new ParticleSystem.MinMaxCurve(e.size[0] / PPU, e.size[1] / PPU);
        main.startRotation = new ParticleSystem.MinMaxCurve(-e.rotation[1] * Mathf.Deg2Rad, -e.rotation[0] * Mathf.Deg2Rad);
        main.maxParticles = e.max_particles; main.simulationSpace = ParticleSystemSimulationSpace.World;
        main.gravityModifier = 0; main.prewarm = sys.loop && e.rate > 0;

        var em = ps.emission; em.rateOverTime = e.rate;
        var bursts = new List<ParticleSystem.Burst>();
        if (e.bursts != null) foreach (var b in e.bursts) bursts.Add(new ParticleSystem.Burst(b.time, (short)b.count));
        em.SetBursts(bursts.ToArray());

        var sh = ps.shape; sh.enabled = true;
        sh.rotation = new Vector3(0, 0, e.direction - 90f);
        if (e.shape == "circle" || e.shape == "ring") { sh.shapeType = ParticleSystemShapeType.Circle; sh.radius = Mathf.Max(0.001f, e.radius / PPU); sh.radiusThickness = e.shape == "ring" ? 0 : 1; sh.arc = 360; }
        else if (e.shape == "box" || e.shape == "line") { sh.shapeType = ParticleSystemShapeType.Rectangle; sh.scale = new Vector3(Mathf.Max(0.001f, e.width / PPU), Mathf.Max(0.001f, e.height / PPU), 1); }
        else { sh.shapeType = ParticleSystemShapeType.Cone; sh.radius = 0.0001f; }
        if (sh.shapeType == ParticleSystemShapeType.Cone) { sh.angle = Mathf.Min(89.9f, e.spread); sh.rotation = new Vector3(-90 + 0, 0, 0); go.transform.localRotation = Quaternion.Euler(0, 0, e.direction - 90f); }
        else { sh.randomDirectionAmount = Mathf.Clamp01(e.spread / 180f); }

        var force = ps.forceOverLifetime; force.enabled = e.gravity[0] != 0 || e.gravity[1] != 0;
        force.space = ParticleSystemSimulationSpace.World;
        force.x = e.gravity[0] / PPU; force.y = e.gravity[1] / PPU; force.z = 0;

        if (e.drag > 0) { var lv = ps.limitVelocityOverLifetime; lv.enabled = true; lv.drag = e.drag; lv.multiplyDragByParticleSize = false; lv.multiplyDragByParticleVelocity = false; }
        if (e.radial_accel != 0 || e.tangential_accel != 0)
        {
            var vel = ps.velocityOverLifetime; vel.enabled = true;
            vel.radial = e.radial_accel / PPU * 0.5f; vel.orbitalZ = e.tangential_accel / PPU * 0.05f;
        }
        if (e.turbulence > 0) { var n = ps.noise; n.enabled = true; n.strength = e.turbulence / PPU * 0.5f; n.frequency = 0.5f; }
        if (e.spin != null && (e.spin[0] != 0 || e.spin[1] != 0)) { var rot = ps.rotationOverLifetime; rot.enabled = true; rot.z = new ParticleSystem.MinMaxCurve(-e.spin[1] * Mathf.Deg2Rad, -e.spin[0] * Mathf.Deg2Rad); }

        var sz = ps.sizeOverLifetime; sz.enabled = true;
        var curve = new AnimationCurve();
        float mx = 0.0001f; foreach (var v in e.size_curve_v) mx = Mathf.Max(mx, v);
        for (int i = 0; i < e.size_curve_t.Length; i++) curve.AddKey(e.size_curve_t[i], e.size_curve_v[i] / mx);
        sz.size = new ParticleSystem.MinMaxCurve(mx, curve);

        var col = ps.colorOverLifetime; col.enabled = true;
        var g = new Gradient();
        var ck = new List<GradientColorKey>(); var ak = new List<GradientAlphaKey>();
        for (int i = 0; i < e.color_t.Length && i < 8; i++) { var c = Hex(e.color_c[i]); ck.Add(new GradientColorKey(c, e.color_t[i])); ak.Add(new GradientAlphaKey(c.a, e.color_t[i])); }
        g.SetKeys(ck.ToArray(), ak.ToArray());
        if (e.random_color) { main.startColor = new ParticleSystem.MinMaxGradient(g) { mode = ParticleSystemGradientMode.RandomColor }; }
        col.color = new ParticleSystem.MinMaxGradient(g);

        var r = go.GetComponent<ParticleSystemRenderer>();
        if (e.stretch > 0) { r.renderMode = ParticleSystemRenderMode.Stretch; r.velocityScale = e.stretch * 0.01f; r.lengthScale = 1; }
        var shader = Shader.Find(e.blend == "add" ? "Legacy Shaders/Particles/Additive" : "Legacy Shaders/Particles/Alpha Blended");
        if (shader == null) shader = Shader.Find("Universal Render Pipeline/Particles/Unlit");
        var mat = new Material(shader);
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(Path.Combine(dir, e.texture.Replace("asset:", "") + ".png"));
        if (tex != null) { mat.mainTexture = tex; if (mat.HasProperty("_BaseMap")) mat.SetTexture("_BaseMap", tex); }
        string matPath = AssetDatabase.GenerateUniqueAssetPath(Path.Combine(dir, sys.name + "_" + e.name + ".mat"));
        AssetDatabase.CreateAsset(mat, matPath);
        r.sharedMaterial = mat;
        ps.Play();
    }
}
'''


def unity_json(ps: ParticleSystem) -> dict:
    """JSON achatado para o JsonUtility do Unity (sem listas de listas)."""
    ems = []
    for em in ps.emitters:
        d = em.model_dump()
        d["size_curve_t"] = [t for t, _ in em.size_curve]
        d["size_curve_v"] = [v for _, v in em.size_curve]
        d["color_t"] = [t for t, _ in em.color]
        d["color_c"] = [c if isinstance(c, str) else "#ffffffff" for _, c in em.color]
        d["texture"] = em.texture.replace("asset:", "")
        for k in ("size_curve", "color"):
            d.pop(k)
        ems.append(d)
    return {"name": ps.name, "duration": ps.duration, "loop": ps.loop, "emitters": ems}

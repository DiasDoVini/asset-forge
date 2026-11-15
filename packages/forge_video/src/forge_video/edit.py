"""Operações de edição de vídeo (ffmpeg). Cada op lê `src` e grava `out` (MP4 H.264 + AAC opcional).

Ops (dicts com "op"):
  trim{start,end} · cut{start,end} · speed{factor} · reverse · loop{count,mode:repeat|pingpong} ·
  fade{in_s,out_s} · crop{x,y,w,h (0..1)} · resize{width,height} · fps{fps,smooth} · color{brightness,contrast,
  saturation,gamma} · concat{path,transition:none|fade|wipeleft|slideleft|dissolve,duration} ·
  audio{path,offset,volume,mode:replace|mix} · mute · splice{start,end,path}
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import ff

OPS_HELP = __doc__.split("Ops (dicts com \"op\"):", 1)[1].strip()


def _a(info: ff.Info) -> list[str]:
    return ["-c:a", "aac", "-b:a", "192k"] if info.has_audio else ["-an"]


def _atempo(f: float) -> str:
    parts = []
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.5f}")
    return ",".join(parts)


def _matched(path: Path, ref: ff.Info, td: Path, name: str) -> Path:
    """Re-encoda `path` com o tamanho e fps de `ref` (necessário para concat/xfade)."""
    out = td / name
    ff.normalize(path, out, fps=ref.fps, width=ref.width, height=ref.height)
    return out


def apply(src: Path, op: dict[str, Any], out: Path) -> Path:
    o = op["op"]
    g = op.get
    info = ff.probe(src)
    d = info.duration
    if o == "trim":
        s, e = float(g("start", 0)), float(g("end") or d)
        if e <= s:
            raise ValueError("trim: fim <= início")
        ff.run(["-ss", f"{s:.4f}", "-to", f"{e:.4f}", "-i", str(src), *ff.H264, *_a(info), str(out)])
    elif o == "cut":
        s, e = float(g("start", 0)), float(g("end", d))
        if e <= s:
            raise ValueError("cut: fim <= início")
        if s <= 0.001:
            return apply(src, {"op": "trim", "start": e}, out)
        if e >= d - 0.001:
            return apply(src, {"op": "trim", "start": 0, "end": s}, out)
        v = f"[0:v]trim=0:{s},setpts=PTS-STARTPTS[v0];[0:v]trim={e},setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[v]"
        maps = ["-map", "[v]"]
        if info.has_audio:
            v += f";[0:a]atrim=0:{s},asetpts=PTS-STARTPTS[a0];[0:a]atrim={e},asetpts=PTS-STARTPTS[a1];[a0][a1]concat=n=2:v=0:a=1[a]"
            maps += ["-map", "[a]"]
        ff.run(["-i", str(src), "-filter_complex", v, *maps, *ff.H264, *_a(info), str(out)])
    elif o == "speed":
        f = float(g("factor", 1.0))
        if f <= 0:
            raise ValueError("speed: fator deve ser > 0")
        args = ["-i", str(src), "-filter:v", f"setpts=PTS/{f}"]
        if info.has_audio:
            args += ["-filter:a", _atempo(f)]
        ff.run(args + [*ff.H264, *_a(info), str(out)])
    elif o == "reverse":
        args = ["-i", str(src), "-vf", "reverse"] + (["-af", "areverse"] if info.has_audio else [])
        ff.run(args + [*ff.H264, *_a(info), str(out)])
    elif o == "loop":
        n = max(1, int(g("count", 2)))
        if g("mode", "repeat") == "pingpong":
            fc = "[0:v]split[a][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1:a=0[p]"
            if n > 1:
                fc += f";[p]loop=loop={n - 1}:size=32767:start=0[v]"
            else:
                fc = fc.replace("[p]", "[v]")
            ff.run(["-i", str(src), "-filter_complex", fc, "-map", "[v]", *ff.H264, "-an", str(out)])
        else:
            ff.run(["-stream_loop", str(n - 1), "-i", str(src), *ff.H264, *_a(info), str(out)])
    elif o == "fade":
        fi, fo = float(g("in_s", 0)), float(g("out_s", 0))
        vf, af = [], []
        if fi > 0:
            vf.append(f"fade=t=in:st=0:d={fi}")
            af.append(f"afade=t=in:st=0:d={fi}")
        if fo > 0:
            vf.append(f"fade=t=out:st={max(0, d - fo):.4f}:d={fo}")
            af.append(f"afade=t=out:st={max(0, d - fo):.4f}:d={fo}")
        args = ["-i", str(src), "-vf", ",".join(vf) or "null"]
        if info.has_audio and af:
            args += ["-af", ",".join(af)]
        ff.run(args + [*ff.H264, *_a(info), str(out)])
    elif o == "crop":
        x, y, w, h = (float(g(k, dv)) for k, dv in (("x", 0), ("y", 0), ("w", 1), ("h", 1)))
        vf = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=trunc(iw/2)*2:trunc(ih/2)*2"
        ff.run(["-i", str(src), "-vf", vf, *ff.H264, *_a(info), str(out)])
    elif o == "resize":
        w, h = int(g("width", -2) or -2), int(g("height", -2) or -2)
        vf = f"scale={w}:{h}:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2"
        ff.run(["-i", str(src), "-vf", vf, *ff.H264, *_a(info), str(out)])
    elif o == "fps":
        fps = float(g("fps", 24))
        vf = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1" if g("smooth", False) else f"fps={fps}"
        ff.run(["-i", str(src), "-vf", vf, *ff.H264, *_a(info), str(out)])
    elif o == "color":
        eq = f"eq=brightness={float(g('brightness', 0))}:contrast={float(g('contrast', 1))}:saturation={float(g('saturation', 1))}:gamma={float(g('gamma', 1))}"
        ff.run(["-i", str(src), "-vf", eq, *ff.H264, *_a(info), str(out)])
    elif o == "mute":
        ff.run(["-i", str(src), "-c:v", "copy", "-an", str(out)])
    elif o == "audio":
        apath = Path(g("path"))
        off, vol, mode = float(g("offset", 0)), float(g("volume", 1)), g("mode", "replace")
        delay = int(off * 1000)
        a1 = f"[1:a]adelay={delay}|{delay},volume={vol}[a1]"
        if mode == "mix" and info.has_audio:
            fc = f"{a1};[0:a][a1]amix=inputs=2:duration=first:dropout_transition=0[a]"
        else:
            fc = f"{a1};[a1]apad[a]"
        ff.run(["-i", str(src), "-i", str(apath), "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
    elif o == "concat":
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            other = ff.probe(Path(g("path")))
            b = _matched(Path(g("path")), info, td, "b.mp4")
            trans, dur = g("transition", "none"), float(g("duration", 0.5))
            audio = info.has_audio and other.has_audio
            if trans == "none":
                fc = "[0:v][1:v]concat=n=2:v=1:a=0[v]"
                if audio:
                    fc += ";[0:a][1:a]concat=n=2:v=0:a=1[a]"
            else:
                xt = {"fade": "fade", "dissolve": "dissolve", "wipeleft": "wipeleft", "slideleft": "slideleft"}.get(trans, "fade")
                dur = min(dur, d / 2, other.duration / 2)
                fc = f"[0:v]settb=AVTB,fps={info.fps}[x0];[1:v]settb=AVTB,fps={info.fps}[x1];[x0][x1]xfade=transition={xt}:duration={dur}:offset={d - dur:.4f}[v]"
                if audio:
                    fc += f";[0:a][1:a]acrossfade=d={dur}[a]"
            maps = ["-map", "[v]"] + (["-map", "[a]"] if audio else [])
            ff.run(["-i", str(src), "-i", str(b), "-filter_complex", fc, *maps, *ff.H264,
                    *(["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]), str(out)])
    elif o == "splice":
        s, e = float(g("start", 0)), float(g("end", d))
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            clip = _matched(Path(g("path")), info, td, "clip.mp4")
            parts = []
            if s > 0.01:
                apply(src, {"op": "trim", "start": 0, "end": s}, td / "a.mp4")
                parts.append(td / "a.mp4")
            parts.append(clip)
            if e < d - 0.01:
                apply(src, {"op": "trim", "start": e}, td / "c.mp4")
                parts.append(td / "c.mp4")
            inputs, chain = [], ""
            for i, p_ in enumerate(parts):
                inputs += ["-i", str(p_)]
                chain += f"[{i}:v]"
            fc = f"{chain}concat=n={len(parts)}:v=1:a=0[v]"
            maps = ["-map", "[v]"]
            if info.has_audio:  # mantém a trilha original inteira
                inputs += ["-i", str(src)]
                maps += ["-map", f"{len(parts)}:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
            ff.run([*inputs, "-filter_complex", fc, *maps, *ff.H264, *([] if info.has_audio else ["-an"]), str(out)])
    else:
        raise ValueError(f"operação desconhecida: {o}")
    return out


def apply_all(src: Path, ops: list[dict[str, Any]], out: Path, on_step=None) -> Path:
    with tempfile.TemporaryDirectory() as tds:
        cur = src
        for i, op in enumerate(ops):
            if on_step:
                on_step(i, op)
            nxt = Path(tds) / f"step_{i}.mp4"
            apply(cur, op, nxt)
            cur = nxt
        shutil.copyfile(cur, out)
    return out

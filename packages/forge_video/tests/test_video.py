from pathlib import Path

import pytest

from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_video import edit, ff, modality
from forge_video.pipelines import shift_markers

try:
    ff.binary("ffmpeg")
    HAS_FF = True
except ff.FFmpegError:
    HAS_FF = False
pytestmark = pytest.mark.skipif(not HAS_FF, reason="sem ffmpeg")


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield
    set_registry(None)


def _clip(path: Path, seconds: float = 3.0, audio: bool = True, color: str = "testsrc") -> Path:
    args = ["-f", "lavfi", "-i", f"{color}=size=320x240:rate=24:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    ff.run(args + [*ff.H264, str(path)])
    return path


def test_ops_durations(tmp_path):
    src = _clip(tmp_path / "a.mp4")
    assert ff.probe(src).has_audio
    cases = [
        ({"op": "trim", "start": 0.5, "end": 2.0}, 1.5),
        ({"op": "cut", "start": 1.0, "end": 2.0}, 2.0),
        ({"op": "speed", "factor": 2.0}, 1.5),
        ({"op": "reverse"}, 3.0),
        ({"op": "loop", "count": 2}, 6.0),
        ({"op": "fade", "in_s": 0.5, "out_s": 0.5}, 3.0),
        ({"op": "fps", "fps": 12}, 3.0),
    ]
    for op, dur in cases:
        out = edit.apply(src, op, tmp_path / f"{op['op']}.mp4")
        assert ff.probe(out).duration == pytest.approx(dur, abs=0.12), op


def test_concat_transition_and_splice(tmp_path):
    a = _clip(tmp_path / "a.mp4", 2.0)
    b = _clip(tmp_path / "b.mp4", 2.0, color="smptebars")
    hard = edit.apply(a, {"op": "concat", "path": str(b), "transition": "none"}, tmp_path / "c1.mp4")
    assert ff.probe(hard).duration == pytest.approx(4.0, abs=0.12)
    soft = edit.apply(a, {"op": "concat", "path": str(b), "transition": "fade", "duration": 0.5}, tmp_path / "c2.mp4")
    assert ff.probe(soft).duration == pytest.approx(3.5, abs=0.15)
    piece = _clip(tmp_path / "p.mp4", 0.5, audio=False, color="smptebars")
    sp = edit.apply(a, {"op": "splice", "start": 0.5, "end": 1.0, "path": str(piece)}, tmp_path / "s.mp4")
    info = ff.probe(sp)
    assert info.duration == pytest.approx(2.0, abs=0.12) and info.has_audio


def test_markers_follow_edits():
    ms = [{"t": 0.5, "label": "a"}, {"t": 1.5, "label": "b"}, {"t": 2.5, "label": "c"}]
    out = shift_markers(ms, [{"op": "trim", "start": 1.0, "end": 3.0}, {"op": "speed", "factor": 2.0}])
    assert [(m["label"], m["t"]) for m in out] == [("b", 0.25), ("c", 0.75)]
    cut = shift_markers(ms, [{"op": "cut", "start": 1.0, "end": 2.0}])
    assert [(m["label"], m["t"]) for m in cut] == [("a", 0.5), ("c", 1.5)]


def test_import_edit_export(tmp_path):
    p = Project.create(tmp_path / "proj")
    av = p.store.import_file(_clip(tmp_path / "in.mp4"), slug="clipe")
    assert (av.modality, av.kind) == ("video", "video")
    ed = run_pipeline(p, "video.edit", {"input_asset": "clipe", "trim_start": 1.0, "fade_out": 0.3,
                                        "markers": [{"t": 0.5, "label": "impacto"}]})
    assert ed.version == 2 and ed.extra["markers"][0]["label"] == "impacto"
    assert ed.extra["video"]["duration"] == pytest.approx(2.0, abs=0.12)
    out = tmp_path / "x"
    out.mkdir()
    files = run_export(p, "clipe", "video.file", {"format": "sheet", "fps": 4}, out)
    assert any(f.name.endswith("_sheet.png") for f in files)
    gif = run_export(p, "clipe", "video.file", {"format": "gif", "width": 160}, out)
    assert gif[0].stat().st_size > 1000

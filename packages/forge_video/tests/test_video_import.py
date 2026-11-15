import pytest

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_video import ff, modality

try:
    ff.binary("ffmpeg")
    HAS_FF = True
except ff.FFmpegError:
    HAS_FF = False


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


@pytest.mark.skipif(not HAS_FF, reason="sem ffmpeg")
def test_import_video_normalizes(project, tmp_path):
    src = tmp_path / "clip.mp4"
    ff.run(["-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1", "-pix_fmt", "yuv420p", str(src)])
    av = project.store.import_file(src)
    assert (av.modality, av.kind) == ("video", "video")
    assert {f.path for f in av.files} >= {"video.mp4", "poster.png", "video.json"}
    assert av.extra["video"]["width"] == 64


def test_import_video_without_ffmpeg_is_a_clear_error(project, tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "probe", lambda p: (_ for _ in ()).throw(ff.FFmpegError("ffprobe ausente")))
    p = tmp_path / "x.mp4"
    p.write_bytes(b"\0" * 64)
    with pytest.raises(ValueError, match="ffmpeg"):
        project.store.import_file(p)
    assert not (project.store.root / "x").exists()

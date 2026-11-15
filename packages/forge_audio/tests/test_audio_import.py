import numpy as np
import pytest
import soundfile as sf

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_audio import dsp, modality


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


def _tone(path, seconds=1.0, sr=22050, fmt=None, subtype=None):
    t = np.arange(int(seconds * sr)) / sr
    sf.write(str(path), (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sr, format=fmt, subtype=subtype)


@pytest.mark.parametrize("name,fmt,sub", [("a.wav", "WAV", "PCM_16"), ("a.flac", "FLAC", None), ("a.ogg", "OGG", "VORBIS")])
def test_import_audio_formats(project, tmp_path, name, fmt, sub):
    p = tmp_path / name
    _tone(p, fmt=fmt, subtype=sub)
    av = project.store.import_file(p)
    assert (av.modality, av.kind) == ("audio", "sfx")
    assert {f.path for f in av.files} >= {"audio.wav", "waveform.png", "spectrogram.png"}
    assert av.extra["audio"]["duration"] == pytest.approx(1.0, abs=0.05)
    a = dsp.load(project.store.files_of(av, "primary")[0])
    assert a.sr == 22050 and a.frames == pytest.approx(22050, abs=64)


def test_import_audio_options_and_long_is_music(project, tmp_path):
    p = tmp_path / "long.wav"
    _tone(p, seconds=21.0, sr=8000)
    av = project.store.import_file(p, options={"sample_rate": 16000, "mono": True})
    assert av.kind == "music"
    assert dsp.load(project.store.files_of(av, "primary")[0]).sr == 16000
    with pytest.raises(Exception):
        project.store.import_file(p, options={"bogus": 1})


def test_import_audio_corrupt(project, tmp_path):
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"not audio at all" * 20)
    with pytest.raises(ValueError, match="decodificar"):
        project.store.import_file(p)
    assert not (project.store.root / "bad").exists()

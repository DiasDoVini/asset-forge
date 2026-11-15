import numpy as np
import pytest

from forge_core.export import run_export
from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline
from forge_audio import dsp, modality, synth
from forge_audio.dsp import Audio


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(modality)
    set_registry(r)
    yield r
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def tone(freq=440.0, seconds=1.0, sr=44100, amp=0.5, channels=1) -> Audio:
    t = np.arange(int(seconds * sr)) / sr
    y = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return Audio(np.repeat(y[:, None], channels, axis=1), sr)


def test_loudness_of_full_scale_sine():
    # BS.1770: seno de 1 kHz a 0 dBFS em um canal ≈ -3.01 LUFS
    assert dsp.loudness(tone(1000, 3.0, amp=1.0)) == pytest.approx(-3.01, abs=0.1)


def test_normalize_loudness_hits_target():
    a = dsp.normalize_loudness(tone(500, 3.0, amp=0.05), -16.0)
    assert dsp.loudness(a) == pytest.approx(-16.0, abs=0.3)


def test_trim_fade_reverse_and_cut():
    a = dsp.pad(tone(seconds=1.0), 0.5, 0.5)
    t = dsp.trim_silence(a, -40, 0)
    assert t.duration == pytest.approx(1.0, abs=0.01)
    f = dsp.fade(t, 100, 100)
    assert abs(f.data[0, 0]) < 1e-3 and abs(f.data[-1, 0]) < 1e-3
    assert dsp.cut(t, 0.25, 0.5).duration == pytest.approx(0.75, abs=0.01)
    assert np.allclose(dsp.reverse(dsp.reverse(t)).data, t.data)


def test_pitch_shift_keeps_duration_and_moves_frequency():
    a = tone(440, 1.0)
    b = dsp.pitch_shift(a, 12)
    assert b.frames == a.frames
    spec = np.abs(np.fft.rfft(b.data[4096:4096 + 16384, 0]))
    peak_hz = np.argmax(spec) * a.sr / 16384
    assert peak_hz == pytest.approx(880, rel=0.05)


def test_make_loop_has_smooth_seam():
    rng = np.random.default_rng(0)
    noise = Audio((rng.standard_normal((44100 * 2, 1)) * 0.2).astype(np.float32), 44100)
    loop = dsp.make_loop(dsp.lowpass(noise, 800), 200)
    assert loop.duration == pytest.approx(1.8, abs=0.01)
    assert dsp.loop_seam_score(loop) > 0.7


def test_loop_to_bars_length():
    a = tone(220, 10.0)
    looped = dsp.loop_to_bars(a, 120, 4)  # compasso = 2 s → 4 compassos (8 s)
    assert looped.duration == pytest.approx(8.0, abs=0.01)


def test_sfxr_presets_render():
    for name in synth.PRESETS:
        a = synth.render(synth.preset(name, 1))
        assert a.frames > 100 and np.max(np.abs(a.data)) > 0.01, name


def test_retro_pipeline_with_variations_and_export(project, tmp_path):
    av = run_pipeline(project, "audio.retro", {"preset": "coin", "seed": 5, "variations": 3})
    assert av.kind == "sfx" and av.extra["variations"] == 3
    roles = sorted(f.role for f in av.files)
    assert roles == ["part", "part", "preview", "primary", "report"]
    out = tmp_path / "out"
    out.mkdir()
    files = run_export(project, f"{av.slug}@v{av.version:03d}", "audio.file", {"format": "ogg"}, out)
    names = sorted(f.name for f in files)
    assert [n for n in names if n.endswith(".ogg")] == [f"{av.slug}_0{i}.ogg" for i in (1, 2, 3)]
    assert dsp.load(out / f"{av.slug}_01.ogg").frames > 0


def test_edit_pipeline_new_version(project):
    av = run_pipeline(project, "audio.retro", {"preset": "explosion", "seed": 2})
    ed = run_pipeline(project, "audio.edit", {"input_asset": av.slug, "pitch_semitones": -5, "reverb": 0.3,
                                              "fade_out_ms": 50, "normalize": "peak", "target": -3})
    assert ed.slug == av.slug and ed.version == 2
    assert ed.extra["preset"] == "explosion"
    assert [o["op"] for o in ed.extra["ops"]] == ["pitch", "reverb", "fade", "normalize"]
    assert ed.extra["audio"]["peak_db"] == pytest.approx(-3.0, abs=0.1)


def test_edit_use_variation_and_ops(project):
    av = run_pipeline(project, "audio.retro", {"preset": "laser", "seed": 9, "variations": 2})
    ed = run_pipeline(project, "audio.edit", {"input_asset": av.slug, "ops": [{"op": "use_variation", "index": 2},
                                                                             {"op": "loop", "crossfade_ms": 20}]})
    assert ed.extra["loop"] is True


def test_mix_layers(project):
    a = run_pipeline(project, "audio.retro", {"preset": "hit", "seed": 1}, asset="hit")
    b = run_pipeline(project, "audio.retro", {"preset": "explosion", "seed": 1}, asset="boom")
    m = run_pipeline(project, "audio.mix", {"input_asset": "hit", "layers": [{"asset": "boom", "offset": 0.05, "gain_db": -3}]})
    assert m.slug != a.slug and m.extra["audio"]["duration"] >= b.extra["audio"]["duration"]

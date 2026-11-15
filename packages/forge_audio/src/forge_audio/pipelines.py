"""Pipelines de áudio.

Bloco "audio" do project.yaml (todos opcionais):
    audio:
      sound: "gritty dark fantasy, medieval"      # estilo sonoro anexado aos prompts de SFX
      music: "orchestral, choir, low strings"     # estilo anexado aos prompts de música
      sample_rate: 48000                          # taxa do arquivo final
      sfx_lufs: -16                               # alvo de loudness (SFX / música)
      music_lufs: -14
      models: {sa3_sfx: ..., ace_unet: ...}       # sobrescreve graphs.DEFAULT_MODELS

Cada versão grava: audio.wav (primary), waveform.png (preview), spectrogram.png (report) e,
quando há variações, var_02.wav… (part). Estatísticas (duração, LUFS, pico) ficam em extra.audio.
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge_core import ComfyError, OutputFile, Pipeline, PipelineParams
from forge_core.pipeline import Context
from forge_core.runner import resolve_input

from . import dsp, graphs, synth, visuals
from .dsp import Audio

SfxType = Literal["sfx", "impact", "whoosh", "ui", "foley", "magic", "creature", "weapon", "explosion", "ambience", "voice_fx"]

_SFX_HINTS: dict[str, str] = {
    "sfx": "sound effect, clean, isolated, no music",
    "impact": "single impact sound effect, punchy transient, short tail, isolated, no music",
    "whoosh": "fast whoosh swish sound effect, air movement, isolated, no music",
    "ui": "short clean user interface sound, crisp, game menu, isolated, no music",
    "foley": "foley sound effect, realistic, close mic, isolated, no music",
    "magic": "magical spell sound effect, shimmering, sparkles, fantasy, isolated, no music",
    "creature": "creature vocalization sound effect, animal or monster, isolated, no music",
    "weapon": "weapon sound effect, sharp attack, isolated, no music",
    "explosion": "explosion sound effect, deep boom, debris, rumble tail, isolated, no music",
    "ambience": "ambient soundscape, continuous background atmosphere, seamless, no music",
    "voice_fx": "vocal effort sound, grunt or shout, no words, isolated, no music",
}
NEGATIVE = "music, melody, speech, talking, low quality, distorted, clipping, noise hiss"
KEYS = ["C major", "C# major", "D major", "Eb major", "E major", "F major", "F# major", "G major", "Ab major", "A major",
        "Bb major", "B major", "C minor", "C# minor", "D minor", "Eb minor", "E minor", "F minor", "F# minor", "G minor",
        "G# minor", "A minor", "Bb minor", "B minor"]


# ---------------------------------------------------------------- helpers

def _models(style: dict[str, Any]) -> dict[str, str]:
    m = dict(graphs.DEFAULT_MODELS)
    m.update(style.get("models") or {})
    return m


def _seed(seed: int | None) -> int:
    return seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)


def _ensure_comfy(ctx: Context) -> None:
    """ComfyUI configurado se ele enxergar os modelos; senão outra instância do Asset Forge no ar."""
    from forge_core.comfy_select import use_instance

    use_instance(ctx, what="o áudio")


def require_models(ctx: Context, files: list[str]) -> None:
    missing = []
    for f in files:
        folder = graphs.MODEL_SOURCES.get(f, ("checkpoints", ""))[0]
        if f not in ctx.comfy.models(folder):
            missing.append(f)
    if missing:
        urls = "; ".join(f"{m} → {graphs.MODEL_SOURCES[m][0]}/ ({graphs.MODEL_SOURCES[m][1]})" for m in missing if m in graphs.MODEL_SOURCES)
        raise ComfyError(f"modelos de áudio ausentes no ComfyUI: {missing}. Baixe com "
                         f"`python scripts/download_models.py audio` ou manualmente: {urls}")


def _audio_of(ctx: Context, ref: str | None = None) -> Audio:
    if ref is None:
        files = ctx.input_files("primary") or ctx.input_files()
        if not files:
            raise ValueError("asset de entrada sem arquivo")
        return dsp.load(files[0])
    av, path = resolve_input(ctx.project, ref)
    if av is not None:
        path = (ctx.project.store.files_of(av, "primary") or ctx.project.store.files_of(av))[0]
    return dsp.load(path)


def _upload_audio(ctx: Context, a: Audio, name: str = "src.wav") -> str:
    with tempfile.TemporaryDirectory() as td:
        p = dsp.save(a, Path(td) / name, subtype="PCM_16")
        return ctx.comfy.upload_image(p)


def _run_audio(ctx: Context, graph: graphs.Graph, node: str, label: str, span=(0.05, 0.85)) -> list[Audio]:
    ctx.check_cancelled()
    a, b = span
    ctx.progress(a, label)
    outs = ctx.comfy.run_workflow(graph.to_dict(), on_progress=lambda v, m: ctx.progress(a + (b - a) * v, label))
    res = []
    with tempfile.TemporaryDirectory() as td:
        for i, o in enumerate(x for x in outs if x.node_id == node):
            p = Path(td) / f"{i}{Path(o.filename).suffix or '.flac'}"
            p.write_bytes(ctx.comfy.download(o.filename, o.subfolder, o.type))
            res.append(dsp.load(p))
    if not res:
        raise ComfyError("o workflow não produziu áudio")
    ctx.progress(b, "pós-processando")
    return res


def finish(ctx: Context, takes: list[Audio], *, kind: str, extra: dict[str, Any] | None = None,
           sample_rate: int | None = None) -> list[OutputFile]:
    """Grava a(s) tomada(s): a 1ª é o primary; as demais viram variações (part)."""
    d = ctx.version_dir
    sr = sample_rate or int(ctx.style().get("sample_rate") or 0) or None
    files: list[OutputFile] = []
    stats = None
    for i, a in enumerate(takes):
        if sr and a.sr != sr:
            a = dsp.resample(a, sr)
        name = "audio.wav" if i == 0 else f"var_{i + 1:02d}.wav"
        dsp.save(a, d / name, subtype="PCM_24")
        files.append(OutputFile(d / name, "primary" if i == 0 else "part", "audio/wav"))
        if i == 0:
            stats = dsp.analyze(a)
            visuals.waveform(a).save(d / "waveform.png")
            visuals.spectrogram(a).save(d / "spectrogram.png")
            files += [OutputFile(d / "waveform.png", "preview"), OutputFile(d / "spectrogram.png", "report")]
    ctx.meta["kind"] = kind
    ctx.meta.setdefault("extra", {}).update({**(extra or {}), "audio": stats, "variations": len(takes)})
    return files


def _post(a: Audio, *, trim: bool, fade_out_ms: float, lufs: float | None, mono: bool, loop: bool,
          loop_ms: float = 400.0) -> Audio:
    if mono:
        a = dsp.to_mono(a)
    if trim:
        a = dsp.trim_silence(a, -50.0, 10.0)
    if loop:
        a = dsp.make_loop(a, loop_ms)
    elif fade_out_ms > 0:
        a = dsp.fade(a, 2.0, fade_out_ms)
    if lufs is not None:
        a = dsp.normalize_loudness(a, lufs, -1.0)
    return a


class _PostParams(PipelineParams):
    seed: int | None = Field(None, description="semente (vazio = aleatória)")
    normalize: bool = Field(True, description="normalizar loudness (alvo do projeto: audio.sfx_lufs / music_lufs)")
    mono: bool | None = Field(None, description="mono (vazio = mono para SFX curtos, estéreo para o resto)")


# ---------------------------------------------------------------- Text → SFX

class SfxParams(_PostParams):
    prompt: str = Field(..., description='o som, em inglês de preferência: "heavy wooden door creaking open slowly"')
    sound_type: SfxType = Field("sfx", description="tipo do som (ajusta o prompt e o pós-processamento)")
    duration: float = Field(2.0, ge=0.3, le=120.0, description="segundos")
    variations: int = Field(1, ge=1, le=8, description="tomadas diferentes do mesmo som (ficam como variações)")
    quality: Literal["fast", "high"] = Field("fast", description="fast = Stable Audio 3 small SFX; high = medium (mais lento)")
    loop: bool | None = Field(None, description="fechar em loop sem emenda (vazio = sim para ambience)")
    trim_silence: bool = Field(True, description="cortar silêncio do início/fim (não se aplica a loops)")
    fade_out_ms: float = Field(30.0, ge=0, le=5000)
    use_style: bool = Field(True, description="anexar o estilo sonoro do projeto (audio.sound)")
    steps: int = Field(8, ge=1, le=100)


class TextToSfx(Pipeline):
    id = "audio.sfx"
    title = "Text → SFX"
    description = ("Efeito sonoro a partir de texto (Stable Audio 3): impactos, passos, magias, UI, criaturas, "
                   "explosões, ambiências em loop. Gera várias variações de uma vez.")
    resources = {"gpu"}
    kind = "sfx"
    Params = SfxParams

    def run(self, ctx: Context, p: SfxParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        key = "sa3_sfx" if p.quality == "fast" else "sa3_medium"
        parts = [p.prompt.strip(), _SFX_HINTS[p.sound_type]]
        if p.use_style and style.get("sound"):
            parts.append(str(style["sound"]))
        full = ", ".join(x for x in parts if x)
        loop = p.loop if p.loop is not None else p.sound_type == "ambience"
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        require_models(ctx, [models[key], models["sa3_clip"]])
        ctx.log(f"prompt: {full}")
        # gera um pouco mais longo para o corte de silêncio / crossfade do loop
        gen_seconds = p.duration + (0.5 if loop else 0.25)
        g, node = graphs.stable_audio(full, NEGATIVE, gen_seconds, seed, models=models, model_key=key,
                                      steps=p.steps, batch=p.variations)
        takes = _run_audio(ctx, g, node, "gerando som")
        mono = p.mono if p.mono is not None else (p.duration <= 4 and p.sound_type != "ambience")
        lufs = float(style.get("sfx_lufs", -16)) if p.normalize else None
        takes = [_post(t, trim=p.trim_silence and not loop, fade_out_ms=p.fade_out_ms, lufs=lufs, mono=mono, loop=loop)
                 for t in takes]
        if loop:
            takes = [dsp.trim(t, 0, min(t.duration, p.duration)) if t.duration > p.duration + 0.5 else t for t in takes]
        ctx.meta.update(seed=seed, prompt=p.prompt)
        return finish(ctx, takes, kind="ambience" if p.sound_type == "ambience" else "sfx",
                      extra={"full_prompt": full, "model": models[key], "sound_type": p.sound_type, "loop": loop})


# ---------------------------------------------------------------- Text → Music

class MusicParams(_PostParams):
    prompt: str = Field(..., description='estilo/gênero/instrumentos/clima: "epic orchestral battle theme, taiko drums, brass"')
    lyrics: str = Field("", description='letra com tags [verse]/[chorus]; vazio = instrumental')
    duration: float = Field(60.0, ge=5.0, le=300.0, description="segundos")
    bpm: int | None = Field(None, ge=40, le=240, description="vazio = 120 (ou o que o prompt sugerir)")
    key: str | None = Field(None, description='tonalidade, ex. "D minor" (vazio = escolhida pela seed)')
    time_signature: Literal["2", "3", "4", "6"] = "4"
    language: str = Field("en", description="idioma da letra (en, pt, es, ja...)")
    loop: bool = Field(False, description="cortar em compassos inteiros e fechar em loop para música de fundo")
    use_style: bool = Field(True, description="anexar o estilo musical do projeto (audio.music)")
    steps: int = Field(8, ge=1, le=100)


class TextToMusic(Pipeline):
    id = "audio.music"
    title = "Text → Música"
    description = ("Música a partir de texto (ACE-Step 1.5): trilhas, temas de batalha, menus, jingles; "
                   "instrumental ou com letra; opção de loop em compassos inteiros.")
    resources = {"gpu"}
    kind = "music"
    Params = MusicParams

    def run(self, ctx: Context, p: MusicParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        seed = _seed(p.seed)
        tags = p.prompt.strip()
        if p.use_style and style.get("music"):
            tags = f"{tags}, {style['music']}"
        bpm = p.bpm or 120
        key = p.key if p.key in KEYS else random.Random(seed).choice(KEYS)
        lyrics = p.lyrics.strip() or "[Instrumental]"
        _ensure_comfy(ctx)
        require_models(ctx, [models[k] for k in ("ace_unet", "ace_clip1", "ace_clip2", "ace_vae")])
        extra_s = 4.0 if p.loop else 0.0
        ctx.log(f"tags: {tags} | {bpm} bpm, {key}, {p.time_signature}/4")
        g, node = graphs.ace_step15(tags, lyrics, p.duration + extra_s, seed, models=models, bpm=bpm, key=key,
                                    time_signature=p.time_signature, language=p.language, steps=p.steps)
        a = _run_audio(ctx, g, node, "compondo")[0]
        if p.mono:
            a = dsp.to_mono(a)
        if p.loop:
            a = dsp.loop_to_bars(a, bpm, int(p.time_signature), 40.0)
        else:
            a = dsp.fade(dsp.trim_silence(a, -60.0, 50.0), 5.0, 1500.0)
        if p.normalize:
            a = dsp.normalize_loudness(a, float(style.get("music_lufs", -14)), -1.0)
        ctx.meta.update(seed=seed, prompt=p.prompt)
        return finish(ctx, [a], kind="music", extra={"tags": tags, "lyrics": p.lyrics or None, "bpm": bpm, "key": key,
                                                     "time_signature": p.time_signature, "loop": p.loop,
                                                     "model": models["ace_unet"]})


# ---------------------------------------------------------------- Retro (sfxr)

class RetroParams(PipelineParams):
    preset: Literal["coin", "laser", "explosion", "powerup", "hit", "jump", "blip", "click", "magic", "step", "random"] = "coin"
    seed: int | None = Field(None, description="cada seed gera um som diferente do preset")
    variations: int = Field(1, ge=1, le=16, description="variações leves do mesmo som (ótimo para passos/tiros)")
    mutation: float = Field(0.3, ge=0.0, le=1.0, description="quanto as variações diferem entre si")
    length: float = Field(1.0, ge=0.25, le=4.0, description="multiplica a duração do envelope")
    params: dict[str, Any] = Field(default_factory=dict, description="sobrescreve parâmetros sfxr (wave, start_freq, slide, decay...)")
    sample_rate: Literal[22050, 44100, 48000] = 44100
    bits: Literal[8, 16] = Field(16, description="8 = mais crocante, estilo console antigo")
    gain_db: float = Field(-1.0, ge=-30, le=0, description="pico final (dBFS)")


class RetroSfx(Pipeline):
    id = "audio.retro"
    title = "Som retrô (sfxr)"
    description = ("Efeitos 8/16-bit procedurais estilo sfxr: moeda, laser, explosão, power-up, dano, pulo, blip. "
                   "Roda em CPU, instantâneo, sem modelos. Parâmetros editáveis para ajuste fino.")
    resources = {"cpu"}
    kind = "sfx"
    Params = RetroParams

    def default_slug(self, params: RetroParams) -> str:
        return f"{params.preset} sfx"

    def run(self, ctx: Context, p: RetroParams) -> list[OutputFile]:
        seed = _seed(p.seed)
        base = synth.preset(p.preset, seed)
        if p.params:
            base.update(**p.params)
        takes = []
        for i in range(p.variations):
            sp = base if i == 0 else synth.mutate(base, p.mutation, seed + i)
            a = synth.render(sp, p.sample_rate, seed + i, length=p.length)
            if p.bits == 8:
                a = dsp.bitcrush(a, 8)
            takes.append(dsp.fade(dsp.normalize_peak(a, p.gain_db), 0.0, 4.0))
            ctx.progress((i + 1) / p.variations, "sintetizando")
        ctx.meta.update(seed=seed, prompt=None)
        return finish(ctx, takes, kind="sfx", sample_rate=p.sample_rate,
                      extra={"preset": p.preset, "sfxr": base.to_dict(), "length": p.length, "bits": p.bits})


# ---------------------------------------------------------------- Variação (áudio → áudio)

class VariationParams(PipelineParams):
    prompt: str = Field("", description="como o resultado deve soar (vazio = mesmo prompt do original)")
    strength: float = Field(0.5, ge=0.05, le=0.95, description="0.2 = parecido; 0.8 = bem diferente")
    engine: Literal["auto", "sfx", "music"] = Field("auto", description="auto: música → ACE-Step, resto → Stable Audio")
    variations: int = Field(1, ge=1, le=8)
    seed: int | None = None
    normalize: bool = True


class AudioVariation(Pipeline):
    id = "audio.variation"
    title = "Variação / restyle de áudio"
    description = ("Gera variações de um som ou música existente (áudio → áudio), mantendo ritmo e forma; "
                   "com prompt muda timbre/estilo (ex.: 'same footstep but on snow').")
    resources = {"gpu"}
    requires_input = True
    Params = VariationParams

    def default_slug(self, params: VariationParams) -> str:
        return Path((params.input_asset or "audio").split("@")[0]).stem + "-var"

    def run(self, ctx: Context, p: VariationParams) -> list[OutputFile]:
        style = ctx.style()
        models = _models(style)
        src = _audio_of(ctx)
        inp = ctx.input
        is_music = (p.engine == "music") or (p.engine == "auto" and inp is not None and inp.kind == "music")
        prompt = p.prompt or (inp.prompt if inp and inp.prompt else "")
        seed = _seed(p.seed)
        _ensure_comfy(ctx)
        if is_music:
            require_models(ctx, [models[k] for k in ("ace_unet", "ace_clip1", "ace_clip2", "ace_vae")])
            ex = (inp.extra if inp else {}) or {}
            takes = []
            for i in range(p.variations):
                g, node = graphs.ace_step15(prompt or "instrumental", ex.get("lyrics") or "[Instrumental]", src.duration,
                                            seed + i, models=models, bpm=int(ex.get("bpm") or 120),
                                            key=ex.get("key") or "C major", source_audio=_upload_audio(ctx, src),
                                            denoise=p.strength)
                takes += _run_audio(ctx, g, node, f"variação {i + 1}")
        else:
            require_models(ctx, [models["sa3_sfx"], models["sa3_clip"]])
            g, node = graphs.stable_audio(prompt or "sound effect", NEGATIVE, src.duration, seed, models=models,
                                          batch=p.variations, source_audio=_upload_audio(ctx, src), denoise=p.strength)
            takes = _run_audio(ctx, g, node, "gerando variações")
        takes = [dsp.match(src, t) for t in takes]
        takes = [dsp.trim(t, 0, min(t.duration, src.duration)) for t in takes]
        if p.normalize:
            target = float(style.get("music_lufs" if is_music else "sfx_lufs", -14 if is_music else -16))
            takes = [dsp.normalize_loudness(t, target) for t in takes]
        ctx.meta.update(seed=seed, prompt=prompt or None)
        return finish(ctx, takes, kind=inp.kind if inp else "sfx",
                      extra={"strength": p.strength, "engine": "ace-step" if is_music else "stable-audio"})


# ---------------------------------------------------------------- Edição

class Op(BaseModel):
    """Operação de edição. Campos usados dependem de `op`."""

    model_config = ConfigDict(extra="allow")
    op: Literal["trim", "cut", "silence_trim", "fade", "gain", "normalize", "loudness", "reverse", "speed", "pitch",
                "stretch", "lowpass", "highpass", "bandpass", "eq", "reverb", "echo", "distortion", "bitcrush",
                "tremolo", "compress", "loop", "loop_bars", "pad", "channels", "sample_rate", "use_variation"]


OPS_HELP = (
    "trim{start,end} · cut{start,end} · silence_trim{threshold_db} · fade{in_ms,out_ms} · gain{db} · "
    "normalize{peak_db} · loudness{lufs} · reverse · speed{factor} · pitch{semitones} · stretch{factor} · "
    "lowpass{hz} · highpass{hz} · bandpass{lo,hi} · eq{low_db,mid_db,high_db} · reverb{mix,room,damping} · "
    "echo{delay_ms,feedback,mix} · distortion{drive} · bitcrush{bits,sample_rate} · tremolo{rate_hz,depth} · "
    "compress{threshold_db,ratio} · loop{crossfade_ms} · loop_bars{bpm,beats} · pad{before,after} · "
    "channels{mode: mono|stereo} · sample_rate{hz} · use_variation{index}"
)


def apply_op(a: Audio, op: dict[str, Any], variations: list[Path] | None = None) -> Audio:
    o = op["op"]
    g = op.get
    if o == "trim":
        return dsp.trim(a, float(g("start", 0)), g("end"))
    if o == "cut":
        return dsp.cut(a, float(g("start", 0)), float(g("end", a.duration)))
    if o == "silence_trim":
        return dsp.trim_silence(a, float(g("threshold_db", -50)), float(g("keep_ms", 10)))
    if o == "fade":
        return dsp.fade(a, float(g("in_ms", 0)), float(g("out_ms", 0)), g("shape", "equal_power"))
    if o == "gain":
        return dsp.gain(a, float(g("db", 0)))
    if o == "normalize":
        return dsp.normalize_peak(a, float(g("peak_db", -1)))
    if o == "loudness":
        return dsp.normalize_loudness(a, float(g("lufs", -16)))
    if o == "reverse":
        return dsp.reverse(a)
    if o == "speed":
        return dsp.speed(a, float(g("factor", 1)))
    if o == "pitch":
        return dsp.pitch_shift(a, float(g("semitones", 0)))
    if o == "stretch":
        return dsp.time_stretch(a, float(g("factor", 1)))
    if o == "lowpass":
        return dsp.lowpass(a, float(g("hz", 8000)))
    if o == "highpass":
        return dsp.highpass(a, float(g("hz", 80)))
    if o == "bandpass":
        return dsp.bandpass(a, float(g("lo", 300)), float(g("hi", 3000)))
    if o == "eq":
        return dsp.eq3(a, float(g("low_db", 0)), float(g("mid_db", 0)), float(g("high_db", 0)))
    if o == "reverb":
        return dsp.reverb(a, float(g("mix", 0.3)), float(g("room", 0.5)), float(g("damping", 0.5)))
    if o == "echo":
        return dsp.echo(a, float(g("delay_ms", 250)), float(g("feedback", 0.4)), float(g("mix", 0.35)))
    if o == "distortion":
        return dsp.distortion(a, float(g("drive", 0.5)))
    if o == "bitcrush":
        return dsp.bitcrush(a, int(g("bits", 8)), g("sample_rate"))
    if o == "tremolo":
        return dsp.tremolo(a, float(g("rate_hz", 6)), float(g("depth", 0.5)))
    if o == "compress":
        return dsp.compress(a, float(g("threshold_db", -18)), float(g("ratio", 4)))
    if o == "loop":
        return dsp.make_loop(a, float(g("crossfade_ms", 250)))
    if o == "loop_bars":
        return dsp.loop_to_bars(a, float(g("bpm", 120)), int(g("beats", 4)))
    if o == "pad":
        return dsp.pad(a, float(g("before", 0)), float(g("after", 0)))
    if o == "channels":
        return dsp.set_channels(a, g("mode", "keep"))
    if o == "sample_rate":
        return dsp.resample(a, int(g("hz", a.sr)))
    if o == "use_variation":
        idx = int(g("index", 1))
        if idx <= 1:
            return a
        if not variations or idx - 2 >= len(variations):
            raise ValueError(f"variação {idx} não existe")
        return dsp.load(variations[idx - 2])
    raise ValueError(f"operação desconhecida: {o}")


class EditParams(PipelineParams):
    ops: list[Op] = Field(default_factory=list, description="operações em ordem (avançado): " + OPS_HELP)
    trim_start: float | None = Field(None, ge=0, description="segundos")
    trim_end: float | None = Field(None, ge=0, description="segundos (fim do trecho mantido)")
    trim_silence: bool = False
    pitch_semitones: float = Field(0.0, ge=-24, le=24, description="altura sem mudar a duração")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="velocidade + altura juntos (varispeed)")
    lowpass_hz: float | None = Field(None, ge=50, le=22000)
    highpass_hz: float | None = Field(None, ge=10, le=20000)
    reverb: float = Field(0.0, ge=0, le=1, description="quantidade de reverb (0 = nenhum)")
    room: float = Field(0.5, ge=0, le=1, description="tamanho da sala do reverb")
    echo_ms: float | None = Field(None, ge=20, le=2000, description="delay do eco (vazio = sem eco)")
    distortion: float = Field(0.0, ge=0, le=1)
    bitcrush_bits: int | None = Field(None, ge=2, le=16, description="som retrô: 8 bits, 4 bits...")
    reverse: bool = False
    fade_in_ms: float = Field(0.0, ge=0, le=20000)
    fade_out_ms: float = Field(0.0, ge=0, le=20000)
    make_loop: bool = Field(False, description="fechar em loop (crossfade fim → início)")
    loop_crossfade_ms: float = Field(250.0, ge=5, le=5000)
    normalize: Literal["none", "peak", "loudness"] = "none"
    target: float = Field(-16.0, ge=-40, le=0, description="alvo: dBFS de pico ou LUFS")
    channels: Literal["keep", "mono", "stereo"] = "keep"


def edit_ops(p: EditParams) -> list[dict[str, Any]]:
    """Converte os campos simples em operações (na ordem que soa natural) depois de `ops`."""
    ops = [o.model_dump() for o in p.ops]
    if p.trim_start is not None or p.trim_end is not None:
        ops.append({"op": "trim", "start": p.trim_start or 0.0, "end": p.trim_end})
    if p.trim_silence:
        ops.append({"op": "silence_trim"})
    if p.reverse:
        ops.append({"op": "reverse"})
    if p.speed != 1.0:
        ops.append({"op": "speed", "factor": p.speed})
    if p.pitch_semitones:
        ops.append({"op": "pitch", "semitones": p.pitch_semitones})
    if p.highpass_hz:
        ops.append({"op": "highpass", "hz": p.highpass_hz})
    if p.lowpass_hz:
        ops.append({"op": "lowpass", "hz": p.lowpass_hz})
    if p.distortion:
        ops.append({"op": "distortion", "drive": p.distortion})
    if p.bitcrush_bits:
        ops.append({"op": "bitcrush", "bits": p.bitcrush_bits})
    if p.echo_ms:
        ops.append({"op": "echo", "delay_ms": p.echo_ms})
    if p.reverb:
        ops.append({"op": "reverb", "mix": p.reverb, "room": p.room})
    if p.make_loop:
        ops.append({"op": "loop", "crossfade_ms": p.loop_crossfade_ms})
    if p.fade_in_ms or p.fade_out_ms:
        ops.append({"op": "fade", "in_ms": p.fade_in_ms, "out_ms": p.fade_out_ms})
    if p.channels != "keep":
        ops.append({"op": "channels", "mode": p.channels})
    if p.normalize == "peak":
        ops.append({"op": "normalize", "peak_db": min(0.0, p.target)})
    elif p.normalize == "loudness":
        ops.append({"op": "loudness", "lufs": p.target})
    return ops


class AudioEdit(Pipeline):
    id = "audio.edit"
    title = "Editar áudio"
    description = ("Corta, faz fade, normaliza, muda altura/velocidade, filtra, reverb, eco, distorção, bitcrush, "
                   "inverte e fecha loops. Grava nova versão (CPU, instantâneo).")
    resources = {"cpu"}
    requires_input = True
    same_asset = True
    Params = EditParams

    def run(self, ctx: Context, p: EditParams) -> list[OutputFile]:
        a = _audio_of(ctx)
        ops = edit_ops(p)
        if not ops:
            raise ValueError("nenhuma edição pedida")
        variations = ctx.input_files("part")
        for i, op in enumerate(ops):
            ctx.check_cancelled()
            ctx.progress(i / len(ops), op["op"])
            a = apply_op(a, op, variations)
        inp = ctx.input
        extra = {k: v for k, v in ((inp.extra if inp else {}) or {}).items() if k not in ("audio", "variations", "ops")}
        extra["ops"] = ops
        if any(o["op"] in ("loop", "loop_bars") for o in ops):
            extra["loop"] = True
        return finish(ctx, [a], kind=inp.kind if inp else "audio", sample_rate=a.sr, extra=extra)


# ---------------------------------------------------------------- Mixagem em camadas

class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset: str = Field(..., description="slug, slug@v003 ou arquivo")
    offset: float = Field(0.0, ge=0, description="segundos a partir do início")
    gain_db: float = Field(0.0, ge=-60, le=24)
    pan: float = Field(0.0, ge=-1, le=1)


class MixParams(PipelineParams):
    layers: list[Layer] = Field(..., min_length=1, description="camadas somadas sobre o asset de entrada (ou sobre silêncio)")
    base_gain_db: float = Field(0.0, ge=-60, le=24)
    normalize: Literal["none", "peak", "loudness"] = "peak"
    target: float = Field(-1.0, ge=-40, le=0)


class AudioMix(Pipeline):
    id = "audio.mix"
    title = "Mixar camadas"
    description = ("Combina vários sons num só (ex.: impacto + whoosh + debris = golpe completo; sequências com "
                   "offset). A entrada é a camada base (opcional).")
    resources = {"cpu"}
    accepts_input = True
    Params = MixParams

    def default_slug(self, params: MixParams) -> str:
        return "mix " + Path(params.layers[0].asset.split("@")[0]).stem

    def run(self, ctx: Context, p: MixParams) -> list[OutputFile]:
        if ctx.input is not None or ctx.input_path is not None:
            base = dsp.gain(_audio_of(ctx), p.base_gain_db)
        else:
            first = _audio_of(ctx, p.layers[0].asset)
            base = dsp.silence(0.01, first.sr, 2)
        for i, ly in enumerate(p.layers):
            ctx.progress(i / len(p.layers), f"camada {i + 1}")
            base = dsp.mix(base, _audio_of(ctx, ly.asset), ly.offset, ly.gain_db, ly.pan)
        if p.normalize == "peak":
            base = dsp.normalize_peak(base, p.target)
        elif p.normalize == "loudness":
            base = dsp.normalize_loudness(base, p.target)
        return finish(ctx, [base], kind="sfx", sample_rate=base.sr,
                      extra={"layers": [ly.model_dump() for ly in p.layers]})


PIPELINES: list[Pipeline] = [TextToSfx(), TextToMusic(), RetroSfx(), AudioVariation(), AudioEdit(), AudioMix()]

"""Catálogo do que o Asset Forge precisa para funcionar, para a tela de Instalação: ferramentas (Ollama, ffmpeg, git,
uv), contas (Hugging Face, Claude), instâncias do ComfyUI, pacotes de modelos por recurso e LLMs do Ollama.

Cada item sabe dizer o próprio estado (`status`) e, quando dá, instalar-se (`install`, uma tarefa de `setup_tasks`).
As URLs dos modelos foram conferidas contra os arquivos em uso (mesmo tamanho em bytes); o estado compara o tamanho do
arquivo local com o esperado, então um download interrompido sem `.part` aparece como incompleto.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import comfy_supervisor as cs
from .setup_tasks import TaskManager, hf_token

ROOT = cs.ROOT
HF = "https://huggingface.co"


# ---------------------------------------------------------------- pasta de modelos

def models_dir() -> Path:
    """Pasta compartilhada de modelos do ComfyUI: FORGE_MODELS_DIR, a usada pela instância 3D
    (extra_model_paths.yaml do comfy/install.ps1), a do Comfy Desktop, ou o padrão do Comfy Desktop."""
    for env in ("FORGE_MODELS_DIR", "FORGE_COMFY_MODELS_DIR"):
        if os.environ.get(env):
            return Path(os.environ[env])
    for yaml in (ROOT / "comfy/instance/ComfyUI/extra_model_paths.yaml",
                 Path(os.environ.get("APPDATA", "")) / "Comfy Desktop" / "shared_model_paths.yaml"):
        try:
            m = re.search(r"base_path:\s*['\"]?([^'\"\r\n]+)", yaml.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if m and Path(m.group(1).strip()).is_dir():
            return Path(m.group(1).strip())
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"


@dataclass
class ModelFile:
    folder: str
    name: str
    url: str
    size: int

    def path(self, root: Path) -> Path:
        return root / self.folder / self.name

    def state(self, root: Path) -> str:
        p = self.path(root)
        if p.exists():
            return "ok" if not self.size or p.stat().st_size == self.size else "mismatch"
        return "partial" if Path(str(p) + ".part").exists() else "missing"


def _f(folder: str, name: str, url: str, size: int) -> ModelFile:
    return ModelFile(folder, name, url, size)


_CO = f"{HF}/Comfy-Org"
ZIMAGE = [
    _f("diffusion_models", "z_image_turbo_bf16.safetensors", f"{_CO}/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors", 12309866400),
    _f("text_encoders", "qwen_3_4b.safetensors", f"{_CO}/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors", 8044982048),
    _f("vae", "ae.safetensors", f"{_CO}/z_image_turbo/resolve/main/split_files/vae/ae.safetensors", 335304388),
]
QWEN_EDIT = [
    _f("diffusion_models", "qwen_image_edit_2511_fp8mixed.safetensors", f"{_CO}/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors", 20533762817),
    _f("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors", f"{_CO}/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", 9384670680),
    _f("vae", "qwen_image_vae.safetensors", f"{_CO}/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", 253806246),
    _f("loras", "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", f"{HF}/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", 849608296),
]
_QI21 = f"{_CO}/Qwen-Image-2.1/resolve/main"
QI21_SHARED = [
    _f("text_encoders", "qwen3vl_8b_int8_convrot.safetensors", f"{_QI21}/text_encoders/qwen3vl_8b_int8_convrot.safetensors", 9350798360),
    _f("vae", "qwen_image_2.1_vae_bf16.safetensors", f"{_QI21}/vae/qwen_image_2.1_vae_bf16.safetensors", 675509688),
]
QI21_TURBO = [_f("diffusion_models", "qwen_image_2.1_turbo_int8_convrot.safetensors",
                 f"{_QI21}/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors", 7256787829), *QI21_SHARED]
QI21_FULL = [_f("diffusion_models", "qwen_image_2.1_int8_convrot.safetensors",
                f"{_QI21}/diffusion_models/qwen_image_2.1_int8_convrot.safetensors", 7256783064), *QI21_SHARED]
ENHANCE = [
    _f("upscale_models", "4x-UltraSharp.safetensors", f"{HF}/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.safetensors", 66864028),
    _f("background_removal", "birefnet.safetensors", f"{_CO}/BiRefNet/resolve/main/background_removal/birefnet.safetensors", 444473596),
    _f("diffusion_models", "seedvr2_3b_fp8_e4m3fn.safetensors", f"{_CO}/SeedVR2/resolve/main/diffusion_models/seedvr2_3b_fp8_e4m3fn.safetensors", 3392794232),
    _f("vae", "seedvr2_ema_vae_fp16.safetensors", f"{_CO}/SeedVR2/resolve/main/vae/ema_vae_fp16.safetensors", 501324814),
]
UMT5 = _f("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", f"{_CO}/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6735906897)
_WAN22 = f"{_CO}/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files"
VIDEO_5B = [
    _f("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors", f"{_WAN22}/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 9999658848),
    _f("vae", "wan2.2_vae.safetensors", f"{_WAN22}/vae/wan2.2_vae.safetensors", 1409400960),
    UMT5,
]
VIDEO_14B = [
    _f("diffusion_models", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", f"{_WAN22}/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", 14294742832),
    _f("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", f"{_WAN22}/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", 14294742832),
    _f("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", f"{_WAN22}/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", 1226977424),
    _f("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", f"{_WAN22}/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", 1226977424),
    UMT5,
    _f("vae", "wan_2.1_vae.safetensors", f"{_CO}/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors", 253815318),
]
_SA3 = f"{_CO}/stable-audio-3/resolve/main"
_ACE = f"{_CO}/ace_step_1.5_ComfyUI_files/resolve/main/split_files"
AUDIO = [
    _f("checkpoints", "stable_audio_3_small_sfx.safetensors", f"{_SA3}/checkpoints/stable_audio_3_small_sfx.safetensors", 2270384940),
    _f("text_encoders", "t5gemma_b_b_ul2.safetensors", f"{_SA3}/text_encoders/t5gemma_b_b_ul2.safetensors", 1187264003),
    _f("diffusion_models", "acestep_v1.5_turbo.safetensors", f"{_ACE}/diffusion_models/acestep_v1.5_turbo.safetensors", 4787825604),
    _f("text_encoders", "qwen_0.6b_ace15.safetensors", f"{_ACE}/text_encoders/qwen_0.6b_ace15.safetensors", 1191588248),
    _f("text_encoders", "qwen_1.7b_ace15.safetensors", f"{_ACE}/text_encoders/qwen_1.7b_ace15.safetensors", 3708523360),
    _f("vae", "ace_1.5_vae.safetensors", f"{_ACE}/vae/ace_1.5_vae.safetensors", 337431732),
]
AUDIO_HQ = [_f("checkpoints", "stable_audio_3_medium.safetensors", f"{_SA3}/checkpoints/stable_audio_3_medium.safetensors", 9222116660)]
HUNYUAN = [_f("checkpoints", "hunyuan_3d_v2.1.safetensors", f"{_CO}/hunyuan3D_2.1_repackaged/resolve/main/hunyuan_3d_v2.1.safetensors", 7365943290)]
HUNYUAN_MV = [_f("checkpoints", "hunyuan3d-dit-v2-mv_fp16.safetensors", f"{_CO}/hunyuan3D_2.0_repackaged/resolve/main/split_files/hunyuan3d-dit-v2-mv_fp16.safetensors", 4928151562)]
SDXL = [_f("checkpoints", "sd_xl_base_1.0.safetensors", f"{HF}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors", 6938078334)]


# ---------------------------------------------------------------- itens

@dataclass
class Item:
    id: str
    group: str
    title: str
    description: str
    needed_for: str = ""
    required: bool = False
    files: list[ModelFile] = field(default_factory=list)
    size: int = 0                                   # bytes aproximados (quando não são arquivos do catálogo)
    check: Callable[[], tuple[str, str]] | None = None   # -> (estado, detalhe)
    install: Callable[[TaskManager, "Item"], Any] | None = None
    link: str | None = None
    manual: str | None = None                       # instrução quando não dá para instalar daqui
    depends: list[str] = field(default_factory=list)
    install_label: str = "Instalar"

    def status(self, root: Path) -> dict[str, Any]:
        if self.files:
            states = [f.state(root) for f in self.files]
            have = sum(f.path(root).stat().st_size for f, s in zip(self.files, states) if s == "ok")
            if all(s == "ok" for s in states):
                st, detail = "ok", f"{len(states)} arquivo(s)"
            elif any(s == "mismatch" for s in states):
                bad = [f.name for f, s in zip(self.files, states) if s == "mismatch"]
                st, detail = "partial", f"incompleto (tamanho errado): {', '.join(bad)}"
            elif any(s in ("ok", "partial") for s in states):
                st, detail = "partial", f"{states.count('ok')} de {len(states)} arquivos"
            else:
                st, detail = "missing", ""
            return {"state": st, "detail": detail, "have": have,
                    "files": [{"name": f.name, "folder": f.folder, "size": f.size, "state": s, "url": f.url} for f, s in zip(self.files, states)]}
        if self.check:
            st, detail = self.check()
            return {"state": st, "detail": detail}
        return {"state": "manual", "detail": ""}

    def describe(self, root: Path) -> dict[str, Any]:
        size = sum({f.name: f.size for f in self.files}.values()) or self.size
        return {"id": self.id, "group": self.group, "title": self.title, "description": self.description,
                "needed_for": self.needed_for, "required": self.required, "size": size, "link": self.link,
                "manual": self.manual, "depends": self.depends, "can_install": self.install is not None or bool(self.files),
                "install_label": self.install_label, **self.status(root)}


def _which(*names: str, extra: list[str] = ()) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for pat in extra:
        p = Path(os.path.expandvars(pat))
        if p.exists():
            return str(p)
    return None


def _winget(pkg: str) -> Callable[[TaskManager, Item], Any]:
    def go(tm: TaskManager, it: Item):
        if not shutil.which("winget"):
            raise RuntimeError("winget não encontrado (Windows 10/11 com o App Installer da Microsoft Store)")
        return tm.run_command(it.id, it.title, ["winget", "install", "--id", pkg, "-e", "--silent",
                                                 "--accept-package-agreements", "--accept-source-agreements"])
    return go


def _ps(script: str, *args: str) -> Callable[[TaskManager, Item], Any]:
    def go(tm: TaskManager, it: Item):
        return tm.run_command(it.id, it.title, ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                                 str(ROOT / script), *[a() if callable(a) else a for a in args]], cwd=ROOT)
    return go


def _ollama_check() -> tuple[str, str]:
    from . import local_llm

    exe = _which("ollama", extra=["%LOCALAPPDATA%/Programs/Ollama/ollama.exe"])
    if not exe:
        return "missing", ""
    try:
        v = local_llm._get("/api/version").get("version")
        return "ok", f"versão {v} em {local_llm.URL}"
    except local_llm.LocalLLMError:
        return "partial", "instalado, mas não está rodando: abra o app Ollama"


def _ffmpeg_check() -> tuple[str, str]:
    try:
        from forge_video.ff import FFmpegError, binary
    except ImportError:
        exe = _which("ffmpeg")
        return ("ok", exe) if exe else ("missing", "")
    try:
        return "ok", binary("ffmpeg")
    except FFmpegError:
        return "missing", ""


def _tool(name: str, *extra: str) -> Callable[[], tuple[str, str]]:
    def check() -> tuple[str, str]:
        exe = _which(name, extra=list(extra))
        return ("ok", exe) if exe else ("missing", "")
    return check


def _hf_check() -> tuple[str, str]:
    return ("ok", "token configurado") if hf_token() else ("missing", "sem token (só os modelos restritos precisam)")


def _claude_check() -> tuple[str, str]:
    from .critic import api_available

    ok, how = api_available()
    return ("ok", how) if ok else ("missing", "opcional: sem credencial, use o modo manual (Claude Code via MCP)")


def _instance_check(name: str) -> Callable[[], tuple[str, str]]:
    def check() -> tuple[str, str]:
        inst = cs.get(name)
        if not inst.installed:
            return "missing", ""
        if name == "3d" and not (ROOT / "comfy/instance/ComfyUI/custom_nodes/forge_nodes/__init__.py").exists():
            return "partial", ("o nó forge_nodes (ForgeLoad3DFile) sumiu da instância: clique em Reinstalar "
                               "(texture_ai, refine e outros pipelines 3D falham sem ele)")
        return "ok", "rodando" if cs.port_open(inst.port, 0.3) else "instalada (parada: inicie em Opções > ComfyUI)"
    return check


DESKTOP_COMFY = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Installs" / "ComfyUI" / "ComfyUI"
_DESKTOP_YAML = DESKTOP_COMFY / "extra_model_paths.yaml"
_SHARED_FOLDERS = ["checkpoints", "clip_vision", "controlnet", "diffusion_models", "embeddings", "loras", "model_patches",
                   "text_encoders", "upscale_models", "background_removal", "audio_encoders", "frame_interpolation",
                   "vae", "clip", "unet", "latent_upscale_models"]


def desktop_linked() -> bool:
    """O Comfy Desktop lê a pasta de modelos do Asset Forge? (extra_model_paths.yaml ao lado do main.py dele)"""
    try:
        return str(models_dir()) in _DESKTOP_YAML.read_text(encoding="utf-8")
    except OSError:
        return False


def link_desktop_models() -> Path:
    """Faz o Comfy Desktop enxergar também a pasta de modelos do Asset Forge, sem mexer na pasta dele (que continua a
    padrão). O ComfyUI lê esse arquivo além do que o Desktop passa na linha de comando; vale ao reiniciar o servidor."""
    if not (DESKTOP_COMFY / "main.py").exists():
        raise RuntimeError("Comfy Desktop não encontrado (abra-o uma vez para concluir a instalação)")
    lines = ["# Gerado pelo Asset Forge: o Comfy Desktop também enxerga a pasta de modelos do Asset Forge",
             "asset_forge_models:", f"  base_path: '{models_dir()}'"] + [f"  {f}: {f}/" for f in _SHARED_FOLDERS]
    _DESKTOP_YAML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _DESKTOP_YAML


def _desktop_check() -> tuple[str, str]:
    st, detail = _instance_check("desktop")()
    if st != "ok":
        return st, detail
    if not desktop_linked():
        return "partial", ("não enxerga a pasta de modelos do Asset Forge (" + str(models_dir()) + "): clique em "
                           "Conectar pasta de modelos e reinicie o Comfy Desktop")
    if cs.port_open(8188, 0.3):
        try:
            import httpx

            if not httpx.get("http://127.0.0.1:8188/models/diffusion_models", timeout=3).json():
                return "partial", "pasta conectada, mas o servidor ainda não recarregou: reinicie o Comfy Desktop"
        except (httpx.HTTPError, ValueError):
            pass
    return "ok", "rodando e enxergando os modelos" if cs.port_open(8188, 0.3) else "instalado (parado)"


def _desktop_link(tm: TaskManager, it: "Item"):
    def fn(t) -> None:
        path = link_desktop_models()
        t.out(f"gravado {path}")
        t.out("reinicie o Comfy Desktop (feche e abra o app) para ele carregar a pasta")
    return tm.run_python(it.id, it.title, fn)


_TRELLIS_MODELS = ROOT / "comfy/instance-trellis/ComfyUI/models"


def _trellis_weights_check() -> tuple[str, str]:
    need = {"TRELLIS.2-4B": _TRELLIS_MODELS / "microsoft/TRELLIS.2-4B",
            "DINOv3": _TRELLIS_MODELS / "facebook/dinov3-vitl16-pretrain-lvd1689m/model.safetensors"}
    miss = [k for k, p in need.items() if not p.exists()]
    return ("ok", "") if not miss else ("missing" if len(miss) == len(need) else "partial", "falta: " + ", ".join(miss))


def _paint_check() -> tuple[str, str]:
    root = models_dir()
    files = [root / "diffusion_models/hunyuan3d-paintpbr-v2-1.safetensors", root / "vae/hunyuan3d_paintpbr_v2_1_vae.safetensors"]
    n = sum(p.exists() for p in files)
    return ("ok", "") if n == 2 else ("partial" if n else "missing", "")


def _venv_script(venv: str, script: str) -> Callable[[TaskManager, Item], Any]:
    def go(tm: TaskManager, it: Item):
        py = ROOT / venv / "Scripts" / "python.exe"
        if not py.exists():
            raise RuntimeError(f"instale antes a instância ({venv})")
        return tm.run_command(it.id, it.title, [str(py), "-u", str(ROOT / script)], cwd=ROOT)
    return go


ITEMS: list[Item] = [
    # ---- ferramentas
    Item("ollama", "tools", "Ollama", "Roda LLMs locais: assistente de prompt, tradução nas Souls e a verificação visual dos projetos +18.",
         "assistente de prompt (✨), projetos +18", required=True, size=1_200_000_000, check=_ollama_check, install=_winget("Ollama.Ollama"),
         link="https://ollama.com/download"),
    Item("ffmpeg", "tools", "FFmpeg", "Codifica e lê vídeo (MP4/WebM, quadros) e áudio de vídeos.", "vídeo, exportar animação em vídeo",
         required=True, size=200_000_000, check=_ffmpeg_check, install=_winget("Gyan.FFmpeg"), link="https://ffmpeg.org/download.html"),
    Item("git", "tools", "Git", "Baixa o ComfyUI e os nós nas instalações das instâncias 3D.", "instalar as instâncias 3D",
         size=350_000_000, check=_tool("git", "%ProgramFiles%/Git/cmd/git.exe"), install=_winget("Git.Git")),
    Item("uv", "tools", "uv", "Gerenciador de Python usado pelas instâncias 3D (ambientes isolados com o PyTorch certo).",
         "instalar as instâncias 3D", size=40_000_000, check=_tool("uv", "%USERPROFILE%/.local/bin/uv.exe"), install=_winget("astral-sh.uv")),
    Item("huggingface", "tools", "Conta do Hugging Face", "Token de acesso para modelos restritos (DINOv3 do TRELLIS) e downloads mais estáveis.",
         "pesos do TRELLIS.2", check=_hf_check, link="https://huggingface.co/settings/tokens",
         manual="Crie um token de leitura em huggingface.co/settings/tokens e cole abaixo; para o DINOv3, aceite a licença em "
                "huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m."),
    Item("claude", "tools", "Claude (API da Anthropic)", "Crítico da geração avançada, shaders/partículas escritos pela IA e o planejador de animação 3D.",
         "geração avançada, VFX com IA, 3d.animate", check=_claude_check, link="https://console.anthropic.com/",
         manual="Opcional. Defina ANTHROPIC_API_KEY antes de iniciar o Asset Forge ou rode `ant auth login`. Sem isso, o Claude Code "
                "faz esses papéis pelo MCP."),
    # ---- ComfyUI
    Item("comfy-desktop", "comfy", "Comfy Desktop (instância 2D, :8188)", "O ComfyUI oficial: imagens, edição, vídeo, upscale e áudio.",
         "2D, vídeo, áudio", required=True, size=5_000_000_000, check=_desktop_check, link="https://www.comfy.org/download",
         install=_desktop_link, install_label="Conectar pasta de modelos",
         manual="Baixe e instale pelo site e abra uma vez para concluir. Depois clique em Conectar pasta de modelos: o Desktop "
                "passa a enxergar também os modelos do Asset Forge (sem duplicar nada) ao ser reiniciado. Enquanto isso, "
                "os pipelines usam outra instância no ar que tenha os modelos."),
    Item("comfy-3d", "comfy", "Instância Hunyuan3D (:8189)", "ComfyUI dedicado com os nós do Hunyuan3D 2.1 (forma + textura PBR), "
         "usando a mesma pasta de modelos.", "3D: imagem→malha, textura com IA", size=9_000_000_000, check=_instance_check("3d"),
         install=_ps("comfy/install.ps1", "-SharedModels", lambda: str(models_dir())), depends=["git", "uv"]),
    Item("comfy-trellis", "comfy", "Instância TRELLIS.2 (:8190)", "ComfyUI separado com PyTorch 2.10 e o TRELLIS.2 (bordas nítidas, peças pequenas).",
         "3D com TRELLIS.2", size=10_000_000_000, check=_instance_check("trellis"), install=_ps("comfy/install_trellis.ps1", "-SharedModels", lambda: str(models_dir())),
         depends=["git", "uv"]),
    Item("trellis-weights", "comfy", "Pesos do TRELLIS.2 + DINOv3", "TRELLIS.2-4B (~16 GB) e o DINOv3 (restrito: aceite a licença e configure "
         "o token do Hugging Face antes).", "3D com TRELLIS.2", size=17_500_000_000, check=_trellis_weights_check,
         install=_venv_script("comfy/instance-trellis/ComfyUI/.venv", "comfy/download_trellis_models.py"), depends=["comfy-trellis", "huggingface"],
         install_label="Baixar"),
    Item("hunyuan-paint", "comfy", "Textura PBR do Hunyuan3D 2.1", "Baixa os pesos oficiais da Tencent e converte para o ComfyUI (~4 GB).",
         "3d.texture_ai", size=4_300_000_000, check=_paint_check,
         install=_venv_script("comfy/instance/ComfyUI/.venv", "comfy/convert_hunyuan_paint.py"), depends=["comfy-3d"], install_label="Baixar e converter"),
    # ---- modelos (pasta compartilhada)
    Item("models-image", "models", "Imagens 2D — Z-Image Turbo", "Gera sprites, ícones e texturas a partir de texto.",
         "2d.text2d, pixel art, Souls, texturas de VFX, texto→3D", required=True, files=ZIMAGE),
    Item("models-qwen-image", "models", "Imagens 2D — Qwen-Image 2.1 Turbo (novo)",
         "O modelo de imagem mais recente da Alibaba (set/out 2026): ótimo com texto escrito na imagem, composição e "
         "retratos; 8 passos, int8 (~7 GB) + codificador Qwen3-VL 8B. Roda no ComfyUI 0.38+ (a instância TRELLIS serve).",
         "2d.text2d / text2pixel com engine=qwen-image-2.1", files=QI21_TURBO),
    Item("models-qwen-image-full", "models", "Imagens 2D — Qwen-Image 2.1 completo", "A versão base (25 passos): mais lenta, "
         "um pouco mais fiel; compartilha o codificador e o VAE com o Turbo.", "engine=qwen-image-2.1-full", files=QI21_FULL,
         depends=["models-qwen-image"]),
    Item("models-edit", "models", "Edição — Qwen-Image-Edit 2511", "Edita por instrução, gera quadros de animação e vistas; LoRA Lightning (4 passos).",
         "2d.edit, image2d, animação quadro a quadro, 3d.ai_edit", required=True, files=QWEN_EDIT),
    Item("models-enhance", "models", "Upscale e remover fundo", "4x-UltraSharp, SeedVR2 3B (upscale fiel) e BiRefNet (recorte de fundo).",
         "2d.enhance, 2d.remove_bg, remove_bg em todos os pipelines", required=True, files=ENHANCE),
    Item("models-video", "models", "Vídeo — Wan 2.2 TI2V 5B", "Texto/imagem → vídeo 720p, leve.", "video.generate/animate/extend", files=VIDEO_5B),
    Item("models-video-14b", "models", "Animação — Wan 2.2 I2V 14B", "Imagem → vídeo com quadro final; mais pesado e preciso (LoRA de 4 passos).",
         "2d.animate, vídeo com quadro final, regen_segment", files=VIDEO_14B),
    Item("models-audio", "models", "Áudio — Stable Audio 3 + ACE-Step 1.5", "Efeitos sonoros e música (com ou sem letra).",
         "audio.sfx, audio.music, audio.variation", files=AUDIO),
    Item("models-audio-hq", "models", "Áudio HQ — Stable Audio 3 medium", "Efeitos e ambiências em qualidade maior.", "audio.sfx (qualidade alta)",
         files=AUDIO_HQ, depends=["models-audio"]),
    Item("models-3d", "models", "3D — Hunyuan3D 2.1", "Imagem → malha 3D.", "3d.image_to_3d, text_to_3d, ai_edit", files=HUNYUAN, depends=["comfy-3d"]),
    Item("models-3d-mv", "models", "3D multivista — Hunyuan3D 2.0 MV", "Frente/lado/costas → malha.", "3D a partir de várias vistas",
         files=HUNYUAN_MV, depends=["comfy-3d"]),
    Item("models-sdxl", "models", "SDXL base 1.0", "Checkpoint base para LoRAs das Creator Souls (modo LoRA) e 2d.checkpoint.",
         "Souls com LoRA, 2d.checkpoint", files=SDXL),
]


def _llm_items() -> list[Item]:
    from . import local_llm

    def check_for(mid: str) -> Callable[[], tuple[str, str]]:
        def check() -> tuple[str, str]:
            try:
                ms = local_llm.models()
            except local_llm.LocalLLMError:
                return "missing", "Ollama parado"
            return ("ok", "") if any(local_llm._same(m, mid) for m in ms) else ("missing", "")
        return check

    def pull(mid: str) -> Callable[[TaskManager, Item], Any]:
        return lambda tm, it: tm.ollama_pull(it.id, it.title, mid)

    return [Item(f"ollama:{c['id']}", "llm", c["title"], c["description"], "assistente de prompt" + (" (+18)" if c.get("nsfw") else ""),
                 required=bool(c.get("recommended")), size=int(c["size_gb"] * 1e9), check=check_for(c["id"]), install=pull(c["id"]),
                 depends=["ollama"], install_label="Baixar") for c in local_llm.CATALOG]


def items() -> list[Item]:
    return ITEMS + _llm_items()


def get(item_id: str) -> Item:
    it = next((i for i in items() if i.id == item_id), None)
    if it is None:
        raise KeyError(f"item desconhecido: {item_id}")
    return it


GROUPS = [
    {"id": "tools", "title": "Ferramentas e contas", "description": "Programas que o Asset Forge chama e credenciais opcionais."},
    {"id": "comfy", "title": "ComfyUI", "description": "Os servidores de geração (cada um com suas dependências)."},
    {"id": "models", "title": "Modelos por recurso", "description": "Pesos baixados para a pasta compartilhada de modelos do ComfyUI."},
    {"id": "llm", "title": "LLMs locais (Ollama)", "description": "Modelos de linguagem que rodam fora do ComfyUI."},
]


def overview() -> dict[str, Any]:
    root = models_dir()
    its = [i.describe(root) for i in items()]
    try:
        free = shutil.disk_usage(root if root.exists() else root.anchor).free
    except OSError:
        free = None
    return {"models_dir": str(root), "models_dir_exists": root.exists(), "disk_free": free, "groups": GROUPS, "items": its,
            "winget": bool(shutil.which("winget"))}


def install(tm: TaskManager, item_id: str):
    it = get(item_id)
    if it.files:
        root = models_dir()
        todo = []
        for f in it.files:
            st, p = f.state(root), f.path(root)
            if st == "ok":
                continue
            if st == "mismatch":   # download interrompido sem .part: continua de onde parou (ou recomeça se maior)
                part = Path(str(p) + ".part")
                if p.stat().st_size < f.size and not part.exists():
                    p.replace(part)
                else:
                    p.unlink()
            todo.append((f.url, p))
        return tm.download_files(it.id, it.title, todo)
    if it.install is None:
        raise ValueError(f"{it.title} não se instala por aqui: {it.manual or it.link or ''}")
    return it.install(tm, it)


def save_hf_token(token: str) -> dict[str, Any]:
    """Confere o token no Hugging Face e grava onde a biblioteca `huggingface_hub` procura (HF_HOME/token)."""
    import httpx

    token = token.strip()
    r = httpx.get(f"{HF}/api/whoami-v2", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    if r.status_code != 200:
        raise ValueError("token recusado pelo Hugging Face")
    p = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "token"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="utf-8")
    return {"user": r.json().get("name"), "path": str(p)}


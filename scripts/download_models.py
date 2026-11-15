"""Baixa os modelos do ComfyUI usados por um grupo de recursos do Asset Forge.

    python scripts/download_models.py audio            # lista o que falta e baixa
    python scripts/download_models.py audio --list     # só lista (tamanho, destino)
    python scripts/download_models.py audio-hq         # Stable Audio 3 medium (SFX em alta qualidade)
    python scripts/download_models.py video            # Wan 2.2 TI2V 5B (text/image → vídeo 720p)

Destino padrão: a pasta compartilhada de modelos do Comfy Desktop
(%LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Shared/models) ou --models-dir / FORGE_MODELS_DIR.
Downloads usam <arquivo>.part e são retomáveis; o pipeline recusa arquivos ainda incompletos.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

HF = "https://huggingface.co"
GROUPS: dict[str, list[tuple[str, str]]] = {
    # (pasta do comfy, url)
    "audio": [
        ("checkpoints", f"{HF}/Comfy-Org/stable-audio-3/resolve/main/checkpoints/stable_audio_3_small_sfx.safetensors"),
        ("text_encoders", f"{HF}/Comfy-Org/stable-audio-3/resolve/main/text_encoders/t5gemma_b_b_ul2.safetensors"),
        ("diffusion_models", f"{HF}/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/diffusion_models/acestep_v1.5_turbo.safetensors"),
        ("text_encoders", f"{HF}/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_0.6b_ace15.safetensors"),
        ("text_encoders", f"{HF}/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_1.7b_ace15.safetensors"),
        ("vae", f"{HF}/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/vae/ace_1.5_vae.safetensors"),
    ],
    "video": [
        ("diffusion_models", f"{HF}/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"),
        ("vae", f"{HF}/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors"),
    ],
    "audio-hq": [
        ("checkpoints", f"{HF}/Comfy-Org/stable-audio-3/resolve/main/checkpoints/stable_audio_3_medium.safetensors"),
    ],
}


def default_models_dir() -> Path:
    env = os.environ.get("FORGE_MODELS_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"


def hf_token() -> str | None:
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok
    p = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "token"
    return p.read_text().strip() if p.exists() else None


def remote_size(client: httpx.Client, url: str) -> int:
    r = client.head(url, follow_redirects=True)
    return int(r.headers.get("x-linked-size") or r.headers.get("content-length") or 0)


def download(client: httpx.Client, url: str, dest: Path, retries: int = 30) -> None:
    """Baixa com retomada: se a conexão cair, continua de onde parou (até `retries` vezes)."""
    import time

    for attempt in range(retries):
        try:
            return _download_once(client, url, dest)
        except (httpx.TransportError, httpx.RemoteProtocolError) as e:
            wait = min(60, 3 * (attempt + 1))
            print(f"\n  conexão caiu ({e.__class__.__name__}); retomando em {wait}s (tentativa {attempt + 2}/{retries})")
            time.sleep(wait)
    raise SystemExit(f"falhou após {retries} tentativas: {dest.name}")


def _download_once(client: httpx.Client, url: str, dest: Path) -> None:
    part = Path(str(dest) + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    with client.stream("GET", url, headers=headers, follow_redirects=True, timeout=None) as r:
        if r.status_code == 416:  # já completo
            part.replace(dest)
            return
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + (done if r.status_code == 206 else 0)
        mode = "ab" if r.status_code == 206 else "wb"
        got = done if r.status_code == 206 else 0
        with part.open(mode) as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total:
                    sys.stdout.write(f"\r  {got / 1e9:6.2f} / {total / 1e9:.2f} GB ({got * 100 // total}%)")
                    sys.stdout.flush()
    sys.stdout.write("\n")
    part.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("group", choices=sorted(GROUPS))
    ap.add_argument("--list", action="store_true", help="só listar")
    ap.add_argument("--models-dir", type=Path, default=None)
    a = ap.parse_args()
    root = a.models_dir or default_models_dir()
    tok = hf_token()
    client = httpx.Client(headers={"Authorization": f"Bearer {tok}"} if tok else {}, timeout=60)
    todo = []
    for folder, url in GROUPS[a.group]:
        dest = root / folder / url.rsplit("/", 1)[-1]
        size = remote_size(client, url)
        state = "ok" if dest.exists() else ("parcial" if Path(str(dest) + ".part").exists() else "falta")
        print(f"[{state:7}] {size / 1e9:5.2f} GB  {folder}/{dest.name}")
        if state != "ok":
            todo.append((url, dest, size))
    print(f"total a baixar: {sum(s for *_, s in todo) / 1e9:.2f} GB -> {root}")
    if a.list or not todo:
        return 0
    for url, dest, _ in todo:
        print(f"baixando {dest.name}")
        download(client, url, dest)
    print("pronto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

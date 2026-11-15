"""Download the 3D models listed in comfy/models.json into the shared models folder.

    uv run python comfy/download_models.py              # default models
    uv run python comfy/download_models.py hunyuan3d-2mv
    uv run python comfy/download_models.py --list

Resumable (HTTP Range); files land in the Comfy Desktop shared folder so both
Comfy Desktop and the dedicated instance see them. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).with_name("models.json")
SHARED = Path(os.environ.get("FORGE_MODELS_DIR", Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"))


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    done = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={done}-"} if done else {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if done and resp.status != 206:  # server ignored Range: restart
            done = 0
        total = done + int(resp.headers.get("Content-Length", 0))
        with open(part, "ab" if done else "wb") as fh:
            start, last = time.time(), 0.0
            got = done
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last > 2:
                    speed = (got - done) / max(now - start, 1e-6) / 1e6
                    print(f"  {got / 1e9:6.2f} / {total / 1e9:.2f} GB  {speed:5.1f} MB/s", flush=True)
                    last = now
    part.replace(dest)


def main(argv: list[str]) -> int:
    models = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if "--list" in argv:
        for key, m in models.items():
            path = SHARED / m["folder"] / m["file"]
            print(f"{key:16} {'OK     ' if path.exists() else 'missing'} {path}\n{'':16} {m['description']}")
        return 0
    keys = argv or [k for k, m in models.items() if m.get("default")]
    for key in keys:
        m = models[key]
        dest = SHARED / m["folder"] / m["file"]
        if dest.exists():
            print(f"{key}: already present ({dest})")
            continue
        if not m.get("url"):
            print(f"{key}: no download URL; install it via Comfy Desktop")
            continue
        print(f"{key}: downloading to {dest}")
        download(m["url"], dest)
        print(f"{key}: done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Exportar/importar Souls como arquivo .soul (zip da pasta da Soul)."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

from .library import SOUL_FILE, Soul, SoulLibrary, SoulManifest


def export_soul(soul: Soul, dest: Path) -> Path:
    dest = Path(dest)
    if dest.is_dir():
        dest = dest / f"{soul.id}.soul"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in sorted(soul.root.rglob("*")):
            if f.is_file() and not f.name.endswith(".tmp"):
                z.write(f, f.relative_to(soul.root).as_posix())
    return dest


def import_soul(library: SoulLibrary, src: Path, new_name: str | None = None) -> Soul:
    src = Path(src)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if SOUL_FILE not in names:
            raise ValueError("arquivo .soul inválido (sem soul.yaml)")
        for n in names:  # sem caminhos absolutos ou '..'
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError(f"caminho inválido no pacote: {n}")
        manifest = SoulManifest.model_validate(yaml.safe_load(z.read(SOUL_FILE)) or {})
        sid = library.unique_id(new_name or manifest.name)
        with tempfile.TemporaryDirectory() as td:
            z.extractall(td)
            library.root.mkdir(parents=True, exist_ok=True)
            dst = library.root / sid
            shutil.copytree(td, dst)
    soul = Soul.load(dst)
    soul.manifest.id = sid
    if new_name:
        soul.manifest.name = new_name
    soul.manifest.source = {**soul.manifest.source, "imported_from": src.name}
    soul.save()
    return soul

"""Animation libraries: folders of GLB files with a humanoid skeleton and clips (e.g. Quaternius' Universal Animation
Library 1 and 2, CC0) that 3d.character_rig / 3d.retarget apply to characters.

Folders: settings.json "3d.animation_libraries" (Options), FORGE_ANIM_LIBRARY (os.pathsep separated) and
``<data>/animations`` next to the projects folder. Files named ``*_RM.glb`` are the root-motion versions of the
file without the suffix (the character travels with the clip instead of walking in place).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .geometry.gltf_anim import AnimationFile, read_json_only

_lock = threading.Lock()
_index_cache: dict[tuple[str, float, int], "LibraryFile | None"] = {}
_file_cache: dict[tuple[str, float], AnimationFile] = {}

# clip sets offered by the UI / used by default (names looked up in every library; missing ones are skipped)
SETS: dict[str, list[str]] = {
    "basic": ["Idle_Loop", "Walk_Loop", "Jog_Fwd_Loop", "Sprint_Loop", "Jump_Start", "Jump_Loop", "Jump_Land",
              "Crouch_Idle_Loop", "Crouch_Fwd_Loop", "Roll", "Interact", "Hit_Chest", "Death01"],
    "combat": ["Sword_Idle", "Sword_Attack", "Sword_Regular_Combo", "Sword_Block", "Punch_Jab", "Punch_Cross",
               "Spell_Simple_Idle_Loop", "Spell_Simple_Shoot", "Pistol_Idle_Loop", "Pistol_Shoot", "Hit_Knockback",
               "Idle_Shield_Loop", "Shield_OneShot"],
    "social": ["Idle_Talking_Loop", "Dance_Loop", "Yes", "Idle_No_Loop", "Idle_FoldArms_Loop", "Sitting_Idle_Loop",
               "PickUp_Table", "Consume"],
}


@dataclass
class LibraryFile:
    id: str                 # file stem, e.g. UAL1_Standard
    path: Path
    clips: list[str]
    durations: list[float]
    joints: int
    root_motion: bool

    @property
    def base_id(self) -> str:
        return self.id[:-3] if self.root_motion else self.id

    def describe(self) -> dict:
        return {"id": self.id, "path": str(self.path), "joints": self.joints, "root_motion": self.root_motion,
                "clips": [{"name": n, "duration": round(d, 3)} for n, d in zip(self.clips, self.durations)]}


def folders() -> list[Path]:
    out: list[Path] = []
    try:
        from forge_core import user_settings

        out += [Path(p) for p in (user_settings.get("3d", "animation_libraries") or []) if p]
        from forge_core.config import get_settings

        out.append(get_settings().projects_dir.parent / "animations")
    except Exception:
        pass
    out += [Path(p) for p in os.environ.get("FORGE_ANIM_LIBRARY", "").split(os.pathsep) if p]
    seen, uniq = set(), []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _probe(path: Path) -> LibraryFile | None:
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    if key in _index_cache:
        return _index_cache[key]
    info = None
    try:
        doc = read_json_only(path)
        anims = doc.get("animations") or []
        if doc.get("skins") and anims:
            durations = []
            for a in anims:
                end = 0.0
                for s in a.get("samplers", []):
                    acc = doc["accessors"][s["input"]]
                    end = max(end, float((acc.get("max") or [0.0])[0]))
                durations.append(end)
            info = LibraryFile(path.stem, path, [a.get("name") or f"clip{i}" for i, a in enumerate(anims)], durations,
                               len(doc["skins"][0]["joints"]), path.stem.upper().endswith("_RM"))
    except (OSError, ValueError, KeyError):
        info = None
    _index_cache[key] = info
    return info


def index() -> list[LibraryFile]:
    files: list[LibraryFile] = []
    with _lock:
        for folder in folders():
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.glb")):
                info = _probe(path)
                if info is not None and not any(f.id == info.id for f in files):
                    files.append(info)
    return files


def load(lib: LibraryFile) -> AnimationFile:
    key = (str(lib.path), lib.path.stat().st_mtime)
    with _lock:
        if key not in _file_cache:
            _file_cache[key] = AnimationFile(lib.path)
        return _file_cache[key]


def reference() -> AnimationFile:
    """The skeleton (and mannequin) of the first in-place library: the T-pose template characters are fitted to."""
    libs = [f for f in index() if not f.root_motion] or index()
    if not libs:
        raise LookupError(no_library_message())
    return load(libs[0])


def no_library_message() -> str:
    where = ", ".join(str(f) for f in folders()) or "(none)"
    return ("no animation library found. Put GLB animation libraries (e.g. Quaternius' Universal Animation Library, "
            f"Unreal-Godot/*.glb) in one of: {where}, or add the folder in Options > 3D")


def resolve(refs: list[str], root_motion: bool = False) -> list[tuple[LibraryFile, str]]:
    """[(library file, clip name)] for clip refs: "Walk_Loop" (first library that has it) or "UAL1_Standard/Walk_Loop".
    With ``root_motion`` the *_RM twin of the library is used when there is one."""
    libs = index()
    if not libs:
        raise LookupError(no_library_message())
    by_id = {f.id: f for f in libs}
    plain = [f for f in libs if not f.root_motion]
    out: list[tuple[LibraryFile, str]] = []
    missing: list[str] = []
    for ref in refs:
        lib_id, _, name = ref.rpartition("/")
        if lib_id:
            lib = by_id.get(lib_id)
            if lib is None or name not in lib.clips:
                missing.append(ref)
                continue
        else:
            lib = next((f for f in plain if name in f.clips), None) or next((f for f in libs if name in f.clips), None)
            if lib is None:
                missing.append(ref)
                continue
        if root_motion and not lib.root_motion:
            twin = by_id.get(lib.id + "_RM")
            if twin is not None and name in twin.clips:
                lib = twin
        if not any(o == (lib, name) for o in out):
            out.append((lib, name))
    if missing:
        known = sorted({c for f in libs for c in f.clips})
        raise ValueError(f"unknown animation(s): {', '.join(missing)}. Available: {', '.join(known)}")
    return out


def expand(sets: list[str], names: list[str]) -> list[str]:
    """Clip names of the chosen sets (only those some library has) plus the explicit names, without repeats."""
    available = {c for f in index() for c in f.clips}
    out: list[str] = []
    for s in sets:
        if s == "all":
            for f in index():
                if not f.root_motion:
                    out += [c for c in f.clips if c not in out and c != "A_TPose"]
            continue
        if s not in SETS:
            raise ValueError(f"unknown animation set '{s}'. Sets: {', '.join(['all', *SETS])}")
        out += [c for c in SETS[s] if c in available and c not in out]
    out += [n for n in names if n not in out]
    return out

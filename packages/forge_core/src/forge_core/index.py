"""Índice SQLite (regenerável) para busca/listagem rápida. A verdade está nos asset.json em disco."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .assets import AssetVersion

_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    modality TEXT, kind TEXT, name TEXT, pipeline TEXT, prompt TEXT,
    tags TEXT, parent TEXT, primary_file TEXT, preview_file TEXT, created TEXT, base TEXT,
    PRIMARY KEY (slug, version)
);
CREATE TABLE IF NOT EXISTS asset_meta (
    slug TEXT PRIMARY KEY, folder TEXT NOT NULL DEFAULT '', category TEXT
);
"""


class AssetIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(versions)")}
            if "base" not in cols:  # índices antigos
                c.execute("ALTER TABLE versions ADD COLUMN base TEXT")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert(self, av: "AssetVersion") -> None:
        p, pv = av.primary(), av.preview()
        row = (av.slug, av.version, av.modality, av.kind, av.name, av.pipeline, av.prompt or "",
               json.dumps(av.tags), av.parent, p.path if p else None, pv.path if pv else None, av.created.isoformat(),
               av.extra.get("base"))
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO versions (slug, version, modality, kind, name, pipeline, prompt, tags, parent, "
                      "primary_file, preview_file, created, base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    def remove(self, slug: str, version: int | None = None) -> None:
        with self._lock, self._conn() as c:
            if version is None:
                c.execute("DELETE FROM versions WHERE slug=?", (slug,))
                c.execute("DELETE FROM asset_meta WHERE slug=?", (slug,))
            else:
                c.execute("DELETE FROM versions WHERE slug=? AND version=?", (slug, version))

    def clear(self) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM versions")
            c.execute("DELETE FROM asset_meta")

    def set_meta(self, slug: str, folder: str, category: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO asset_meta VALUES (?,?,?)", (slug, folder or "", category))

    def folder_counts(self) -> dict[str, int]:
        """{pasta: nº de assets} (só pastas com assets; "" = raiz)."""
        sql = ("SELECT COALESCE(am.folder, '') AS folder, COUNT(DISTINCT v.slug) AS n FROM versions v "
               "LEFT JOIN asset_meta am ON am.slug = v.slug GROUP BY COALESCE(am.folder, '')")
        with self._conn() as c:
            return {r["folder"]: r["n"] for r in c.execute(sql).fetchall()}

    def search(self, q: str = "", modality: str | None = None, kind: str | None = None,
               tag: str | None = None, all_versions: bool = False, base: str | None = None,
               folder: str | None = None, recursive: bool = False, category: str | None = None) -> list[dict]:
        """Por padrão devolve só a última versão de cada asset, mais recentes primeiro."""
        where, args = [], []
        if q:
            where.append("(versions.slug LIKE ? OR name LIKE ? OR prompt LIKE ? OR tags LIKE ? OR am.folder LIKE ?)")
            args += [f"%{q}%"] * 5
        if modality:
            where.append("modality=?"); args.append(modality)
        if kind:
            where.append("kind=?"); args.append(kind)
        if tag:
            where.append("tags LIKE ?"); args.append(f'%"{tag}"%')
        if base:  # assets derivados de outro (ex.: animações de um personagem)
            where.append("(base = ? OR base LIKE ?)"); args += [base, f"{base}@%"]
        if folder is not None:
            if folder == "":
                where.append("COALESCE(am.folder, '') = ''" if not recursive else "1=1")
            elif recursive:
                where.append("(am.folder = ? OR am.folder LIKE ?)"); args += [folder, f"{folder}/%"]
            else:
                where.append("am.folder = ?"); args.append(folder)
        if category:
            where.append("am.category = ?") if category != "__none__" else where.append("am.category IS NULL")
            if category != "__none__":
                args.append(category)
        if not all_versions:
            where.append("version = (SELECT MAX(version) FROM versions v2 WHERE v2.slug = versions.slug)")
        sql = ("SELECT versions.*, COALESCE(am.folder, '') AS folder, am.category AS category, "
               "(SELECT COUNT(*) FROM versions v3 WHERE v3.slug = versions.slug) AS version_count "
               "FROM versions LEFT JOIN asset_meta am ON am.slug = versions.slug")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created DESC"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"] or "[]")
            d["ref"] = f"{d['slug']}@v{d['version']:03d}"
            out.append(d)
        return out
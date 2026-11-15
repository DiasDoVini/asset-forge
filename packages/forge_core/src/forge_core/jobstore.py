"""Histórico persistente de jobs (SQLite em <projects_dir>/.forge/jobs.db).

Registra jobs do servidor, da CLI e do MCP para consultar falhas e repetir sem redigitar.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    started TEXT, finished TEXT,
    status TEXT NOT NULL,
    project TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    request TEXT NOT NULL,       -- JSON do JobRequest (params, asset, name, tags)
    result TEXT, error TEXT,
    progress REAL DEFAULT 0, message TEXT,
    logs TEXT,                   -- JSON (últimas linhas)
    source TEXT DEFAULT 'server',
    retry_of TEXT
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);
"""

MAX_LOG_LINES = 300


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, job: dict[str, Any]) -> None:
        """Upsert de um job (dict no formato de Job.model_dump(mode='json'))."""
        req = job["request"]
        row = (job["id"], job["created"], job.get("started"), job.get("finished"), job["status"], req["project"],
               req["pipeline"], json.dumps(req, ensure_ascii=False), job.get("result"), job.get("error"),
               job.get("progress", 0), job.get("message"), json.dumps((job.get("logs") or [])[-MAX_LOG_LINES:], ensure_ascii=False),
               job.get("source", "server"), job.get("retry_of"))
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["request"] = json.loads(d["request"])
        d["logs"] = json.loads(d["logs"] or "[]")
        return d

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, project: str | None = None, status: str | None = None, pipeline: str | None = None,
             limit: int = 200) -> list[dict[str, Any]]:
        where, args = [], []
        if project:
            where.append("project=?"); args.append(project)
        if status:
            st = status.split(",")
            where.append(f"status IN ({','.join('?' * len(st))})"); args += st
        if pipeline:
            where.append("pipeline=?"); args.append(pipeline)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(sql, [*args, limit]).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, job_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def clear(self, status: str | None = None, project: str | None = None) -> int:
        where, args = ["status NOT IN ('queued','running')"], []
        if status:
            st = status.split(",")
            where.append(f"status IN ({','.join('?' * len(st))})"); args += st
        if project:
            where.append("project=?"); args.append(project)
        with self._lock, self._conn() as c:
            return c.execute("DELETE FROM jobs WHERE " + " AND ".join(where), args).rowcount

    def mark_interrupted(self) -> int:
        """No startup do servidor: jobs que ficaram 'running/queued' viram erro."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as c:
            return c.execute(
                "UPDATE jobs SET status='error', error='interrompido (servidor reiniciado ou processo encerrado)', "
                "finished=? WHERE status IN ('queued','running') AND source='server'", (now,)).rowcount


def default_store() -> JobStore:
    from .config import get_settings

    return JobStore(get_settings().projects_dir / ".forge" / "jobs.db")


def record_direct_run(source: str, project: str, pipeline: str, params: dict[str, Any], asset: str | None,
                      name: str | None, tags: list[str] | None, fn, retry_of: str | None = None):
    """Executa `fn()` (run_pipeline síncrono) registrando o job no histórico (CLI/MCP)."""
    import uuid

    store = default_store()
    now = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
    job: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12], "created": now(), "started": now(), "status": "running", "source": source,
        "request": {"project": project, "pipeline": pipeline, "params": params, "asset": asset, "name": name, "tags": tags or []},
        "logs": [], "retry_of": retry_of,
    }
    store.save(job)
    try:
        av = fn(job["logs"])
        job.update(status="done", result=av.ref, progress=1.0)
        return av
    except BaseException as e:
        job.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        job["finished"] = now()
        store.save(job)

"""Tarefas em segundo plano da tela de Instalação: baixar arquivos (retomável, com token do Hugging Face), baixar
modelos pelo Ollama e rodar scripts/instaladores (PowerShell, winget), com progresso, log e cancelamento.

Uma tarefa por item por vez; downloads de arquivo rodam no máximo 2 em paralelo. O estado fica em memória (o servidor
recarregar interrompe as tarefas; os arquivos `.part` ficam e o próximo download continua de onde parou).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx


def hf_token() -> str | None:
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok:
        return tok
    p = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "token"
    try:
        return p.read_text().strip() or None
    except OSError:
        return None


class Cancelled(Exception):
    pass


@dataclass
class Task:
    item: str
    title: str
    kind: str                       # file | ollama | script
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"          # queued | running | done | error | cancelled
    done: int = 0
    total: int = 0
    message: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    cancel: threading.Event = field(default_factory=threading.Event)
    proc: subprocess.Popen | None = None

    def out(self, line: str) -> None:
        self.log.append(line.rstrip())
        self.message = line.strip()[:200] or self.message

    def as_dict(self, with_log: bool = False) -> dict[str, Any]:
        d = {"id": self.id, "item": self.item, "title": self.title, "kind": self.kind, "status": self.status,
             "done": self.done, "total": self.total, "message": self.message, "started": self.started,
             "finished": self.finished}
        if with_log:
            d["log"] = list(self.log)
        return d


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._files = threading.Semaphore(2)

    def list(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in sorted(self.tasks.values(), key=lambda t: -t.started)]

    def get(self, tid: str) -> Task | None:
        return self.tasks.get(tid)

    def active(self, item: str) -> Task | None:
        return next((t for t in self.tasks.values() if t.item == item and t.status in ("queued", "running")), None)

    def cancel(self, tid: str) -> bool:
        t = self.tasks.get(tid)
        if not t or t.status not in ("queued", "running"):
            return False
        t.cancel.set()
        if t.proc and t.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(t.proc.pid), "/T", "/F"], capture_output=True)
            else:
                t.proc.terminate()
        return True

    def clear(self) -> int:
        with self._lock:
            old = [k for k, t in self.tasks.items() if t.status not in ("queued", "running")]
            for k in old:
                del self.tasks[k]
        return len(old)

    # ---------------------------------------------------------------- iniciar

    def _start(self, task: Task, fn: Callable[[Task], None], gate: threading.Semaphore | None = None) -> Task:
        with self._lock:
            cur = self.active(task.item)
            if cur:
                return cur
            self.tasks[task.id] = task

        def run() -> None:
            try:
                if gate:
                    gate.acquire()
                try:
                    if task.cancel.is_set():
                        raise Cancelled()
                    task.status = "running"
                    fn(task)
                    task.status = "done"
                    task.message = task.message or "pronto"
                finally:
                    if gate:
                        gate.release()
            except Cancelled:
                task.status, task.message = "cancelled", "cancelado"
            except Exception as e:  # noqa: BLE001 — qualquer falha vira estado da tarefa
                task.status = "cancelled" if task.cancel.is_set() else "error"
                task.out(f"erro: {e}")
            finally:
                task.finished = time.time()

        threading.Thread(target=run, name=f"setup-{task.item}", daemon=True).start()
        return task

    def download_files(self, item: str, title: str, files: list[tuple[str, Path]]) -> Task:
        """Baixa (url, destino) em sequência; o progresso soma todos os arquivos que faltam."""
        def fn(t: Task) -> None:
            tok = hf_token()
            headers = {"Authorization": f"Bearer {tok}"} if tok else {}
            with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as c:
                todo = [(u, d) for u, d in files if not d.exists()]
                sizes = []
                for u, d in todo:
                    try:
                        r = c.head(u)
                        if r.status_code in (401, 403):
                            raise RuntimeError(f"{d.name}: acesso negado ({r.status_code}). Modelo restrito: aceite a licença "
                                               "no Hugging Face e faça login (hf auth login) ou defina HF_TOKEN")
                        sizes.append(int(r.headers.get("x-linked-size") or r.headers.get("content-length") or 0))
                    except httpx.HTTPError:
                        sizes.append(0)
                t.total = sum(sizes)
                base = 0
                for (u, d), size in zip(todo, sizes):
                    t.out(f"baixando {d.name} → {d.parent}")
                    _download(c, u, d, t, base)
                    base += size or d.stat().st_size
                    t.done = base
                t.out(f"pronto ({len(todo)} arquivo(s))" if todo else "nada a baixar: já está tudo aqui")
        return self._start(Task(item, title, "file"), fn, self._files)

    def ollama_pull(self, item: str, title: str, model: str) -> Task:
        from . import local_llm

        def fn(t: Task) -> None:
            last = ""
            layers: dict[str, tuple[int, int]] = {}   # o Ollama informa o progresso por camada: soma todas
            for msg in local_llm.pull(model):
                if t.cancel.is_set():
                    raise Cancelled()
                st = msg.get("status", "")
                if msg.get("total"):
                    layers[msg.get("digest") or st] = (int(msg["total"]), int(msg.get("completed") or 0))
                    t.total = sum(a for a, _ in layers.values())
                    t.done = sum(b for _, b in layers.values())
                if st != last:
                    t.out(st)
                    last = st
            t.out(f"{model} pronto")
        return self._start(Task(item, title, "ollama"), fn)

    def run_python(self, item: str, title: str, fn: Callable[[Task], None]) -> Task:
        """Ação rápida em Python (ex.: gravar uma configuração), com o mesmo acompanhamento das demais."""
        return self._start(Task(item, title, "script"), fn)

    def run_command(self, item: str, title: str, args: list[str], cwd: Path | None = None) -> Task:
        def fn(t: Task) -> None:
            t.out("$ " + " ".join(args))
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            t.proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      creationflags=flags)
            assert t.proc.stdout is not None
            for raw in iter(t.proc.stdout.readline, b""):
                t.out(raw.decode("utf-8", "replace"))
            code = t.proc.wait()
            if t.cancel.is_set():
                raise Cancelled()
            if code != 0:
                raise RuntimeError(f"terminou com código {code}")
        return self._start(Task(item, title, "script"), fn)


def _download(c: httpx.Client, url: str, dest: Path, t: Task, base: int, retries: int = 20) -> None:
    part = Path(str(dest) + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        done = part.stat().st_size if part.exists() else 0
        try:
            with c.stream("GET", url, headers={"Range": f"bytes={done}-"} if done else {}, timeout=None) as r:
                if r.status_code == 416:      # .part já completo
                    break
                if r.status_code in (401, 403):
                    raise RuntimeError(f"acesso negado ({r.status_code}) a {dest.name}: aceite a licença no Hugging Face e faça login")
                r.raise_for_status()
                mode = "ab" if r.status_code == 206 else "wb"
                got = done if r.status_code == 206 else 0
                with part.open(mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        if t.cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        got += len(chunk)
                        t.done = base + got
            break
        except (httpx.TransportError, httpx.RemoteProtocolError) as e:
            wait = min(60, 3 * (attempt + 1))
            t.out(f"conexão caiu ({e.__class__.__name__}); retomando em {wait}s")
            if t.cancel.wait(wait):
                raise Cancelled() from None
    else:
        raise RuntimeError(f"falhou após {retries} tentativas: {dest.name}")
    part.replace(dest)

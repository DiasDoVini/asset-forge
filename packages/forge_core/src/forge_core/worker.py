"""Execução de um pipeline em subprocesso isolado (multiprocessing spawn).

Um crash nativo (access violation em pymeshlab/torch/PIL) mata só o worker; o servidor
reporta o job como erro com o exit code. Cancelamento: o pai sinaliza `cancel` (o pipeline
vê via ctx.check_cancelled / interrupt do Comfy) e, se o worker não sair em `grace` segundos,
o processo é terminado.

Mensagens do filho para o pai (tuplas na fila):
    ("reserved", slug, version)    pasta da versão criada (para limpeza em caso de crash)
    ("log", line)
    ("progress", value, message)
    ("done", ref)
    ("error", "Tipo: mensagem", cancelled: bool)
"""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_ctx = mp.get_context("spawn")


@dataclass
class WorkerRequest:
    project_root: str
    pipeline: str
    params: dict[str, Any]
    asset: str | None = None
    name: str | None = None
    tags: list[str] = field(default_factory=list)
    comfy_url: str | None = None
    folder: str | None = None
    category: str | None = None


def _child(req: WorkerRequest, out: Any, cancel_flag: Any) -> None:
    import sys

    # no MCP (stdio) o stdout herdado é o canal do protocolo: um print de pipeline o corromperia
    sys.stdout = sys.stderr
    from .pipeline import Cancelled
    from .project import Project
    from .runner import run_pipeline

    cancel = threading.Event()

    def watch() -> None:
        while not cancel.is_set():
            if cancel_flag.wait(0.25):
                cancel.set()

    threading.Thread(target=watch, daemon=True).start()
    try:
        project = Project.load(Path(req.project_root))
        av = run_pipeline(
            project, req.pipeline, req.params, asset=req.asset, name=req.name, tags=req.tags,
            comfy_url=req.comfy_url,
            on_log=lambda line: out.put(("log", line)),
            on_progress=lambda v, m: out.put(("progress", v, m)),
            on_reserved=lambda slug, ver: out.put(("reserved", slug, ver)),
            cancel=cancel, folder=req.folder, category=req.category,
        )
        out.put(("done", av.ref))
    except Cancelled:
        out.put(("error", "cancelado", True))
    except BaseException as e:  # noqa: BLE001 - tudo vira mensagem para o pai
        out.put(("error", f"{type(e).__name__}: {e}", cancel.is_set()))
    finally:
        cancel.set()


def describe_exit(code: int | None) -> str:
    if code is None:
        return "processo não terminou"
    u = code & 0xFFFFFFFF
    known = {0xC0000005: "access violation", 0xC00000FD: "stack overflow", 0xC0000409: "stack buffer overrun"}
    if u in known:
        return f"processo terminou com 0x{u:08X} ({known[u]})"
    if code < 0:
        return f"processo morto pelo sinal {-code}"
    return f"processo terminou com código {code}"


class WorkerResult:
    def __init__(self) -> None:
        self.ref: str | None = None
        self.error: str | None = None
        self.cancelled = False
        self.reserved: tuple[str, int] | None = None
        self.exitcode: int | None = None


def run_isolated(req: WorkerRequest, *, on_log: Callable[[str], None] | None = None,
                 on_progress: Callable[[float, str | None], None] | None = None,
                 cancel: threading.Event | None = None, grace: float = 10.0) -> WorkerResult:
    """Bloqueante (chame via asyncio.to_thread). Nunca levanta por crash do filho."""
    out = _ctx.Queue()
    cancel_flag = _ctx.Event()
    proc = _ctx.Process(target=_child, args=(req, out, cancel_flag), daemon=True, name=f"forge-{req.pipeline}")
    proc.start()
    res = WorkerResult()
    cancel_at: float | None = None
    finished = False

    def handle(msg: tuple) -> None:
        nonlocal finished
        kind = msg[0]
        if kind == "log" and on_log:
            on_log(msg[1])
        elif kind == "progress" and on_progress:
            on_progress(msg[1], msg[2])
        elif kind == "reserved":
            res.reserved = (msg[1], msg[2])
        elif kind == "done":
            res.ref, finished = msg[1], True
        elif kind == "error":
            res.error, res.cancelled, finished = msg[1], bool(msg[2]), True

    while True:
        try:
            handle(out.get(timeout=0.25))
            continue
        except queue.Empty:
            pass
        if cancel is not None and cancel.is_set() and cancel_at is None:
            cancel_flag.set()
            cancel_at = time.monotonic()
        if cancel_at is not None and proc.is_alive() and time.monotonic() - cancel_at > grace:
            proc.terminate()
        if not proc.is_alive():
            # esvazia o que sobrou na fila
            while True:
                try:
                    handle(out.get(timeout=0.1))
                except queue.Empty:
                    break
            break
    proc.join(5)
    res.exitcode = proc.exitcode
    if not finished:
        res.cancelled = cancel_at is not None
        res.error = "cancelado (processo encerrado)" if res.cancelled else describe_exit(proc.exitcode)
    return res

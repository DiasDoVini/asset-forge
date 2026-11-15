import sys
import threading
import time
from pathlib import Path

import pytest

from forge_core.pipeline import Registry, set_registry
from forge_core.project import Project
from forge_core.worker import WorkerRequest, describe_exit, run_isolated

sys.path.insert(0, str(Path(__file__).parent))  # o filho (spawn) herda sys.path


@pytest.fixture(autouse=True)
def crashy_env(monkeypatch):
    monkeypatch.setenv("FORGE_MODALITIES", "crashy:modality")
    set_registry(None)
    yield
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def test_worker_ok(project):
    logs, prog = [], []
    res = run_isolated(WorkerRequest(str(project.root), "crashy.ok", {"prompt": "caixa"}),
                       on_log=logs.append, on_progress=lambda v, m: prog.append(v))
    assert res.ref == "caixa@v001" and res.error is None
    assert logs == ["olá do worker"] and 0.5 in prog
    assert project.store.get("caixa").files[0].path == "image.png"


def test_worker_crash_is_contained(project):
    res = run_isolated(WorkerRequest(str(project.root), "crashy.segfault", {}))
    assert res.ref is None and not res.cancelled
    assert res.reserved == ("segfault", 1)
    assert "0xC0000005" in res.error or "sinal" in res.error
    # a versão ficou órfã; o sweep recolhe
    assert len(project.store.incomplete()) == 1
    assert project.store.sweep_incomplete(only=res.reserved) == ["segfault/v001"]
    assert project.store.incomplete() == [] and project.store.slugs() == []


def test_worker_cancel_kills_stuck_job(project):
    cancel = threading.Event()
    threading.Timer(1.5, cancel.set).start()
    t = time.monotonic()
    res = run_isolated(WorkerRequest(str(project.root), "crashy.hang", {}), cancel=cancel, grace=1.0)
    assert res.cancelled and res.ref is None
    assert time.monotonic() - t < 30


def test_describe_exit():
    assert "access violation" in describe_exit(-1073741819)  # 0xC0000005 assinado
    assert describe_exit(3) == "processo terminou com código 3"

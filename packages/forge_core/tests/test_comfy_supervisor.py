import os
import socket
import sys
import time

import pytest

from forge_core import comfy_supervisor as cs

pytestmark = pytest.mark.skipif(os.name != "nt", reason="scripts .ps1 (Windows)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(cond, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.3)
    return False


def test_supervisor_starts_and_restarts(tmp_path, monkeypatch):
    port = _free_port()
    script = tmp_path / "fake.ps1"
    script.write_text(f'param([int]$Port)\n& "{sys.executable}" -m http.server $Port --bind 127.0.0.1\n', encoding="utf-8")
    inst = cs.Instance("fake", port, str(script), "falsa")
    monkeypatch.setattr(cs, "INSTANCES", [inst])
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    logs = []
    sup = cs.ComfySupervisor(log=logs.append, grace=20, down_after=1, min_restart=1)
    sup.run_in_thread()
    try:
        assert _wait(lambda: cs.port_open(port)), logs          # subiu
        assert any("iniciando" in m for m in logs)
        # derruba a instância: o supervisor deve subir de novo
        os.system(f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {port} -State Listen | '
                  f'ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"')
        assert _wait(lambda: not cs.port_open(port), 10)
        assert _wait(lambda: cs.port_open(port)), logs          # voltou
        assert sum("iniciando" in m for m in logs) >= 2
    finally:
        sup.stop_event.set()
        time.sleep(6)
    assert _wait(lambda: not cs.port_open(port), 15)            # Ctrl+C encerra o que ele iniciou


def test_adopts_running_instance(monkeypatch):
    port = _free_port()
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen()
    try:
        inst = cs.Instance("ext", port, "nao-existe.ps1", "externa")
        monkeypatch.setattr(cs, "INSTANCES", [inst])
        monkeypatch.setattr(cs.Instance, "installed", property(lambda self: True))
        logs = []
        sup = cs.ComfySupervisor(log=logs.append)
        sup.run_in_thread()
        assert _wait(lambda: any("monitorando" in m for m in logs), 5)
        sup.stop_event.set()
        time.sleep(1)
        assert not any("iniciando" in m for m in logs)          # não mexe no que já roda
    finally:
        srv.close()


def _fake(tmp_path, monkeypatch) -> cs.Instance:
    port = _free_port()
    script = tmp_path / "fake.ps1"
    script.write_text(f'param([int]$Port)\n& "{sys.executable}" -m http.server $Port --bind 127.0.0.1\n', encoding="utf-8")
    inst = cs.Instance("fake", port, str(script), "falsa")
    monkeypatch.setattr(cs, "INSTANCES", [inst])
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    return inst


def test_stop_from_options_is_respected_by_supervisor(tmp_path, monkeypatch):
    inst = _fake(tmp_path, monkeypatch)
    logs = []
    sup = cs.ComfySupervisor(log=logs.append, grace=20, down_after=1, min_restart=1)
    sup.run_in_thread()
    try:
        assert _wait(lambda: cs.port_open(inst.port)), logs
        assert _wait(lambda: cs.supervisor_info() is not None, 10)          # batimento
        assert cs.listener_pid(inst.port)
        cs.update_control("fake", autostart=False)                           # "Parar" nas Opções
        assert cs.stop(inst)
        time.sleep(8)
        assert not cs.port_open(inst.port)                                   # não voltou
        assert cs.supervisor_info()["instances"]["fake"] == "parada"
        cs.update_control("fake", autostart=True)                            # "Iniciar"/manter no ar
        assert _wait(lambda: cs.port_open(inst.port)), logs
    finally:
        sup.stop_event.set()
        time.sleep(6)
        cs.stop(inst)


def test_options_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge_core.options_server import build_router

    inst = _fake(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(build_router("agora", "boot", lambda: [], lambda: 3, lambda: {}))
    c = TestClient(app)
    try:
        assert c.get("/api/system").json()["pipelines"] == 3
        [d] = c.get("/api/comfy/instances").json()
        assert (d["name"], d["listening"], d["autostart"]) == ("fake", False, True)
        assert c.post("/api/comfy/instances/fake/start").json() == {"started": True}
        assert _wait(lambda: cs.port_open(inst.port))
        assert cs.held("fake")                                               # supervisor não interfere ao subir
        assert c.get("/api/comfy/instances/fake/log").json()["text"]
        assert c.post("/api/comfy/instances/fake/free").status_code == 502   # não é um ComfyUI de verdade
        assert c.post("/api/comfy/instances/fake/stop").json() == {"stopped": True}
        assert not cs.port_open(inst.port) and not cs.autostart("fake")
        assert c.put("/api/comfy/instances/fake/autostart", json={"value": True}).json()["autostart"] is True
        assert c.get("/api/comfy/instances/nada").status_code == 404
    finally:
        cs.stop(inst)

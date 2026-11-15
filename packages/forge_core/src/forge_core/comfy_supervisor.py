"""Sobe e monitora as instâncias do ComfyUI usadas pelo Asset Forge (um comando para tudo).

Instâncias (cada uma com dependências incompatíveis entre si, por isso processos separados):
    desktop  :8188  scripts/comfy-desktop.ps1   Comfy Desktop — 2D (Z-Image, Qwen Edit, Wan, SeedVR2, BiRefNet)
    3d       :8189  comfy/start.ps1             Hunyuan3D 2.1 + paint
    trellis  :8190  comfy/start_trellis.ps1     TRELLIS.2

Instâncias já rodando (iniciadas por outra pessoa/sessão) são adotadas: só monitoradas. Se uma cair
(porta fechada por ~30 s), o supervisor a sobe de novo, com intervalo mínimo entre tentativas.
Logs em <raiz>/.logs/comfy-<nome>.log. Ctrl+C encerra só as instâncias que este supervisor iniciou.

Controle pela tela de Opções (o servidor web é outro processo): `.logs/comfy-control.json` diz quais instâncias o
supervisor deve deixar paradas (`autostart: false`) e até quando não mexer numa que o servidor está (re)iniciando
(`hold_until`). O supervisor grava `.logs/comfy-supervisor.json` (pid e estado de cada instância) a cada volta.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(__file__).resolve().parents[4]


@dataclass
class Instance:
    name: str
    port: int
    script: str           # relativo à raiz do repositório
    title: str
    installed_check: str | None = None  # caminho que precisa existir (instalação feita)
    install_hint: str = ""

    @property
    def script_path(self) -> Path:
        return ROOT / self.script

    @property
    def installed(self) -> bool:
        if not self.script_path.exists():
            return False
        if self.installed_check is None:
            return True
        return Path(os.path.expandvars(self.installed_check)).exists() if "%" in self.installed_check \
            else (ROOT / self.installed_check).exists()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


INSTANCES = [
    Instance("desktop", 8188, "scripts/comfy-desktop.ps1", "Comfy Desktop (2D)",
             "%LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Installs/ComfyUI/standalone-env/python.exe",
             "Instale o Comfy Desktop (comfy.org/download) e abra-o uma vez para concluir a instalação."),
    Instance("3d", 8189, "comfy/start.ps1", "Hunyuan3D (3D)", "comfy/instance/ComfyUI/.venv/Scripts/python.exe",
             "powershell -ExecutionPolicy Bypass -File comfy\\install.ps1"),
    Instance("trellis", 8190, "comfy/start_trellis.ps1", "TRELLIS.2 (3D)", "comfy/instance-trellis/ComfyUI/.venv/Scripts/python.exe",
             "powershell -ExecutionPolicy Bypass -File comfy\\install_trellis.ps1"),
]


def get(name: str) -> Instance:
    inst = next((i for i in INSTANCES if i.name == name), None)
    if inst is None:
        raise KeyError(f"instância desconhecida: {name} (há: {[i.name for i in INSTANCES]})")
    return inst


def port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def ready(inst: Instance) -> bool:
    try:
        return httpx.get(f"{inst.url}/system_stats", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def status() -> list[dict]:
    return [{"name": i.name, "port": i.port, "title": i.title, "installed": i.installed,
             "listening": port_open(i.port), "ready": ready(i), "script": i.script} for i in INSTANCES]


# ---------------------------------------------------------------- controle compartilhado (Opções <-> supervisor)

CONTROL = "comfy-control.json"
HEARTBEAT = "comfy-supervisor.json"


def _logs_dir() -> Path:
    d = ROOT / ".logs"
    d.mkdir(exist_ok=True)
    return d


def _write_json(name: str, data: dict) -> None:
    tmp = _logs_dir() / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _logs_dir() / name)


def read_control() -> dict[str, dict]:
    try:
        data = json.loads((_logs_dir() / CONTROL).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_control(name: str, **fields) -> dict:
    data = read_control()
    data[name] = {**data.get(name, {}), **fields}
    _write_json(CONTROL, data)
    return data[name]


def autostart(name: str) -> bool:
    """O supervisor mantém esta instância no ar? (desligado ao parar pelas Opções)"""
    return bool(read_control().get(name, {}).get("autostart", True))


def held(name: str) -> bool:
    """O servidor web está (re)iniciando esta instância agora: o supervisor não deve mexer."""
    return time.time() < float(read_control().get(name, {}).get("hold_until", 0) or 0)


def supervisor_info(max_age: float = 30.0) -> dict | None:
    """Batimento do supervisor do `forge up`; None se não há um ativo (ou é de uma versão sem batimento)."""
    try:
        hb = json.loads((_logs_dir() / HEARTBEAT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return hb if time.time() - float(hb.get("ts", 0)) <= max_age else None


# ---------------------------------------------------------------- processos

def listener_pid(port: int) -> int | None:
    """PID do processo escutando na porta."""
    try:
        if os.name == "nt":
            # bytes + decodificação tolerante: a saída vem na página de código do console (ex.: "Endereço" em cp850)
            raw = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=10).stdout or b""
            out = raw.decode("ascii", errors="replace")
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and re.search(rf"[:.]{port}$", parts[1]):
                    return int(parts[4])
            return None
        out = (subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, timeout=10).stdout or b"").decode(errors="replace")
        return int(out.split()[0]) if out.split() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _process_chain(pid: int) -> list[int]:
    """pid + ancestrais do mesmo ComfyUI (lançador python do venv e o powershell do script), de baixo para cima."""
    if os.name != "nt":
        return [pid]
    ps = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    try:
        raw = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30).stdout or b"[]"
        procs = json.loads(raw.decode("utf-8", errors="replace") or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return [pid]
    by_pid = {p["ProcessId"]: p for p in procs if isinstance(p, dict)}
    chain, cur = [pid], by_pid.get(pid)
    for _ in range(4):
        parent = by_pid.get(cur.get("ParentProcessId")) if cur else None
        if not parent:
            break
        name = (parent.get("Name") or "").lower()
        cmd = (parent.get("CommandLine") or "").lower()
        if (name in ("python.exe", "pythonw.exe") and "main.py" in cmd) or (name == "powershell.exe" and "comfy" in cmd):
            chain.append(parent["ProcessId"])
            cur = parent
        else:
            break
    return chain


def stop(inst: Instance, timeout: float = 30.0) -> bool:
    """Encerra a instância que está na porta (mesmo se outra sessão a iniciou). True se a porta fechou."""
    pid = listener_pid(inst.port)
    if pid is None:
        return not port_open(inst.port)
    for p in reversed(_process_chain(pid)):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
        else:
            try:
                os.kill(p, 15)
            except OSError:
                pass
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not port_open(inst.port, 0.3):
            return True
        time.sleep(0.5)
    return False


def log_tail(inst: Instance, lines: int = 200) -> str:
    path = _log_path(inst)
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 256 * 1024))
        data = f.read().decode("utf-8", "replace")
    data = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data)  # cores ANSI do ComfyUI
    return "\n".join(data.splitlines()[-lines:])


def details(inst: Instance) -> dict:
    """Estado completo para a tela de Opções: porta, versão, GPU/VRAM, fila, PID e controle."""
    d: dict = {"name": inst.name, "port": inst.port, "title": inst.title, "url": inst.url, "script": inst.script,
               "installed": inst.installed, "install_hint": inst.install_hint, "listening": port_open(inst.port, 0.5),
               "ready": False, "autostart": autostart(inst.name), "starting": held(inst.name), "log": str(_log_path(inst))}
    if not d["listening"]:
        return d
    try:
        with httpx.Client(base_url=inst.url, timeout=3) as c:
            st = c.get("/system_stats").json()
            d["ready"] = True
            sysi = st.get("system", {})
            d["system"] = {k: sysi.get(k) for k in ("comfyui_version", "python_version", "pytorch_version", "ram_total", "ram_free")}
            d["devices"] = [{k: dev.get(k) for k in ("name", "type", "vram_total", "vram_free")} for dev in st.get("devices", [])]
            q = c.get("/queue").json()
            d["queue"] = {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}
        if d["starting"]:   # subiu: o supervisor volta a vigiar já, sem esperar o fim da carência
            update_control(inst.name, hold_until=0)
            d["starting"] = False
    except (httpx.HTTPError, ValueError):
        pass
    d["pid"] = listener_pid(inst.port)
    return d


def _log_path(inst: Instance) -> Path:
    return _logs_dir() / f"comfy-{inst.name}.log"


def start(inst: Instance, detached: bool = False) -> subprocess.Popen:
    log = open(_log_path(inst), "ab")
    log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} iniciando {inst.name} ====\n".encode())
    log.flush()
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | (subprocess.CREATE_NO_WINDOW if detached else 0)  # console oculto próprio: sobrevive ao servidor
    return subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(inst.script_path), "-Port", str(inst.port)],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()


@dataclass
class _State:
    proc: subprocess.Popen | None = None
    adopted: bool = False
    down_since: float | None = None
    last_start: float = 0.0
    starting_until: float = 0.0
    status: str = "?"


@dataclass
class ComfySupervisor:
    names: list[str] | None = None
    log: Callable[[str], None] = print
    grace: float = 240.0       # tempo para uma instância recém-iniciada abrir a porta
    down_after: float = 30.0   # porta fechada por este tempo = caiu
    min_restart: float = 60.0
    stop_event: threading.Event = field(default_factory=threading.Event)

    def instances(self) -> list[Instance]:
        return [i for i in INSTANCES if (not self.names or i.name in self.names)]

    def _heartbeat(self, states: dict[str, _State]) -> None:
        try:
            _write_json(HEARTBEAT, {"pid": os.getpid(), "ts": time.time(), "instances": {n: st.status for n, st in states.items()}})
        except OSError:
            pass

    def run(self) -> None:
        states = {i.name: _State() for i in self.instances()}
        for i in self.instances():
            if not i.installed:
                states[i.name].status = "não instalada"
                self.log(f"[comfy] {i.name} ({i.title}) não instalada ({i.script}); ignorando")
            elif port_open(i.port):
                states[i.name].adopted = True
                states[i.name].status = "ok"
                self.log(f"[comfy] {i.name} já rodando em :{i.port} — monitorando")
        while not self.stop_event.is_set():
            now = time.monotonic()
            for i in self.instances():
                st = states[i.name]
                if not i.installed:
                    continue
                up = port_open(i.port)
                if not autostart(i.name) or held(i.name):
                    # parada pelas Opções, ou o servidor web está (re)iniciando: não mexe
                    st.status = "ok" if up else ("parada" if not autostart(i.name) else "iniciando")
                    st.down_since = None
                    continue
                if up:
                    if st.status != "ok":
                        self.log(f"[comfy] {i.name} online em :{i.port}")
                    st.status, st.down_since = "ok", None
                    continue
                if now < st.starting_until:
                    continue  # ainda subindo
                st.down_since = st.down_since or now
                if now - st.down_since < self.down_after and st.status == "ok":
                    continue  # pode ser um soluço
                if now - st.last_start < self.min_restart:
                    continue
                if st.proc is not None:
                    kill_tree(st.proc)
                self.log(f"[comfy] {i.name} fora do ar — iniciando ({i.script}); log: {_log_path(i)}")
                st.proc, st.adopted = start(i), False
                st.last_start, st.starting_until, st.status = now, now + self.grace, "iniciando"
            self._heartbeat(states)
            self.stop_event.wait(5)
        for i in self.instances():
            st = states[i.name]
            if st.proc is not None and not st.adopted:
                self.log(f"[comfy] encerrando {i.name}")
                kill_tree(st.proc)

    def run_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="comfy-supervisor", daemon=True)
        t.start()
        return t

"""Cliente mínimo da API do ComfyUI (formato de workflow "API").

Templates: qualquer valor string igual a "{{nome}}" nos inputs do grafo é substituído por
`values["nome"]` (mantendo o tipo do valor). Uploads: `uploads={"nome": Path}` sobe o arquivo
para input/forge/ e injeta o nome resultante no placeholder "{{nome}}".
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

ProgressFn = Callable[[float, str | None], None]


class ComfyError(RuntimeError):
    pass


@dataclass
class ComfyOutput:
    node_id: str
    filename: str
    subfolder: str
    type: str
    path: Path | None = None  # preenchido quando salvo em out_dir
    data: bytes | None = None


def load_workflow(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fill(graph: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Substitui placeholders "{{chave}}" em inputs. Levanta erro se sobrar placeholder sem valor."""
    g = copy.deepcopy(graph)
    missing: set[str] = set()
    for node in g.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        for k, v in list(inputs.items()):
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                key = v[2:-2].strip()
                if key in values:
                    inputs[k] = values[key]
                else:
                    missing.add(key)
    if missing:
        raise ComfyError(f"placeholders sem valor no workflow: {sorted(missing)}")
    return g


def find_nodes(graph: dict[str, Any], class_type: str | None = None, title: str | None = None) -> list[str]:
    out = []
    for nid, node in graph.items():
        if class_type and node.get("class_type") != class_type:
            continue
        if title and node.get("_meta", {}).get("title") != title:
            continue
        out.append(nid)
    return out


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188", timeout: float = 30.0,
                 cancel_event: threading.Event | None = None):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.http = httpx.Client(base_url=self.url, timeout=timeout)
        self.cancel_event = cancel_event or threading.Event()
        # transforma o grafo antes do envio (ex.: prompts de segurança ocultos em projetos +18)
        self.graph_filter: Any = None
        # projeto/pipeline/asset do job, gravados com cada workflow enviado (menu Workflows)
        self.meta: dict[str, Any] = {}

    # ---- estado --------------------------------------------------------
    def ping(self) -> bool:
        try:
            return self.http.get("/system_stats", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def system_stats(self) -> dict[str, Any]:
        return self._json(self.http.get("/system_stats"))

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        return self._json(self.http.get(f"/object_info/{node_class}" if node_class else "/object_info"))

    def models(self, folder: str) -> list[str]:
        """Lista arquivos de uma pasta de modelos (checkpoints, loras, diffusion_models, ...)."""
        try:
            return self._json(self.http.get(f"/models/{folder}"))
        except ComfyError:
            return []

    def interrupt(self) -> None:
        try:
            self.http.post("/interrupt")
        except httpx.HTTPError:
            pass

    # ---- arquivos ------------------------------------------------------
    def upload_image(self, path: str | Path, subfolder: str = "forge", overwrite: bool = True) -> str:
        path = Path(path)
        name = f"{uuid.uuid4().hex[:8]}_{path.name}"
        with path.open("rb") as f:
            r = self.http.post("/upload/image", files={"image": (name, f, "application/octet-stream")},
                               data={"subfolder": subfolder, "type": "input", "overwrite": str(overwrite).lower()})
        d = self._json(r)
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def download(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = self.http.get("/view", params={"filename": filename, "subfolder": subfolder, "type": type_}, timeout=120)
        if r.status_code != 200:
            raise ComfyError(f"falha ao baixar {filename}: HTTP {r.status_code}")
        return r.content

    # ---- execução ------------------------------------------------------
    def queue(self, graph: dict[str, Any]) -> str:
        from .comfy_workflows import record

        try:
            r = self.http.post("/prompt", json={"prompt": graph, "client_id": self.client_id})
        except httpx.HTTPError as e:
            record(self.url, graph, prompt_id=None, meta=self.meta, error=f"ComfyUI inacessível: {e}")
            raise
        if r.status_code != 200:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            msg = f"ComfyUI rejeitou o workflow: {json.dumps(detail, ensure_ascii=False)[:2000]}"
            record(self.url, graph, prompt_id=None, meta=self.meta, error=msg)
            raise ComfyError(msg)
        pid = r.json()["prompt_id"]
        record(self.url, graph, prompt_id=pid, meta=self.meta)
        return pid

    def wait(self, prompt_id: str, on_progress: ProgressFn | None = None, timeout: float = 1800) -> dict[str, Any]:
        """Acompanha via websocket (progresso por nó); cai para polling de /history se o ws falhar."""
        try:
            self._wait_ws(prompt_id, on_progress, timeout)
        except ComfyError:
            raise
        except Exception:
            self._wait_poll(prompt_id, timeout)
        return self._history(prompt_id)

    def _wait_ws(self, prompt_id: str, on_progress: ProgressFn | None, timeout: float) -> None:
        from websockets.sync.client import connect

        u = urlparse(self.url)
        ws_url = f"{'wss' if u.scheme == 'https' else 'ws'}://{u.netloc}/ws?clientId={self.client_id}"
        deadline = time.monotonic() + timeout
        with connect(ws_url, open_timeout=5, max_size=None) as ws:
            # o prompt pode ter terminado antes de conectarmos
            if self._history(prompt_id, allow_missing=True):
                return
            while time.monotonic() < deadline:
                if self.cancel_event.is_set():
                    self.interrupt()
                    raise ComfyError("cancelado")
                try:
                    msg = ws.recv(timeout=1.0)
                except TimeoutError:
                    if self._history(prompt_id, allow_missing=True):
                        return
                    continue
                if isinstance(msg, bytes):  # previews binários
                    continue
                m = json.loads(msg)
                data = m.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                t = m.get("type")
                if t == "progress" and on_progress and data.get("max"):
                    on_progress(data["value"] / data["max"], f"nó {data.get('node')}")
                elif t == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                    return
                elif t == "execution_success":
                    return
                elif t == "execution_error":
                    raise ComfyError(f"erro no nó {data.get('node_type')}: {data.get('exception_message')}")
                elif t == "execution_interrupted":
                    raise ComfyError("execução interrompida")
        raise ComfyError("timeout aguardando ComfyUI")

    def _wait_poll(self, prompt_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel_event.is_set():
                self.interrupt()
                raise ComfyError("cancelado")
            if self._history(prompt_id, allow_missing=True):
                return
            time.sleep(0.5)
        raise ComfyError("timeout aguardando ComfyUI")

    def _history(self, prompt_id: str, allow_missing: bool = False) -> dict[str, Any]:
        h = self._json(self.http.get(f"/history/{prompt_id}"))
        entry = h.get(prompt_id)
        if not entry:
            if allow_missing:
                return {}
            raise ComfyError(f"prompt {prompt_id} sem histórico")
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
            detail = msgs[0][1].get("exception_message") if msgs else "erro desconhecido"
            raise ComfyError(f"workflow falhou: {detail}")
        if not status.get("completed", True) and allow_missing:
            return {}
        return entry

    def run_workflow(self, graph: dict[str, Any], uploads: dict[str, str | Path] | None = None,
                     values: dict[str, Any] | None = None, out_dir: str | Path | None = None,
                     on_progress: ProgressFn | None = None, output_nodes: list[str] | None = None,
                     prefix: str = "out") -> list[ComfyOutput]:
        """Preenche, envia, aguarda e baixa as saídas (imagens/arquivos) do workflow."""
        vals = dict(values or {})
        for key, p in (uploads or {}).items():
            vals[key] = self.upload_image(p)
        g = fill(graph, vals)
        if self.graph_filter is not None:
            g = self.graph_filter(g)
        pid = self.queue(g)
        entry = self.wait(pid, on_progress)
        outs: list[ComfyOutput] = []
        for nid, node_out in entry.get("outputs", {}).items():
            if output_nodes and nid not in output_nodes:
                continue
            for key in ("images", "gifs", "files", "3d", "meshes", "audio"):
                for item in node_out.get(key, []) or []:
                    if not isinstance(item, dict) or "filename" not in item:
                        continue
                    if item.get("type") == "temp" and key == "images" and not output_nodes:
                        continue  # previews
                    outs.append(ComfyOutput(nid, item["filename"], item.get("subfolder", ""), item.get("type", "output")))
        if out_dir is not None:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, o in enumerate(outs):
                o.data = self.download(o.filename, o.subfolder, o.type)
                suffix = Path(o.filename).suffix or ".png"
                o.path = out_dir / (f"{prefix}{suffix}" if len(outs) == 1 else f"{prefix}_{i + 1:02d}{suffix}")
                o.path.write_bytes(o.data)
        return outs

    @staticmethod
    def _json(r: httpx.Response) -> Any:
        if r.status_code != 200:
            raise ComfyError(f"HTTP {r.status_code}: {r.text[:500]}")
        return r.json()

    def close(self) -> None:
        self.http.close()

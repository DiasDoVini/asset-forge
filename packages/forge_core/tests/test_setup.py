import http.server
import threading
import time
from functools import partial

import pytest

from forge_core import local_llm, setup_catalog, user_settings
from forge_core.config import get_settings
from forge_core.setup_tasks import TaskManager


class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serve arquivos com suporte a Range (para testar a retomada)."""

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        path = self.translate_path(self.path)
        data = open(path, "rb").read()
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            body = data[start:]
        else:
            self.send_response(200)
            body = data
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        data = open(self.translate_path(self.path), "rb").read()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()


@pytest.fixture
def server(tmp_path):
    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()
    payload = bytes(range(256)) * 4096   # 1 MiB
    (srv_dir / "model.bin").write_bytes(payload)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_RangeHandler, directory=str(srv_dir)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/model.bin", payload
    httpd.shutdown()


def _wait(task, timeout=20):
    end = time.monotonic() + timeout
    while task.status in ("queued", "running") and time.monotonic() < end:
        time.sleep(0.05)
    return task


def test_download_resumes_from_part(tmp_path, server):
    url, payload = server
    dest = tmp_path / "models" / "vae" / "model.bin"
    dest.parent.mkdir(parents=True)
    (dest.parent / "model.bin.part").write_bytes(payload[:300_000])     # download interrompido
    tm = TaskManager()
    t = _wait(tm.download_files("x", "X", [(url, dest)]))
    assert t.status == "done", list(t.log)
    assert dest.read_bytes() == payload and not (dest.parent / "model.bin.part").exists()
    assert t.total == len(payload) and t.done == len(payload)
    # de novo: nada a baixar
    assert "nada a baixar" in _wait(tm.download_files("x", "X", [(url, dest)])).message


def test_catalog_states_and_mismatch_resume(tmp_path, server, monkeypatch):
    url, payload = server
    monkeypatch.setenv("FORGE_MODELS_DIR", str(tmp_path / "models"))
    f_ok = setup_catalog.ModelFile("vae", "ok.bin", url, len(payload))
    f_cut = setup_catalog.ModelFile("diffusion_models", "cut.bin", url, len(payload))
    item = setup_catalog.Item("t", "models", "Teste", "", files=[f_ok, f_cut])
    monkeypatch.setattr(setup_catalog, "ITEMS", [item])
    monkeypatch.setattr(setup_catalog, "_llm_items", lambda: [])
    root = setup_catalog.models_dir()
    assert setup_catalog.get("t").status(root)["state"] == "missing"
    (root / "vae").mkdir(parents=True)
    (root / "vae" / "ok.bin").write_bytes(payload)
    (root / "diffusion_models").mkdir()
    (root / "diffusion_models" / "cut.bin").write_bytes(payload[:1000])   # truncado, sem .part
    st = setup_catalog.overview()["items"][0]
    assert st["state"] == "partial" and "cut.bin" in st["detail"]
    assert [f["state"] for f in st["files"]] == ["ok", "mismatch"]
    t = _wait(setup_catalog.install(TaskManager(), "t"))
    assert t.status == "done", list(t.log)
    assert (root / "diffusion_models" / "cut.bin").read_bytes() == payload
    assert setup_catalog.get("t").status(root)["state"] == "ok"


def test_models_dir_from_comfy_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_MODELS_DIR", raising=False)
    monkeypatch.delenv("FORGE_COMFY_MODELS_DIR", raising=False)
    shared = tmp_path / "shared"
    shared.mkdir()
    y = tmp_path / "comfy/instance/ComfyUI/extra_model_paths.yaml"
    y.parent.mkdir(parents=True)
    y.write_text(f"x:\n  base_path: '{shared}'\n  is_default: true\n", encoding="utf-8")
    monkeypatch.setattr(setup_catalog, "ROOT", tmp_path)
    assert setup_catalog.models_dir() == shared


def test_every_catalog_item_describes(monkeypatch):
    monkeypatch.setattr(setup_catalog, "_llm_items", lambda: [])
    ov = setup_catalog.overview()
    ids = [i["id"] for i in ov["items"]]
    assert len(ids) == len(set(ids)) and {"ollama", "ffmpeg", "models-image", "models-audio", "comfy-3d"} <= set(ids)
    for it in ov["items"]:
        assert it["state"] in ("ok", "missing", "partial", "manual")
        for f in it.get("files", []):
            assert f["url"].startswith("https://huggingface.co/") and f["size"] > 0


def test_local_llm_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path / "projects"))
    get_settings.cache_clear()
    monkeypatch.delenv("FORGE_LOCAL_LLM", raising=False)
    try:
        monkeypatch.setattr(local_llm, "models", lambda: ["llama3:8b", "huihui_ai/qwen3-abliterated:8b", "qwen3.8:27b"])
        assert local_llm.pick_model() == "qwen3.8:27b"                       # catálogo: Qwen3.8 primeiro
        assert local_llm.pick_model(nsfw=True) == "huihui_ai/qwen3-abliterated:8b"
        user_settings.update("local_llm", model="llama3:8b")                # escolhido nas Opções
        assert local_llm.pick_model() == "llama3:8b" and local_llm.pick_model(nsfw=True) == "llama3:8b"
        assert (tmp_path / "settings.json").exists()
        user_settings.update("local_llm", model="nao-baixado:1b")
        with pytest.raises(local_llm.LocalLLMError, match="não está baixado"):
            local_llm.pick_model()
        user_settings.update("local_llm", model=None)
        assert user_settings.get("local_llm", "model") is None
        monkeypatch.setattr(local_llm, "models", lambda: ["qwen3.8"])          # sem tag = :latest
        assert local_llm.pick_model() == "qwen3.8"
    finally:
        get_settings.cache_clear()


def test_setup_routes(tmp_path, server, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge_core.setup_server import build_router

    url, payload = server
    monkeypatch.setenv("FORGE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(setup_catalog, "ITEMS", [setup_catalog.Item("t", "models", "Teste", "", files=[
        setup_catalog.ModelFile("vae", "a.bin", url, len(payload))])])
    monkeypatch.setattr(setup_catalog, "_llm_items", lambda: [])
    app = FastAPI()
    app.include_router(build_router())
    c = TestClient(app)
    assert c.get("/api/setup").json()["items"][0]["state"] == "missing"
    tid = c.post("/api/setup/items/t/install").json()["id"]
    end = time.monotonic() + 20
    while c.get(f"/api/setup/tasks/{tid}").json()["status"] in ("queued", "running") and time.monotonic() < end:
        time.sleep(0.05)
    assert c.get(f"/api/setup/tasks/{tid}").json()["status"] == "done"
    assert c.get("/api/setup").json()["items"][0]["state"] == "ok"
    assert c.post("/api/setup/items/nada/install").status_code == 404
    assert c.delete("/api/setup/tasks").json()["cleared"] == 1

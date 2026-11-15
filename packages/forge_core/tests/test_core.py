import asyncio
import json

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from forge_core.config import Settings
from forge_core.pipeline import Modality, OutputFile, Pipeline, PipelineParams, Registry, set_registry
from forge_core.project import Project, ProjectRegistry
from forge_core.runner import PipelineError, run_pipeline


class DrawParams(PipelineParams):
    prompt: str
    color: str = "#ff0000"


class Draw(Pipeline):
    id = "fake.draw"
    title = "Desenha um quadrado"
    resources = {"cpu"}
    Params = DrawParams

    def run(self, ctx, params):
        ctx.progress(0.5, "desenhando")
        ctx.log(f"style={ctx.style()['prompt']}")
        out = ctx.version_dir / "image.png"
        Image.new("RGBA", (8, 8), params.color).save(out)
        ctx.meta["seed"] = 42
        return [OutputFile(out, "primary")]


class Invert(Pipeline):
    id = "fake.invert"
    resources = {"gpu"}
    requires_input = True
    same_asset = True

    def run(self, ctx, params):
        src = ctx.input_files("primary")[0]
        out = ctx.version_dir / "image.png"
        im = Image.open(src).convert("RGB")
        Image.eval(im, lambda v: 255 - v).save(out)
        return [OutputFile(out)]


class Boom(Pipeline):
    id = "fake.boom"
    resources = {"cpu"}

    def run(self, ctx, params):
        (ctx.version_dir / "partial.txt").write_text("x")
        raise RuntimeError("falhou")


@pytest.fixture(autouse=True)
def registry():
    reg = Registry()
    reg.add(Modality(name="fake", pipelines=[Draw(), Invert(), Boom()]))
    set_registry(reg)
    yield reg
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path / "meu-jogo", name="Meu Jogo")
    p.set_value("style.prompt", "pixel art 16-bit")
    p.set_value("fake.size", 32)
    return p


def test_project_roundtrip(project):
    again = Project.load(project.root)
    assert again.config.style.prompt == "pixel art 16-bit"
    assert again.config.modality("fake") == {"size": 32}
    style = again.style_for("fake")
    assert style["prompt"] == "pixel art 16-bit" and style["size"] == 32


def test_run_and_versions(project):
    av = run_pipeline(project, "fake.draw", {"prompt": "Espada de Gelo"})
    assert av.ref == "espada-de-gelo@v001"
    assert av.seed == 42 and av.modality == "fake" and av.prompt == "Espada de Gelo"
    # derivação cria nova versão do mesmo asset, com parent
    av2 = run_pipeline(project, "fake.invert", {"input_asset": "espada-de-gelo"})
    assert av2.ref == "espada-de-gelo@v002" and av2.parent == "espada-de-gelo@v001"
    px = Image.open(project.store.files_of(av2)[0]).getpixel((0, 0))
    assert px == (0, 255, 255)
    # novo asset com mesmo prompt ganha slug único
    av3 = run_pipeline(project, "fake.draw", {"prompt": "Espada de Gelo"})
    assert av3.slug == "espada-de-gelo-2"
    rows = project.store.search()
    assert {r["ref"] for r in rows} == {"espada-de-gelo@v002", "espada-de-gelo-2@v001"}
    assert len(project.store.index.search(q="gelo", all_versions=True)) == 3


def test_failure_cleans_up(project):
    with pytest.raises(RuntimeError):
        run_pipeline(project, "fake.boom", {})
    assert project.store.slugs() == []


def test_invalid_params(project):
    with pytest.raises(PipelineError):
        run_pipeline(project, "fake.draw", {"nope": 1})
    with pytest.raises(PipelineError):
        run_pipeline(project, "fake.invert", {})


def test_import_and_delete(project, tmp_path):
    src = tmp_path / "x.png"
    Image.new("RGB", (4, 4)).save(src)
    av = project.store.import_file(src, slug="barril")
    assert av.ref == "barril@v001" and av.files[0].mime == "image/png"
    av = project.store.update_meta(av.ref, tags=["prop"])
    assert project.store.search(tag="prop")[0]["slug"] == "barril"
    project.store.delete("barril")
    assert project.store.slugs() == [] and project.store.search() == []
    assert project.store.reindex() == 0


def test_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path))
    from forge_core.config import get_settings
    from forge_core.cli import app

    get_settings.cache_clear()
    r = CliRunner()
    res = r.invoke(app, ["project", "new", "demo", "--style", "cartoon", "--json"])
    assert res.exit_code == 0, res.output
    res = r.invoke(app, ["run", "fake.draw", "-p", "demo", "-P", "prompt=barril", "-P", 'color="#00ff00"', "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ref"] == "barril@v001"
    res = r.invoke(app, ["asset", "list", "-p", "demo", "--json"])
    assert json.loads(res.output)[0]["slug"] == "barril"
    res = r.invoke(app, ["run", "fake.draw", "-p", "demo", "--json"])
    assert res.exit_code == 1 and "error" in json.loads(res.output)
    get_settings.cache_clear()


def test_server_jobs(tmp_path):
    from forge_core.server import create_app

    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)
    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        assert c.post("/api/projects", json={"slug": "demo", "style": {"prompt": "x"}}).status_code == 201
        assert c.get("/api/modalities").json()[0]["pipelines"][0]["id"] == "fake.draw"
        with c.websocket_connect("/api/events") as ws:
            job = c.post("/api/jobs", json={"project": "demo", "pipeline": "fake.draw", "params": {"prompt": "caixa"}}).json()
            seen = None
            for _ in range(20):
                ev = ws.receive_json()
                if ev["type"] == "job" and ev["job"]["status"] in ("done", "error"):
                    seen = ev["job"]
                    break
        assert seen and seen["status"] == "done", seen
        assets = c.get("/api/projects/demo/assets").json()
        assert assets[0]["ref"] == "caixa@v001"
        f = c.get(assets[0]["primary_url"])
        assert f.status_code == 200 and f.headers["content-type"] == "image/png"
        assert c.get("/files/demo/../../etc").status_code in (400, 404)
        bad = c.post("/api/jobs", json={"project": "demo", "pipeline": "nao.existe"})
        assert bad.status_code == 400


def _wait_job(c, job_id, tries=600):
    import time

    for _ in range(tries):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job não terminou")


def test_job_history_survives_restart_and_retry(tmp_path, monkeypatch):
    from forge_core.server import create_app

    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path))
    from forge_core.config import get_settings

    get_settings.cache_clear()
    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)
    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        c.post("/api/projects", json={"slug": "demo"})
        ok = c.post("/api/jobs", json={"project": "demo", "pipeline": "fake.draw", "params": {"prompt": "caixa"}}).json()
        bad = c.post("/api/jobs", json={"project": "demo", "pipeline": "fake.boom", "params": {}}).json()
        assert _wait_job(c, ok["id"])["status"] == "done"
        assert _wait_job(c, bad["id"])["status"] == "error"

    # "reinício": novo app, mesmo disco
    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        failed = c.get("/api/jobs", params={"status": "error,cancelled"}).json()
        assert [j["id"] for j in failed] == [bad["id"]] and "falhou" in failed[0]["error"]
        assert len(c.get("/api/jobs", params={"project": "demo"}).json()) == 2
        r = c.post(f"/api/jobs/{ok['id']}/retry", json={"params": {"color": "#00ff00"}, "new_seed": True}).json()
        assert r["retry_of"] == ok["id"] and r["request"]["params"]["color"] == "#00ff00"
        assert _wait_job(c, r["id"])["result"] == "caixa-2@v001"  # sem asset de destino: asset novo
        assert c.delete(f"/api/jobs/{bad['id']}").status_code == 200
        assert c.delete("/api/jobs", params={"status": "done"}).json()["deleted"] == 2
        assert c.get("/api/jobs").json() == []

    # CLI também registra no histórico
    from forge_core.cli import app as cli

    res = CliRunner().invoke(cli, ["run", "fake.boom", "-p", "demo", "--json"])
    assert res.exit_code == 1
    res = CliRunner().invoke(cli, ["jobs", "--failed", "--json"])
    rows = json.loads(res.output)
    assert rows[0]["source"] == "cli" and rows[0]["pipeline"] == "fake.boom"
    get_settings.cache_clear()


def test_import_slug_from_folder_and_derived_edit(project, tmp_path):
    d = tmp_path / "Saint Germain"
    d.mkdir()
    src = d / "image.png"
    Image.new("RGB", (4, 4)).save(src)
    assert project.store.import_file(src).slug == "saint-germain"
    assert project.store.import_file(src, name="Concept").name == "Concept"
    # edição (same_asset) com `asset` novo: asset derivado separado, com base = entrada
    run_pipeline(project, "fake.draw", {"prompt": "concept"})
    av = run_pipeline(project, "fake.invert", {"input_asset": "concept"}, asset="concept-pose-2")
    assert av.ref == "concept-pose-2@v001" and av.parent == "concept@v001" and av.extra["base"] == "concept@v001"
    assert project.store.version_numbers("concept") == [1]
    # sem `asset`: continua sendo nova versão do mesmo asset
    assert run_pipeline(project, "fake.invert", {"input_asset": "concept"}).ref == "concept@v002"


class Slow(Pipeline):
    id = "fake.slow"
    resources = {"cpu"}

    def run(self, ctx, params):
        import time

        time.sleep(4)
        out = ctx.version_dir / "image.png"
        Image.new("RGB", (2, 2)).save(out)
        return [OutputFile(out)]


def test_admin_reload_guards(tmp_path, monkeypatch, registry):
    from forge_core.server import create_app

    registry.add(Modality(name="fake2", pipelines=[]))  # registro extra não interfere
    registry.pipelines["fake.slow"] = Slow()
    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)
    monkeypatch.delenv("FORGE_SUPERVISED", raising=False)
    exits = []
    monkeypatch.setattr("os._exit", lambda code: exits.append(code))  # nunca derrubar o pytest
    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        assert c.post("/api/admin/reload", json={}).status_code == 400  # sem supervisor
        h = c.get("/api/health").json()
        assert h["boot_id"] and h["supervised"] is False
        monkeypatch.setenv("FORGE_SUPERVISED", "1")
        c.post("/api/projects", json={"slug": "demo"})
        c.post("/api/jobs", json={"project": "demo", "pipeline": "fake.slow", "params": {}})
        r = c.post("/api/admin/reload", json={"timeout": 0.2})  # job ativo: recusa em vez de derrubar
        assert r.status_code == 409 and "busy" in r.json()["detail"]
        assert exits == []  # recusou: não agendou reinício

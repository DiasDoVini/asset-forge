import json
from types import SimpleNamespace

import pytest
from PIL import Image

from forge_core import critic as critic_mod
from forge_core.pipeline import Modality, OutputFile, Pipeline, PipelineParams, Registry, set_registry
from forge_core.project import Project
from forge_core.refine import (Action, ActionSpec, Brief, Preview, RefineEngine, Refiner, Review, load_session,
                               preview_from_image)


class GenP(PipelineParams):
    prompt: str = "x"
    color: str = "#ff0000"


class Gen(Pipeline):
    id = "fake.gen"
    resources = {"cpu"}
    Params = GenP

    def run(self, ctx, p):
        out = ctx.version_dir / "image.png"
        Image.new("RGB", (8, 8), p.color).save(out)
        return [OutputFile(out)]


class FixP(PipelineParams):
    color: str


class Fix(Pipeline):
    id = "fake.fix"
    resources = {"cpu"}
    requires_input = True
    same_asset = True
    Params = FixP

    def run(self, ctx, p):
        if p.color == "boom":
            raise RuntimeError("falhou de propósito")
        out = ctx.version_dir / "image.png"
        Image.new("RGB", (8, 8), p.color).save(out)
        ctx.meta["extra"] = {"from": ctx.input.ref}
        return [OutputFile(out)]


class R(Refiner):
    id = "fake.thing"
    default_criteria = ["ser verde"]

    def initial_action(self, brief):
        return Action(pipeline="fake.gen", params={"prompt": brief.prompt})

    def actions(self):
        return [ActionSpec("fake.gen", "do zero"), ActionSpec("fake.fix", "corrigir cor")]

    def previews(self, ctx):
        img = Image.open(ctx.project.store.files_of(ctx.asset, "primary")[0])
        return [preview_from_image(img, ctx, "p.png", "frente")]


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(Modality(name="fake", pipelines=[Gen(), Fix()], refiners=[R()]))
    set_registry(r)
    yield
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def _color(project, ref):
    return Image.open(project.store.files_of(project.store.get(ref), "primary")[0]).getpixel((0, 0))


def test_manual_loop(project):
    eng = RefineEngine(project)
    s = eng.create("fake.thing", Brief(prompt="algo verde", max_iterations=5), critic="manual", folder="Testes")
    s = eng.step(s.id)
    assert s.status == "awaiting_review" and s.iterations[0].result == f"{s.asset}@v001"
    assert s.iterations[0].previews[0].label == "frente"
    assert project.store.get_meta(s.asset).folder == "Testes"
    s = eng.review(s.id, Review(score=3, verdict="revise", issues=["está vermelho"],
                                next_action=Action(pipeline="fake.fix", params={"input_asset": "$current", "color": "#00ff00"})))
    assert s.status == "ready"
    s = eng.step(s.id)
    it = s.iterations[1]
    assert it.resolved_params["input_asset"] == f"{s.asset}@v001" and it.result == f"{s.asset}@v002"
    assert _color(project, it.result) == (0, 255, 0)
    s = eng.review(s.id, Review(score=9, verdict="accept"))
    assert s.status == "done" and s.final == f"{s.asset}@v002" and s.best == f"{s.asset}@v002"
    # persistido
    assert load_session(project, s.id).status == "done"


def test_validation_and_errors(project):
    eng = RefineEngine(project)
    s = eng.step(eng.create("fake.thing", Brief(prompt="x")).id)
    with pytest.raises(ValueError):  # ação fora do catálogo
        eng.review(s.id, Review(score=2, verdict="revise", next_action=Action(pipeline="fake.other")))
    with pytest.raises(Exception):  # params inválidos para a ação
        eng.review(s.id, Review(score=2, verdict="revise", next_action=Action(pipeline="fake.fix", params={"nope": 1})))
    # erro na execução vira parte da iteração; sessão segue para revisão
    eng.review(s.id, Review(score=2, verdict="revise",
                            next_action=Action(pipeline="fake.fix", params={"input_asset": "$current", "color": "boom"})))
    s = eng.step(s.id)
    assert s.status == "awaiting_review" and "falhou de propósito" in s.iterations[-1].error
    assert s.iterations[-1].result is None


def test_api_mode_runs_until_accept_or_limit(project):
    script = iter([
        Review(score=4, verdict="revise", next_action=Action(pipeline="fake.fix", params={"input_asset": "$current", "color": "#0000ff"})),
        Review(score=6, verdict="revise", next_action=Action(pipeline="fake.fix", params={"input_asset": "$best", "color": "#00ff00"})),
        Review(score=8.5, verdict="revise", next_action=Action(pipeline="fake.fix", params={"input_asset": "$current", "color": "#000000"})),
    ])
    eng = RefineEngine(project, critic=lambda s, r: next(script))
    s = eng.create("fake.thing", Brief(prompt="x", target_score=8), critic="api")
    s = eng.run_until_pause(s.id)
    # nota 8.5 >= alvo 8 encerra mesmo com verdict=revise
    assert s.status == "done" and len(s.iterations) == 3 and s.final == f"{s.asset}@v003"
    assert _color(project, s.final) == (0, 255, 0)

    loop = RefineEngine(project, critic=lambda s, r: Review(score=1, verdict="revise",
                        next_action=Action(pipeline="fake.gen", params={"prompt": "de novo"})))
    s2 = loop.run_until_pause(loop.create("fake.thing", Brief(prompt="y", max_iterations=3), critic="api").id)
    assert s2.status == "done" and len(s2.iterations) == 3 and "limite" in s2.message


def test_api_critic_failure_falls_back_to_manual(project):
    def broken(s, r):
        raise RuntimeError("sem credencial")

    eng = RefineEngine(project, critic=broken)
    s = eng.run_until_pause(eng.create("fake.thing", Brief(prompt="x"), critic="api").id)
    assert s.status == "awaiting_review" and s.critic == "manual" and "indisponível" in s.message


def test_critic_parses_and_validates(project, monkeypatch):
    eng = RefineEngine(project)
    s = eng.step(eng.create("fake.thing", Brief(prompt="algo verde", criteria=["sem borda"])).id)
    replies = iter([
        {"score": 3, "verdict": "revise", "summary": "s", "issues": ["vermelho"],
         "next_action": {"pipeline": "fake.nope", "params_json": "{}", "note": ""}},  # inválido -> corrige
        {"score": 3, "verdict": "revise", "summary": "s", "issues": ["vermelho"],
         "next_action": {"pipeline": "fake.fix", "params_json": json.dumps({"input_asset": "$current", "color": "#00ff00"}), "note": "pintar"}},
    ])
    seen = []

    def fake_call(client, messages):
        seen.append(messages)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                               content=[SimpleNamespace(type="text", text=json.dumps(next(replies)))])

    monkeypatch.setattr(critic_mod, "_call", fake_call)
    monkeypatch.setattr("anthropic.Anthropic", lambda **kw: object())
    rv = critic_mod.review_iteration(project, s, R())
    assert rv.next_action.pipeline == "fake.fix" and rv.reviewer == "claude-api:claude-opus-5-5"
    first = seen[0][0]["content"]
    text = first[0]["text"]
    assert "algo verde" in text and "ser verde" in text and "sem borda" in text and "fake.fix" in text
    assert any(b["type"] == "image" for b in first)          # prévia enviada como imagem
    assert seen[1][-1]["role"] == "user" and "inválida" in seen[1][-1]["content"]


def test_server_manual_flow(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from forge_core.config import Settings
    from forge_core.server import create_app

    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)

    def wait(c, sid, status):
        for _ in range(600):
            s = c.get(f"/api/projects/demo/refine/{sid}").json()
            if s["status"] == status:
                return s
            time.sleep(0.05)
        raise AssertionError(f"status {s['status']} != {status}")

    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        c.post("/api/projects", json={"slug": "demo"})
        assert c.get("/api/refiners").json()["refiners"][0]["id"] == "fake.thing"
        s = c.post("/api/projects/demo/refine", json={"refiner": "fake.thing", "brief": {"prompt": "verde"}}).json()
        s = wait(c, s["id"], "awaiting_review")
        url = s["iterations"][0]["previews"][0]["url"]
        assert c.get(url).status_code == 200
        bad = c.post(f"/api/projects/demo/refine/{s['id']}/review",
                     json={"score": 2, "verdict": "revise", "next_action": {"pipeline": "fake.zzz"}})
        assert bad.status_code == 400
        c.post(f"/api/projects/demo/refine/{s['id']}/review",
               json={"score": 2, "verdict": "revise", "issues": ["vermelho"],
                     "next_action": {"pipeline": "fake.fix", "params": {"input_asset": "$current", "color": "#00ff00"}}})
        s = wait(c, s["id"], "awaiting_review")
        assert len(s["iterations"]) == 2 and s["iterations"][1]["job_id"]  # rodou como job da fila
        jobs = c.get("/api/jobs").json()
        assert any("refine" in j["request"]["tags"] for j in jobs)
        s = c.post(f"/api/projects/demo/refine/{s['id']}/review", json={"score": 9, "verdict": "accept"}).json()
        assert s["status"] == "done" and s["final"].endswith("@v002")
        lst = c.get("/api/projects/demo/refine").json()
        assert lst[0]["iteration_count"] == 2 and lst[0]["last_score"] == 9 and lst[0]["thumb"]

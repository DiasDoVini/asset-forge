"""Modo +18: política de conteúdo, interruptor, API, CLI, MCP, crítico e LLM local.

Nenhum teste gera imagem — tudo é verificado antes de chegar ao ComfyUI.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from typer.testing import CliRunner

from forge_core import content
from forge_core.config import Settings
from forge_core.content import ContentPolicyError, check_params, find_explicit, find_minor_reference
from forge_core.pipeline import Modality, OutputFile, Pipeline, PipelineParams, Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline


class P(PipelineParams):
    prompt: str = ""
    layers: list[dict] = []
    code: str = ""


class Draw(Pipeline):
    id = "t.draw"
    Params = P
    resources = {"cpu"}

    def run(self, ctx, p):
        f = ctx.version_dir / "image.png"
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(f)
        return [OutputFile(f)]


SAFE = {"minor_present": False, "minor_confidence": 0, "youngest_apparent_age": 30, "self_harm": False, "reason": "ok"}


@pytest.fixture(autouse=True)
def vision(monkeypatch):
    """Classificador visual simulado (os testes não dependem do Ollama). verdict[0] é a resposta atual."""
    from forge_core import safety

    verdict = [dict(SAFE)]
    calls = []
    monkeypatch.setattr(safety, "check_image", lambda img: calls.append(img) or verdict[0])
    return verdict, calls


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(Modality(name="t", pipelines=[Draw()]))
    set_registry(r)
    yield
    set_registry(None)


@pytest.fixture
def adult(tmp_path):
    p = Project.create(tmp_path / "adulto")
    p.set_nsfw(True, confirm_adult=True)
    return p


# ---------------------------------------------------------------- filtro de menores (tentativas de burla)

MINOR_CASES = [
    # diretos
    "teen girl", "TEEN", "loli", "shota", "child", "kid", "schoolgirl", "little girl", "jailbait", "preteen",
    # pt/es
    "criança", "garotinha", "menina", "novinha", "colegial", "adolescente", "menor de idade", "ensino médio",
    "jovencita", "chica de 15 años", "menina de 15 anos",
    # idades
    "15 years old", "16-year-old", "15yo", "13 y.o.", "12 y/o", "age 12", "aged twelve", "age of 14",
    "sixteen years old", "fifteen yo", "de 12 anos", "dezesseis anos", "under 18", "underaged",
    # burlas
    "t33n", "l0li", "sh0ta", "k1d", "t e e n", "l.o.l.i", "y o u n g girl", "tееn",  # cirílico
    "pre-teen", "school_girl", "high school student", "highschool", "teenaged", "jail bait", "kiddie",
]

ADULT_OK = [
    "adult woman, 25 years old", "19 yo woman", "eighteen years old", "elf queen, 300 years old",
    "a woman in her thirties", "mature female warrior", "estilo dos anos 80", "retro 90s anime style",
    "old man, 70 years old", "petite adult woman", "goblin", "skidding car", "kidney", "warrior between walls",
    "espada menor", "nineteen", "Essex",
]


@pytest.mark.parametrize("text", MINOR_CASES)
def test_minor_references_detected(text):
    assert find_minor_reference(text), text


@pytest.mark.parametrize("text", ADULT_OK)
def test_adult_texts_not_flagged(text):
    assert find_minor_reference(text) is None, (text, find_minor_reference(text))


def test_explicit_detection():
    for t in ["nude", "n u d e", "nudе", "NAKED", "pelada", "sexo", "hentai"]:
        assert find_explicit(t), t
    for t in ["cumprimento", "nutrition", "sextant", "Essex", "number"]:
        assert find_explicit(t) is None, t


# ---------------------------------------------------------------- interruptor

def test_toggle_requires_confirmation_and_is_user_only(tmp_path):
    p = Project.create(tmp_path / "p")
    with pytest.raises(PermissionError):
        p.set_nsfw(True)
    for key in ("nsfw", "nsfw_confirmed_at"):
        with pytest.raises(PermissionError):
            p.set_value(key, True)
    assert not Project.load(p.root).config.nsfw
    p.set_nsfw(True, confirm_adult=True)
    q = Project.load(p.root)  # persiste no project.yaml
    assert q.config.nsfw and q.config.nsfw_confirmed_at is not None
    q.set_nsfw(False)
    assert not Project.load(p.root).config.nsfw and Project.load(p.root).config.nsfw_confirmed_at is None


# ---------------------------------------------------------------- runner

def test_sfw_project_blocks_explicit_allows_young_characters(tmp_path):
    p = Project.create(tmp_path / "p")
    with pytest.raises(ContentPolicyError):
        run_pipeline(p, "t.draw", {"prompt": "nude elf"})
    run_pipeline(p, "t.draw", {"prompt": "child knight sprite"})  # jogo comum: personagens jovens são ok


def test_adult_project_allows_adults_and_blocks_minors(adult):
    run_pipeline(adult, "t.draw", {"prompt": "nude adult elf woman, 30 years old"})
    for bad in ["nude teen", "17 years old", "l0li"]:
        with pytest.raises(ContentPolicyError):
            run_pipeline(adult, "t.draw", {"prompt": bad})
    assert len(adult.store.search()) == 1  # nada foi gravado pelas tentativas recusadas


def test_nested_params_and_style_are_checked(adult):
    with pytest.raises(ContentPolicyError):
        check_params(adult, {"layers": [{"note": "a schoolgirl"}]})
    check_params(adult, {"code": "float kid = 1.0;"})  # código não é texto descritivo
    adult.config.style.prompt = "loli art style"
    with pytest.raises(ContentPolicyError):
        check_params(adult, {"prompt": "portrait of an adult woman"})


# ---------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path):
    from forge_core.server import create_app

    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)
    with TestClient(create_app(settings, isolate_jobs=False, persist_jobs=False)) as c:
        assert c.post("/api/projects", json={"slug": "p"}).status_code == 201
        yield c


def test_api_toggle(client):
    assert client.post("/api/projects/p/nsfw", json={"enabled": True}).status_code == 403
    client.patch("/api/projects/p", json={"nsfw": True})  # PATCH genérico não liga o modo
    assert client.get("/api/projects/p").json()["nsfw"] is False
    r = client.post("/api/projects/p/nsfw", json={"enabled": True, "confirm_adult": True})
    assert r.status_code == 200 and r.json()["nsfw"] is True
    assert client.post("/api/projects/p/nsfw", json={"enabled": False}).json()["nsfw"] is False


def test_api_job_submission_is_checked_before_queueing(client):
    r = client.post("/api/jobs", json={"project": "p", "pipeline": "t.draw", "params": {"prompt": "nude elf"}})
    assert r.status_code == 400 and "+18" in r.json()["detail"]
    client.post("/api/projects/p/nsfw", json={"enabled": True, "confirm_adult": True})
    r = client.post("/api/jobs", json={"project": "p", "pipeline": "t.draw", "params": {"prompt": "nude teen"}})
    assert r.status_code == 400 and "menores" in r.json()["detail"]
    assert client.post("/api/jobs", json={"project": "p", "pipeline": "t.draw", "params": {"prompt": "adult woman"}}).status_code == 202
    assert all("teen" not in json.dumps(j) for j in client.get("/api/jobs?project=p").json())


def test_prompt_assist_respects_policy(client, monkeypatch):
    from forge_core import local_llm

    monkeypatch.setattr(local_llm, "pick_model", lambda *a, **k: "fake")
    sent = {}

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        sent.update(json)
        return httpx.Response(200, json={"message": {"content": REPLY[0]}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(local_llm.httpx, "post", fake_post)
    REPLY = ["a glowing potion"]
    assert client.post("/api/projects/p/prompt-assist", json={"prompt": "poção"}).json()["prompt"] == "a glowing potion"
    assert "general audience" in sent["messages"][0]["content"]
    # projeto normal: pedido explícito é recusado antes de chamar o modelo
    sent.clear()
    assert client.post("/api/projects/p/prompt-assist", json={"prompt": "nude elf"}).status_code == 400 and not sent
    client.post("/api/projects/p/nsfw", json={"enabled": True, "confirm_adult": True})
    REPLY[0] = "adult elf woman"
    assert client.post("/api/projects/p/prompt-assist", json={"prompt": "elfa adulta"}).status_code == 200
    assert content.ADULT_SYSTEM_RULE in sent["messages"][0]["content"]
    # mesmo que o modelo invente um menor, a saída é barrada
    REPLY[0] = "a teen girl"
    assert client.post("/api/projects/p/prompt-assist", json={"prompt": "elfa adulta"}).status_code == 400


# ---------------------------------------------------------------- CLI

def test_cli_toggle(tmp_path, monkeypatch):
    from forge_core.cli import app
    from forge_core.config import get_settings

    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    Project.create(tmp_path / "p")
    r = CliRunner()
    assert r.invoke(app, ["project", "nsfw", "on", "-p", "p"]).exit_code == 1
    assert r.invoke(app, ["project", "nsfw", "on", "-p", "p", "--i-am-18"]).exit_code == 0
    assert Project.load(tmp_path / "p").config.nsfw
    assert r.invoke(app, ["project", "set", "nsfw=false", "-p", "p"]).exit_code != 0
    get_settings.cache_clear()


# ---------------------------------------------------------------- MCP e crítico

def test_mcp_never_sends_images_of_adult_projects(adult, tmp_path):

    from forge_core import mcp_server

    av = run_pipeline(adult, "t.draw", {"prompt": "adult woman"})
    out = mcp_server._with_preview(adult, av, include_image=True)
    assert all(isinstance(x, str) for x in out) and any("+18" in x for x in out)
    sfw = Project.create(tmp_path / "normal")
    av2 = run_pipeline(sfw, "t.draw", {"prompt": "potion"})
    assert any(not isinstance(x, str) for x in mcp_server._with_preview(sfw, av2, include_image=True))


def test_api_critic_refuses_adult_projects(adult):
    from forge_core.critic import CriticError, review_iteration

    with pytest.raises(CriticError, match=r"\+18"):
        review_iteration(adult, None, None)  # recusa antes de montar qualquer chamada à API


# ---------------------------------------------------------------- categorias sempre bloqueadas

@pytest.mark.parametrize("text", ["girl cutting herself", "s3lf h4rm", "suicide scene", "automutilação", "cortar os pulsos",
                                  "r4pe", "non-consensual", "estupro", "bestiality", "zoofilia"])
def test_always_blocked_in_any_project(tmp_path, text):
    for nsfw in (False, True):
        p = Project.create(tmp_path / f"p{nsfw}")
        if nsfw:
            p.set_nsfw(True, confirm_adult=True)
        with pytest.raises(ContentPolicyError):
            run_pipeline(p, "t.draw", {"prompt": text})


@pytest.mark.parametrize("text", ["grape", "drape", "therapist", "scrape", "orc raider", "warrior bleeding from a wound"])
def test_always_blocked_no_false_positives(text):
    assert content.find_always_blocked(text) is None


# ---------------------------------------------------------------- prompts de segurança ocultos

def test_hidden_prompts_injected_by_graph_role():
    from forge_core.safety import HIDDEN_NEGATIVE, HIDDEN_POSITIVE, apply_to_graph

    g = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "an adult elf"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "blurry"}},
        "4": {"class_type": "WanImageToVideo", "inputs": {"positive": ["2", 0], "negative": ["3", 0]}},
        "5": {"class_type": "KSampler", "inputs": {"positive": ["4", 0], "negative": ["4", 1]}},
    }
    out = apply_to_graph(g)
    assert out["2"]["inputs"]["text"].startswith("an adult elf") and HIDDEN_POSITIVE in out["2"]["inputs"]["text"]
    assert HIDDEN_NEGATIVE in out["3"]["inputs"]["text"] and HIDDEN_POSITIVE not in out["3"]["inputs"]["text"]
    assert g["2"]["inputs"]["text"] == "an adult elf"  # original intacto
    audio = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "boom"}},
             "2": {"class_type": "KSampler", "inputs": {"positive": ["1", 0], "negative": ["1", 0]}},
             "3": {"class_type": "VAEDecodeAudio", "inputs": {}}}
    assert apply_to_graph(audio) == audio


def test_comfy_client_filter_only_in_adult_projects(adult, tmp_path):
    from forge_core.pipeline import Context

    sfw = Project.create(tmp_path / "sfw")
    mk = lambda pr: Context(project=pr, pipeline=Draw(), version_dir=tmp_path, slug="x", version=1)  # noqa: E731
    assert mk(adult).comfy.graph_filter is not None
    assert mk(sfw).comfy.graph_filter is None


# ---------------------------------------------------------------- verificação visual

def test_vision_guard_discards_flagged_output(adult, vision):
    verdict, calls = vision
    run_pipeline(adult, "t.draw", {"prompt": "adult woman"})
    assert len(calls) == 1
    verdict[0] = {**SAFE, "minor_present": True, "minor_confidence": 0.8, "youngest_apparent_age": 15}
    with pytest.raises(ContentPolicyError, match="verificação de segurança"):
        run_pipeline(adult, "t.draw", {"prompt": "adult woman"}, asset="flagged")
    assert not adult.store.versions("flagged")  # nada gravado
    assert not (adult.store.root / "flagged").exists() or not any((adult.store.root / "flagged").glob("v*"))
    verdict[0] = {**SAFE, "self_harm": True}
    with pytest.raises(ContentPolicyError, match="automutilação"):
        run_pipeline(adult, "t.draw", {"prompt": "adult woman"})


def test_vision_guard_not_used_in_normal_projects(tmp_path, vision):
    _, calls = vision
    run_pipeline(Project.create(tmp_path / "sfw"), "t.draw", {"prompt": "potion"})
    assert calls == []


def test_vision_guard_fails_closed(adult, monkeypatch):
    from forge_core import safety

    monkeypatch.undo()  # volta o check_image real
    monkeypatch.setattr(safety, "VISION_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(safety, "VISION_MODEL", "")
    with pytest.raises(ContentPolicyError, match="Ollama"):
        run_pipeline(adult, "t.draw", {"prompt": "adult woman"})


def test_blocked_thresholds():
    from forge_core.safety import blocked

    assert blocked(SAFE) is None
    assert blocked({**SAFE, "youngest_apparent_age": 16})
    assert blocked({**SAFE, "minor_present": True, "minor_confidence": 0.5})
    assert blocked({**SAFE, "minor_present": True, "minor_confidence": 0.2}) is None

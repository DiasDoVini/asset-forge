import pytest
from fastapi.testclient import TestClient
from PIL import Image

from forge_core.config import Settings
from forge_core.pipeline import Modality, OutputFile, Pipeline, PipelineParams, Registry, set_registry
from forge_core.project import Project, normalize_folder
from forge_core.runner import run_pipeline


class P(PipelineParams):
    prompt: str = "x"


class Make(Pipeline):
    id = "fake.make"
    resources = {"cpu"}
    Params = P

    def run(self, ctx, p):
        out = ctx.version_dir / "image.png"
        Image.new("RGB", (4, 4)).save(out)
        return [OutputFile(out)]


class Derive(Make):
    id = "fake.derive"
    requires_input = True


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(Modality(name="fake", pipelines=[Make(), Derive()]))
    set_registry(r)
    yield
    set_registry(None)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


def test_normalize():
    assert normalize_folder(" Personagens//Cavaleiro/ ") == "Personagens/Cavaleiro"
    assert normalize_folder("../a/./b") == "a/b"
    assert normalize_folder(None) == ""


def test_inheritance_and_filters(project):
    run_pipeline(project, "fake.make", {"prompt": "knight"}, folder="Personagens/Cavaleiro", category="character")
    run_pipeline(project, "fake.make", {"prompt": "sword"}, category="weapon")
    # derivado (slug novo) herda pasta e categoria da entrada
    d = run_pipeline(project, "fake.derive", {"input_asset": "knight", "prompt": "knight walk"})
    assert project.store.get_meta(d.slug).folder == "Personagens/Cavaleiro"
    assert project.store.get_meta(d.slug).category == "character"
    # explícito vence herança
    e = run_pipeline(project, "fake.derive", {"input_asset": "knight", "prompt": "boss"}, folder="Inimigos")
    assert project.store.get_meta(e.slug).folder == "Inimigos"

    s = project.store
    assert {r["slug"] for r in s.search(folder="Personagens/Cavaleiro")} == {"knight", d.slug}
    assert {r["slug"] for r in s.search(folder="Personagens", recursive=True)} == {"knight", d.slug}
    assert s.search(folder="Personagens") == []
    assert [r["slug"] for r in s.search(folder="")] == ["sword"]
    assert [r["slug"] for r in s.search(category="weapon")] == ["sword"]
    assert s.search(q="Cavaleiro")  # busca também casa pela pasta
    assert s.folder_counts() == {"Personagens/Cavaleiro": 2, "": 1, "Inimigos": 1}


def test_move_folder_and_reindex(project, tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (2, 2)).save(src)
    project.store.import_file(src, slug="a", folder="Chars/Hero/Anims")
    project.store.import_file(src, slug="b", folder="Chars/Hero")
    project.store.import_file(src, slug="c", folder="Chars/Heroine")
    assert project.store.move_folder("Chars/Hero", "Personagens/Heroi") == 2
    assert project.store.get_meta("a").folder == "Personagens/Heroi/Anims"
    assert project.store.get_meta("c").folder == "Chars/Heroine"  # prefixo parecido não é afetado
    with pytest.raises(ValueError):
        project.store.move_folder("Personagens", "Personagens/x")
    # índice é regenerável a partir de _meta.json
    project.store.reindex()
    assert {r["slug"] for r in project.store.search(folder="Personagens", recursive=True)} == {"a", "b"}


def test_api(tmp_path):
    from forge_core.server import create_app

    settings = Settings(projects_dir=tmp_path, comfy_url="http://127.0.0.1:1", host="127.0.0.1", port=0, web_dist=None)
    with TestClient(create_app(settings, isolate_jobs=False)) as c:
        c.post("/api/projects", json={"slug": "demo"})
        cats = c.get("/api/projects/demo").json()["categories"]
        assert any(x["id"] == "character" for x in cats)
        for name in ("k1", "k2", "s1"):
            c.post("/api/projects/demo/assets", files={"file": (f"{name}.png", _png(), "image/png")}, data={"asset": name})
        assert c.post("/api/projects/demo/folders", json={"path": "Vazia"}).status_code == 201
        r = c.post("/api/projects/demo/assets/organize", json={"slugs": ["k1", "k2"], "folder": "Personagens/K", "category": "character"})
        assert r.json()["updated"] == ["k1", "k2"]
        tree = c.get("/api/projects/demo/folders").json()
        paths = {f["path"]: f for f in tree["folders"]}
        assert paths["Personagens"]["total"] == 2 and paths["Personagens/K"]["count"] == 2 and "Vazia" in paths
        assert tree["root_count"] == 1
        rows = c.get("/api/projects/demo/assets", params={"folder": "Personagens", "recursive": True}).json()
        assert {r["slug"] for r in rows} == {"k1", "k2"} and rows[0]["category"] == "character"
        assert c.get("/api/projects/demo/assets/k1/1").json()["folder"] == "Personagens/K"
        c.post("/api/projects/demo/folders/rename", json={"old": "Personagens/K", "new": "Herois/K"})
        assert {r["slug"] for r in c.get("/api/projects/demo/assets", params={"folder": "Herois/K"}).json()} == {"k1", "k2"}
        c.delete("/api/projects/demo/folders", params={"path": "Herois"})
        assert len(c.get("/api/projects/demo/assets", params={"folder": ""}).json()) == 3
        assert "Herois" not in c.get("/api/projects/demo").json()["folders"]


def _png() -> bytes:
    import io

    b = io.BytesIO()
    Image.new("RGB", (2, 2)).save(b, "PNG")
    return b.getvalue()

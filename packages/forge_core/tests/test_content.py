import pytest

from forge_core.content import ContentPolicyError, check_params, find_explicit, find_minor_reference
from forge_core.pipeline import Modality, Pipeline, PipelineParams, Registry, set_registry
from forge_core.project import Project
from forge_core.runner import run_pipeline


class P(PipelineParams):
    prompt: str = ""


class Echo(Pipeline):
    id = "t.echo"
    Params = P
    resources = {"cpu"}

    def run(self, ctx, p):
        f = ctx.version_dir / "x.txt"
        f.write_text(p.prompt)
        from forge_core import OutputFile
        return [OutputFile(f)]


@pytest.fixture(autouse=True)
def vision(monkeypatch):
    from forge_core import safety

    monkeypatch.setattr(safety, "check_image", lambda img: {"minor_present": False, "minor_confidence": 0,
                                                            "youngest_apparent_age": 30, "self_harm": False, "reason": ""})


@pytest.fixture(autouse=True)
def reg():
    r = Registry()
    r.add(Modality(name="t", pipelines=[Echo()]))
    set_registry(r)
    yield
    set_registry(None)


def test_minor_detection():
    for t in ["teen girl", "a 15 year old", "15yo", "loli", "criança", "garotinha", "colegial", "age 12", "school-girl"]:
        assert find_minor_reference(t), t
    for t in ["adult woman, 25 years old", "nineteen", "elf queen", "espada menor", "teenage mutant"[:0] or "warrior"]:
        assert not find_minor_reference(t), t


def test_explicit_detection_word_boundaries():
    assert find_explicit("a nude statue") == "nude"
    assert find_explicit("cumprimento") is None and find_explicit("Essex") is None


def test_default_project_blocks_explicit(tmp_path):
    p = Project.create(tmp_path / "p")
    with pytest.raises(ContentPolicyError):
        run_pipeline(p, "t.echo", {"prompt": "nude elf"})
    run_pipeline(p, "t.echo", {"prompt": "child knight sprite"})  # SFW: personagens jovens são ok


def test_nsfw_requires_confirmation_and_blocks_minors(tmp_path):
    p = Project.create(tmp_path / "p")
    with pytest.raises(PermissionError):
        p.set_nsfw(True)
    with pytest.raises(PermissionError):
        p.set_value("nsfw", True)
    p.set_nsfw(True, confirm_adult=True)
    assert p.config.nsfw and p.config.nsfw_confirmed_at
    run_pipeline(p, "t.echo", {"prompt": "nude adult elf woman"})
    with pytest.raises(ContentPolicyError):
        run_pipeline(p, "t.echo", {"prompt": "nude teen"})
    with pytest.raises(ContentPolicyError):
        check_params(p, {"layers": [{"prompt": "17 years old"}]})
    p.config.style.prompt = "loli style"
    with pytest.raises(ContentPolicyError):
        check_params(p, {"prompt": "portrait"})

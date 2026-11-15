"""Qwen-Image 2.1: grafo (igual aos templates oficiais) e escolha da instância do ComfyUI que tem os nós."""
import pytest

from forge_core import ComfyError
from forge_2d import graphs, pipelines


def _by_type(g):
    out = {}
    for nid, n in g.to_dict().items():
        out.setdefault(n["class_type"], []).append((nid, n["inputs"]))
    return out


def test_turbo_graph_matches_template():
    g, img, mask = graphs.text2img_qwen21("a red potion bottle", 1024, 768, 7, models=graphs.DEFAULT_MODELS)
    t = _by_type(g)
    assert t["UNETLoader"][0][1]["unet_name"] == "qwen_image_2.1_turbo_int8_convrot.safetensors"
    assert t["CLIPLoader"][0][1] == {"clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "device": "default"}
    assert t["ManualSigmas"][0][1]["sigmas"].count(",") == 8          # 8 passos
    assert t["CFGGuider"][0][1]["cfg"] == 1.0
    enc_id = t["TextEncodeQwenImage21"][0][0]
    guider = t["CFGGuider"][0][1]
    assert guider["positive"] == [enc_id, 0] and guider["negative"] == [enc_id, 1]
    assert t["EmptyLatentImage"][0][1]["width"] == 1024 and t["EmptyLatentImage"][0][1]["height"] == 768
    assert t["RandomNoise"][0][1]["noise_seed"] == 7
    assert "QwenImage21Cache" in t and mask is None and img in g.to_dict()
    assert "KSampler" not in t


def test_full_graph_and_background_removal():
    g, img, mask = graphs.text2img_qwen21("x", 1024, 1024, 1, models=graphs.DEFAULT_MODELS, turbo=False, steps=25,
                                          remove_bg=True, negative="blurry")
    t = _by_type(g)
    assert t["UNETLoader"][0][1]["unet_name"] == "qwen_image_2.1_int8_convrot.safetensors"
    ks = t["KSampler"][0][1]
    assert (ks["steps"], ks["cfg"], ks["sampler_name"], ks["scheduler"]) == (25, 1.0, "euler", "simple")
    assert t["TextEncodeQwenImage21"][0][1]["negative_prompt"] == "blurry"
    assert "RemoveBackground" in t and mask is not None and "ManualSigmas" not in t


class _FakeComfy:
    def __init__(self, url, nodes=(), models=None, up=True):
        self.url, self._nodes, self._models, self._up = url, set(nodes), models or {}, up
        self.cancel_event = None
        self.graph_filter = None

    def ping(self):
        return self._up

    def object_info(self, node):
        return {node: {}} if node in self._nodes else {}

    def models(self, folder):
        return self._models.get(folder, [])


class _Ctx:
    def __init__(self, comfy):
        self._comfy = comfy
        self.logs = []

    @property
    def comfy(self):
        return self._comfy

    def log(self, m):
        self.logs.append(m)


NEED = {"diffusion_models": "u.safetensors", "text_encoders": "c.safetensors", "vae": "v.safetensors"}
HAS = {"diffusion_models": ["u.safetensors"], "text_encoders": ["c.safetensors"], "vae": ["v.safetensors"]}


@pytest.fixture
def instances(monkeypatch):
    from forge_core import comfy, comfy_supervisor as cs

    made = {}

    def fake_client(url, cancel_event=None, **kw):
        return made[url]

    monkeypatch.setattr(comfy, "ComfyClient", fake_client)
    monkeypatch.setattr(cs, "INSTANCES", [cs.Instance("desktop", 8188, "x", "d"), cs.Instance("3d", 8189, "x", "3"),
                                          cs.Instance("trellis", 8190, "x", "t")])
    return made


def test_picks_instance_with_nodes_and_models(instances):
    instances["http://127.0.0.1:8189"] = _FakeComfy("http://127.0.0.1:8189", nodes=(), models=HAS)          # ComfyUI antigo
    instances["http://127.0.0.1:8190"] = _FakeComfy("http://127.0.0.1:8190", nodes=graphs.QI21_NODES, models=HAS)
    base = _FakeComfy("http://127.0.0.1:8188", up=False)                                                     # desktop parado
    base.graph_filter = "filtro+18"
    ctx = _Ctx(base)
    pipelines._use_comfy_with(ctx, graphs.QI21_NODES, NEED, "o Qwen-Image 2.1")
    assert ctx.comfy.url == "http://127.0.0.1:8190" and ctx.comfy.graph_filter == "filtro+18"
    assert "8190" in ctx.logs[0]


def test_prefers_configured_instance(instances):
    for port in (8189, 8190):
        instances[f"http://127.0.0.1:{port}"] = _FakeComfy(f"http://127.0.0.1:{port}", nodes=graphs.QI21_NODES, models=HAS)
    base = _FakeComfy("http://127.0.0.1:8188", nodes=graphs.QI21_NODES, models=HAS)
    ctx = _Ctx(base)
    pipelines._use_comfy_with(ctx, graphs.QI21_NODES, NEED, "x")
    assert ctx.comfy is base and not ctx.logs


def test_explains_what_is_missing(instances):
    instances["http://127.0.0.1:8189"] = _FakeComfy("http://127.0.0.1:8189", nodes=())
    instances["http://127.0.0.1:8190"] = _FakeComfy("http://127.0.0.1:8190", nodes=graphs.QI21_NODES, models={"vae": ["v.safetensors"]})
    ctx = _Ctx(_FakeComfy("http://127.0.0.1:8188", up=False))
    with pytest.raises(ComfyError) as e:
        pipelines._use_comfy_with(ctx, graphs.QI21_NODES, NEED, "o Qwen-Image 2.1")
    msg = str(e.value)
    assert "8189 (sem os nós" in msg and "diffusion_models/u.safetensors" in msg and "Instalação" in msg


def test_engine_param_is_validated():
    p = pipelines.Text2DParams(prompt="x", engine="qwen-image-2.1")
    assert p.engine == "qwen-image-2.1"
    with pytest.raises(ValueError):
        pipelines.Text2DParams(prompt="x", engine="dalle")


def test_skips_instance_with_empty_models_folder(instances):
    """Caso real: o Comfy Desktop recém-instalado subiu na :8188 com a pasta de modelos vazia."""
    instances["http://127.0.0.1:8189"] = _FakeComfy("http://127.0.0.1:8189", models=HAS)
    instances["http://127.0.0.1:8190"] = _FakeComfy("http://127.0.0.1:8190", models={})
    ctx = _Ctx(_FakeComfy("http://127.0.0.1:8188", models={}))
    pipelines._ensure_comfy(ctx)
    assert ctx.comfy.url == "http://127.0.0.1:8189"
    assert "não enxerga nenhum modelo" in ctx.logs[0]
    ctx = _Ctx(_FakeComfy("http://127.0.0.1:8188", models={}))
    pipelines._ensure_comfy(ctx, NEED)                        # com arquivos: também pula a vazia
    assert ctx.comfy.url == "http://127.0.0.1:8189"

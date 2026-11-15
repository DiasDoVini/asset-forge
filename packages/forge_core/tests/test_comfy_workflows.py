"""Registro dos workflows enviados ao ComfyUI e conversão API → UI (o que o frontend do ComfyUI abre)."""
from forge_core import comfy_workflows as cw

INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"], {}]}},
                               "input_order": {"required": ["ckpt_name"]}, "output": ["MODEL", "CLIP", "VAE"],
                               "output_name": ["MODEL", "CLIP", "VAE"]},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP", {}]}},
                       "input_order": {"required": ["text", "clip"]}, "output": ["CONDITIONING"], "output_name": ["CONDITIONING"]},
    "KSampler": {"input": {"required": {
        "model": ["MODEL", {}], "seed": ["INT", {"default": 0, "control_after_generate": True}], "steps": ["INT", {"default": 20}],
        "cfg": ["FLOAT", {"default": 8.0}], "sampler_name": [["euler", "dpmpp"], {}], "scheduler": [["simple", "karras"], {}],
        "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}], "latent_image": ["LATENT", {}],
        "denoise": ["FLOAT", {"default": 1.0}]}},
        "input_order": {"required": ["model", "seed", "steps", "cfg", "sampler_name", "scheduler", "positive", "negative",
                                     "latent_image", "denoise"]},
        "output": ["LATENT"], "output_name": ["LATENT"]},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}],
                                                "batch_size": ["INT", {"default": 1}]}},
                         "input_order": {"required": ["width", "height", "batch_size"]}, "output": ["LATENT"], "output_name": ["LATENT"]},
}

GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "b.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 512, "batch_size": 1}},
    "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": 42, "steps": 8, "cfg": 1.0, "sampler_name": "dpmpp",
                                               "scheduler": "karras", "positive": ["2", 0], "negative": ["3", 0],
                                               "latent_image": ["4", 0], "denoise": 1.0}},
}


def test_to_ui_widgets_links_and_layout():
    ui = cw.to_ui(GRAPH, INFO)
    nodes = {n["type"] + str(n["id"]): n for n in ui["nodes"]}
    ks = next(n for n in ui["nodes"] if n["type"] == "KSampler")
    # widgets na ordem do object_info, com o "fixed" do control_after_generate depois da seed
    assert ks["widgets_values"] == [42, "fixed", 8, 1.0, "dpmpp", "karras", 1.0]
    assert [i["name"] for i in ks["inputs"]] == ["model", "positive", "negative", "latent_image"]
    assert all(i["link"] for i in ks["inputs"])
    assert len(ui["links"]) == 6 and ui["last_link_id"] == 6
    ckpt = next(n for n in ui["nodes"] if n["type"] == "CheckpointLoaderSimple")
    assert ckpt["widgets_values"] == ["b.safetensors"]
    assert ckpt["outputs"][1]["links"] and len(ckpt["outputs"][1]["links"]) == 2       # CLIP para os dois encoders
    # links coerentes: [id, origem, slot, destino, slot do destino, tipo]
    for lid, o, os_, t, ts, typ in ui["links"]:
        target = next(n for n in ui["nodes"] if n["id"] == t)
        assert target["inputs"][ts]["link"] == lid
        assert typ in ("MODEL", "CLIP", "CONDITIONING", "LATENT")
    # colunas por profundidade: o sampler fica à direita dos encoders
    enc = next(n for n in ui["nodes"] if n["type"] == "CLIPTextEncode")
    assert ks["pos"][0] > enc["pos"][0] > ckpt["pos"][0]
    assert nodes


def test_record_listing_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "_dir", lambda: tmp_path)
    monkeypatch.setattr(cw, "_live", lambda url: {"running": {"p1": [0, "p1"]}, "pending": {}, "history": {
        "p2": {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "sem VRAM", "timestamp": 2000}]]}}}})
    cw.record("http://127.0.0.1:8190", GRAPH, prompt_id="p1", meta={"project": "x", "pipeline": "2d.text2d"})
    cw.record("http://127.0.0.1:8190", GRAPH, prompt_id="p2", meta={"pipeline": "2d.edit"})
    cw.record("http://127.0.0.1:8188", GRAPH, prompt_id=None, error="ComfyUI rejeitou o workflow: value_not_in_list")
    items = {i["prompt_id"] or "rej": i for i in cw.listing()}
    assert items["p1"]["status"] == "running" and items["p1"]["instance"] == "trellis"
    assert items["p2"]["status"] == "error" and items["p2"]["error"] == "sem VRAM"
    assert items["rej"]["status"] == "rejected" and "value_not_in_list" in items["rej"]["error"]
    assert items["p1"]["models"] == ["b.safetensors"] and items["p1"]["nodes"] == 5
    assert cw.load("p1")["meta"]["project"] == "x"
    try:
        cw.load("../../etc")
        raise AssertionError("deveria recusar")
    except KeyError:
        pass

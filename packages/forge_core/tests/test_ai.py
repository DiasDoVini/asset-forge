"""IA do Asset Forge: conversão de histórico por provedor, laço do agente e ferramentas do chat (sem rede)."""
import json

import pytest

from forge_core import ai_providers as ai
from forge_core import chat as chatmod


HISTORY = [
    {"role": "user", "text": "gere um baú"},
    {"role": "assistant", "text": "vou gerar", "tool_calls": [{"id": "t1", "name": "run_pipeline", "input": {"pipeline": "2d.text2d"}},
                                                              {"id": "t2", "name": "list_assets", "input": {}}],
     "raw": {"provider": "claude", "model": "claude-opus-5-5", "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                                                                          {"type": "text", "text": "vou gerar"},
                                                                          {"type": "tool_use", "id": "t1", "name": "run_pipeline", "input": {"pipeline": "2d.text2d"}},
                                                                          {"type": "tool_use", "id": "t2", "name": "list_assets", "input": {}}]}},
    {"role": "tool", "tool_call_id": "t1", "name": "run_pipeline", "result": '{"ref": "bau@v001"}', "images": []},
    {"role": "tool", "tool_call_id": "t2", "name": "list_assets", "result": "[]", "is_error": True},
]


def test_to_claude_groups_results_and_reuses_raw_on_same_model():
    msgs = ai._to_claude(HISTORY, "claude-opus-5-5")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]       # resultados juntos numa mensagem
    assert msgs[1]["content"][0]["type"] == "thinking"                       # conteúdo original (raciocínio) intacto
    results = msgs[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and results[1]["is_error"] is True
    other = ai._to_claude(HISTORY, "claude-sonnet-5-5")                      # outro modelo: reconstruído sem raciocínio
    assert [b["type"] for b in other[1]["content"]] == ["text", "tool_use", "tool_use"]


def test_to_ollama_without_vision_drops_images_and_says_so(tmp_path):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(img)
    hist = [{"role": "user", "text": "oi"}, {"role": "tool", "tool_call_id": "t", "name": "view_asset", "result": "ok", "images": [str(img)]}]
    with_v = ai._to_ollama(hist, "sys", vision=True)
    assert with_v[0]["role"] == "system" and "images" in with_v[2]
    no_v = ai._to_ollama(hist, "sys", vision=False)
    assert "images" not in no_v[2] and "não vê imagens" in no_v[2]["content"]


def test_run_agent_executes_tools_until_answer(monkeypatch):
    calls = iter([
        {"text": "", "tool_calls": [{"id": "c1", "name": "soma", "input": {"a": 2, "b": 3}}], "raw": {}, "model": "m"},
        {"text": "", "tool_calls": [{"id": "c2", "name": "nao_existe", "input": {}}], "raw": {}, "model": "m"},
        {"text": "deu 5", "tool_calls": [], "raw": {}, "model": "m"},
    ])
    seen_histories = []

    def fake_complete(cfg, system, history, tools=None, json_schema=None):
        seen_histories.append(len(history))
        return next(calls)

    monkeypatch.setattr(ai, "complete", fake_complete)
    tools = [ai.Tool("soma", "", {"type": "object"}, lambda a: ai.ToolResult(str(a["a"] + a["b"]), data={"x": 1}))]
    out = []
    hist = [{"role": "user", "text": "2+3?"}]
    ai.run_agent({"provider": "local"}, "sys", hist, tools, on_message=out.append)
    assert [m["role"] for m in out] == ["assistant", "tool", "assistant", "tool", "assistant"]
    assert out[1]["result"] == "5" and out[1]["data"] == {"x": 1}
    assert out[3]["is_error"] and "desconhecida" in out[3]["result"]
    assert out[-1]["text"] == "deu 5" and seen_histories == [1, 3, 5]


def test_run_agent_stops_when_cancelled(monkeypatch):
    monkeypatch.setattr(ai, "complete", lambda *a, **k: pytest.fail("não deveria chamar o modelo"))
    out = []
    ai.run_agent({"provider": "local"}, "sys", [], [], on_message=out.append, cancel=lambda: True)
    assert out[0]["text"] == "(interrompido)"


class _P:
    slug = "p"
    root = None

    def summary(self):
        return {"name": "P", "style": {}}


def test_chat_run_pipeline_tool_is_forgiving_and_explains(tmp_path):
    from forge_core.pipeline import get_registry

    get_registry()
    submitted = []

    def fake_submit(req):
        submitted.append(req)
        return {"id": "j1", "status": "error", "error": "falhou de propósito"}

    tools = {t.name: t for t in chatmod.tools_for(_P(), fake_submit)}
    # input_asset fora de params é movido para dentro; output_asset vira o slug de saída
    r = tools["run_pipeline"].run({"pipeline": "2d.pixelize", "input_asset": "bau", "output_asset": "bau-px", "params": {"size": 24}})
    assert submitted[-1]["params"] == {"size": 24, "input_asset": "bau"} and submitted[-1]["asset"] == "bau-px"
    assert r.is_error and "falhou de propósito" in r.text
    # parâmetro inventado: não envia e lista os aceitos
    r = tools["run_pipeline"].run({"pipeline": "2d.pixelize", "params": {"input_asset": "bau", "palette_name": "wood"}})
    assert r.is_error and "palette_name" in r.text and "size" in r.text and len(submitted) == 1
    pp = json.loads(tools["pipeline_params"].run({"pipeline": "2d.pixelize"}).text)
    assert "size" in pp["params"] and pp["pipeline"] == "2d.pixelize"
    names = [p["id"] for p in json.loads(tools["list_pipelines"].run({"modality": "2d"}).text)]
    assert "2d.text2d" in names and all(n.startswith("2d.") for n in names)


def test_chat_store_roundtrip(tmp_path):
    class P:
        root = tmp_path
        slug = "p"

    c = chatmod.create(P(), provider="local", model="qwen3:8b")
    c["messages"].append({"role": "user", "text": "oi"})
    chatmod.save(P(), c)
    assert chatmod.load(P(), c["id"])["messages"][0]["text"] == "oi"
    assert chatmod.list_chats(P())[0]["messages"] == 1
    with pytest.raises(KeyError):
        chatmod.load(P(), "../x")
    chatmod.delete(P(), c["id"])
    assert chatmod.list_chats(P()) == []

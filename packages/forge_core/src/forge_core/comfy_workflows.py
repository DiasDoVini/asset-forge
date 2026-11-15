"""Workflows que o Asset Forge mandou para o ComfyUI: registro, estado ao vivo e "abrir no ComfyUI".

- `record()` é chamado pelo ComfyClient a cada envio (aceito ou rejeitado): grava o grafo (formato API), a instância,
  o projeto/pipeline e o erro de validação, em `<projetos>/.forge/workflows/` (compartilhado por servidor, MCP e CLI).
- `listing()` junta esses registros com a fila e o histórico de cada instância (rodando, na fila, concluído, erro).
- `to_ui()` converte o grafo API para o formato de UI (nós com posição, links, widgets) usando o `object_info` da
  instância; `publish()` grava esse JSON na pasta `example_workflows` do nó `asset_forge_workflows` instalado na
  instância, e o frontend do ComfyUI o abre por `/?template=<nome>&source=asset_forge_workflows`.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

MODULE = "asset_forge_workflows"     # nome da pasta do nó em custom_nodes (vira o "source" do template)
KEEP = 400                           # registros guardados


def _dir() -> Path:
    from .config import get_settings

    d = get_settings().projects_dir / ".forge" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def record(url: str, graph: dict[str, Any], *, prompt_id: str | None, meta: dict[str, Any] | None = None,
           error: str | None = None) -> None:
    """Nunca levanta: registrar não pode atrapalhar o envio."""
    try:
        rid = prompt_id or f"rejected-{int(time.time() * 1000)}"
        data = {"id": rid, "prompt_id": prompt_id, "url": url.rstrip("/"), "created": time.time(), "graph": graph,
                "meta": meta or {}, "error": error, "pid": os.getpid()}
        (_dir() / f"{rid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-KEEP]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def load(rid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", rid):
        raise KeyError(rid)
    p = _dir() / f"{rid}.json"
    if not p.exists():
        raise KeyError(rid)
    return json.loads(p.read_text(encoding="utf-8"))


def _instance_name(url: str) -> str:
    from . import comfy_supervisor as cs

    return next((i.name for i in cs.INSTANCES if i.url == url.rstrip("/")), url)


def _live(url: str) -> dict[str, Any] | None:
    """Fila e histórico recente de uma instância (None se fora do ar)."""
    try:
        with httpx.Client(base_url=url, timeout=3) as c:
            q = c.get("/queue").json()
            h = c.get("/history", params={"max_items": 200}).json()
    except (httpx.HTTPError, ValueError):
        return None
    running = {e[1]: e for e in q.get("queue_running", [])}
    pending = {e[1]: e for e in q.get("queue_pending", [])}
    return {"running": running, "pending": pending, "history": h}


def _summary(graph: dict[str, Any]) -> list[str]:
    seen: dict[str, int] = {}
    for n in graph.values():
        t = n.get("class_type", "?")
        seen[t] = seen.get(t, 0) + 1
    return [t if k == 1 else f"{t} ×{k}" for t, k in seen.items()]


def _models(graph: dict[str, Any]) -> list[str]:
    out = []
    for n in graph.values():
        for k, v in (n.get("inputs") or {}).items():
            if isinstance(v, str) and k.endswith("_name") and v.endswith((".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin")):
                out.append(v)
    return sorted(set(out))


def listing(limit: int = 100) -> list[dict[str, Any]]:
    """Registros mais recentes com o estado ao vivo; inclui o que está na fila das instâncias sem registro
    (enviado por outra ferramenta)."""
    from . import comfy_supervisor as cs

    files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    recs = []
    for f in files:
        try:
            recs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    urls = {r["url"] for r in recs} | {i.url for i in cs.INSTANCES if cs.port_open(i.port, 0.2)}
    live = {u: _live(u) for u in urls}
    known = {r.get("prompt_id") for r in recs}
    out: list[dict[str, Any]] = []
    for r in recs:
        st, msg, done_at = "unknown", r.get("error"), None
        lv = live.get(r["url"])
        pid = r.get("prompt_id")
        if r.get("error") and not pid:
            st = "rejected"
        elif lv is None:
            st = "offline"
        elif pid in lv["running"]:
            st = "running"
        elif pid in lv["pending"]:
            st = "pending"
        elif pid in lv["history"]:
            s = lv["history"][pid].get("status", {})
            st = "done" if s.get("status_str") == "success" or s.get("completed") else "error"
            if st == "error":
                errs = [m[1] for m in s.get("messages", []) if m[0] == "execution_error"]
                msg = errs[0].get("exception_message") if errs else msg
            ends = [m[1].get("timestamp") for m in s.get("messages", []) if m[0] in ("execution_success", "execution_error")]
            done_at = ends[-1] / 1000 if ends and ends[-1] else None
        out.append({"id": r["id"], "prompt_id": pid, "url": r["url"], "instance": _instance_name(r["url"]), "status": st,
                    "created": r["created"], "finished": done_at, "meta": r.get("meta") or {}, "error": msg,
                    "nodes": len(r.get("graph") or {}), "summary": _summary(r.get("graph") or {}),
                    "models": _models(r.get("graph") or {})})
    # na fila das instâncias mas sem registro (ex.: alguém usando o ComfyUI direto)
    for u, lv in live.items():
        if not lv:
            continue
        for state in ("running", "pending"):
            for pid, e in lv[state].items():
                if pid in known:
                    continue
                g = e[2] if len(e) > 2 and isinstance(e[2], dict) else {}
                out.append({"id": None, "prompt_id": pid, "url": u, "instance": _instance_name(u), "status": state,
                            "created": None, "finished": None, "meta": {"external": True}, "error": None,
                            "nodes": len(g), "summary": _summary(g), "models": _models(g)})
    order = {"running": 0, "pending": 1}
    out.sort(key=lambda x: (order.get(x["status"], 2), -(x["created"] or time.time())))
    return out


# ---------------------------------------------------------------- API -> UI

_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


def _is_widget(spec: Any) -> bool:
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    t = spec[0]
    return isinstance(t, list) or t in _WIDGET_TYPES


def _default(spec: Any) -> Any:
    t = spec[0]
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if "default" in opts:
        return opts["default"]
    if isinstance(t, list):
        return t[0] if t else ""
    if t == "COMBO":
        o = opts.get("options") or []
        return o[0] if o else ""
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(t)


def to_ui(graph: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Converte um prompt (formato API) para um workflow de UI que o frontend do ComfyUI carrega."""
    ids = {nid: i + 1 for i, nid in enumerate(graph)}
    depth: dict[str, int] = {}     # profundidade = coluna (da esquerda para a direita)

    def d(nid: str, stack: tuple = ()) -> int:
        if nid in depth:
            return depth[nid]
        if nid in stack:
            return 0
        parents = [v[0] for v in (graph[nid].get("inputs") or {}).values()
                   if isinstance(v, list) and len(v) == 2 and str(v[0]) in graph]
        depth[nid] = 1 + max((d(str(p), stack + (nid,)) for p in parents), default=-1)
        return depth[nid]

    for nid in graph:
        d(nid)
    col_y: dict[int, float] = {}
    nodes, links = [], []
    out_links: dict[tuple[str, int], list[int]] = {}
    link_id = 0
    for nid, n in graph.items():
        cls = n.get("class_type", "")
        spec = info.get(cls, {})
        req = (spec.get("input") or {}).get("required") or {}
        opt = (spec.get("input") or {}).get("optional") or {}
        order = spec.get("input_order") or {}
        names = list(order.get("required") or req) + list(order.get("optional") or opt)
        all_specs = {**req, **opt}
        inputs, widgets = [], []
        api_in = n.get("inputs") or {}
        for name in names + [k for k in api_in if k not in names]:
            s = all_specs.get(name)
            v = api_in.get(name)
            linked = isinstance(v, list) and len(v) == 2 and str(v[0]) in graph
            widget = s is not None and _is_widget(s)
            if linked:
                link_id += 1
                src, slot = str(v[0]), int(v[1])
                out_types = info.get(graph[src].get("class_type", ""), {}).get("output") or []
                ltype = out_types[slot] if slot < len(out_types) else "*"
                ltype = ltype if isinstance(ltype, str) else "COMBO"
                links.append([link_id, ids[src], slot, ids[nid], len(inputs), ltype])
                out_links.setdefault((src, slot), []).append(link_id)
                entry: dict[str, Any] = {"name": name, "type": ltype, "link": link_id}
                if widget:
                    entry["widget"] = {"name": name}
                inputs.append(entry)
            elif s is not None and not widget:
                inputs.append({"name": name, "type": s[0] if isinstance(s[0], str) else "COMBO", "link": None})
            if widget:
                widgets.append(v if not linked and v is not None else _default(s))
                opts = s[1] if len(s) > 1 and isinstance(s[1], dict) else {}
                if opts.get("control_after_generate") or (name in ("seed", "noise_seed") and s[0] == "INT"):
                    widgets.append("fixed")
        outs = []
        names_out = spec.get("output_name") or []
        for slot, t in enumerate(spec.get("output") or []):
            outs.append({"name": names_out[slot] if slot < len(names_out) else str(t), "type": t if isinstance(t, str) else "COMBO",
                         "links": [], "slot_index": slot})
        col = depth.get(nid, 0)
        y = col_y.get(col, 0.0)
        h = 60 + 26 * (len(inputs) + len(widgets)) + 20 * len(outs)
        col_y[col] = y + h + 40
        node: dict[str, Any] = {"id": ids[nid], "type": cls, "pos": [40 + col * 360, 40 + y], "size": [300, h],
                                "flags": {}, "order": 0, "mode": 0, "inputs": inputs, "outputs": outs,
                                "properties": {"Node name for S&R": cls}, "widgets_values": widgets}
        title = (n.get("_meta") or {}).get("title")
        if title:
            node["title"] = title
        nodes.append(node)
    by_id = {nd["id"]: nd for nd in nodes}
    for (src, slot), lids in out_links.items():
        outs = by_id[ids[src]]["outputs"]
        if slot < len(outs):
            outs[slot]["links"] = lids
    for i, nd in enumerate(sorted(nodes, key=lambda x: (x["pos"][0], x["pos"][1]))):
        nd["order"] = i
    return {"last_node_id": len(nodes), "last_link_id": link_id, "nodes": nodes, "links": links, "groups": [],
            "config": {}, "extra": {"asset_forge": True, "ds": {"scale": 0.75, "offset": [0, 0]}}, "version": 0.4}


def publish(rid: str) -> dict[str, Any]:
    """Gera o workflow de UI de um registro e devolve como abrir no ComfyUI da mesma instância."""
    rec = load(rid)
    url = rec["url"]
    with httpx.Client(base_url=url, timeout=20) as c:
        info = c.get("/object_info").json()
        ui = to_ui(rec["graph"], info)
        meta = rec.get("meta") or {}
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"af_{meta.get('pipeline', 'workflow')}_{rid[:8]}")
        out: dict[str, Any] = {"name": name, "workflow": ui, "instance": _instance_name(url), "url": url, "open_url": None}
        target = workflows_dir_for(url)
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{name}.json").write_text(json.dumps(ui, ensure_ascii=False), encoding="utf-8")
            # a rota estática só existe se o nó foi carregado quando a instância subiu
            if c.get(f"/api/workflow_templates/{MODULE}/{name}.json").status_code == 200:
                out["open_url"] = f"{url}/?template={name}&source={MODULE}"
    if not out["open_url"]:
        out["hint"] = ("A instância ainda não carregou o nó asset_forge_workflows: reinicie-a em Opções > ComfyUI ou "
                       "arraste o arquivo .json baixado para a janela do ComfyUI.")
    return out


# ---------------------------------------------------------------- nó auxiliar nas instâncias

NODE_SRC = Path(__file__).resolve().parents[4] / "comfy" / "custom_nodes" / MODULE


def custom_nodes_dirs() -> dict[str, Path]:
    """Pasta custom_nodes de cada instância conhecida."""
    from . import comfy_supervisor as cs

    root = cs.ROOT
    out = {"3d": root / "comfy/instance/ComfyUI/custom_nodes", "trellis": root / "comfy/instance-trellis/ComfyUI/custom_nodes",
           "desktop": Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Installs" / "ComfyUI" / "ComfyUI" / "custom_nodes"}
    return {k: v for k, v in out.items() if v.is_dir()}


def workflows_dir_for(url: str) -> Path | None:
    from . import comfy_supervisor as cs

    name = next((i.name for i in cs.INSTANCES if i.url == url.rstrip("/")), None)
    d = custom_nodes_dirs().get(name or "")
    return (d / MODULE / "example_workflows") if d else None


def install_node() -> list[str]:
    """Liga (junction/symlink) o nó asset_forge_workflows em cada instância; vale após reiniciar a instância."""
    done = []
    for name, d in custom_nodes_dirs().items():
        link = d / MODULE
        if link.exists():
            continue
        if os.name == "nt":
            import subprocess

            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(NODE_SRC)], capture_output=True, check=True)
        else:
            link.symlink_to(NODE_SRC, target_is_directory=True)
        done.append(name)
    return done

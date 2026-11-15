"""CLI `forge`. Toda saída estruturada aceita --json (pensado para agentes)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer

from .config import get_settings
from .pipeline import get_registry
from .project import ProjectRegistry, Style

app = typer.Typer(help="Asset Forge — gerador local de assets 2D/3D", no_args_is_help=True)
project_app = typer.Typer(help="Projetos (estilo global, referências)", no_args_is_help=True)
asset_app = typer.Typer(help="Assets e versões", no_args_is_help=True)
comfy_app = typer.Typer(help="ComfyUI", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(asset_app, name="asset")
app.add_typer(comfy_app, name="comfy")

JsonOpt = typer.Option(False, "--json", help="saída em JSON")
ProjOpt = typer.Option(None, "--project", "-p", envvar="FORGE_PROJECT", help="slug ou caminho do projeto")


def _registry() -> ProjectRegistry:
    return ProjectRegistry(get_settings().projects_dir)


def _project(ref: str | None):
    if not ref:
        _fail("informe o projeto com -p/--project ou FORGE_PROJECT")
    try:
        return _registry().resolve(ref)
    except FileNotFoundError as e:
        _fail(str(e))


def _out(data: Any, as_json: bool, human: str | None = None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        typer.echo(human if human is not None else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(msg: str, as_json: bool = False) -> None:
    if as_json:
        sys.stdout.write(json.dumps({"error": msg}, ensure_ascii=False) + "\n")
    else:
        typer.secho(f"erro: {msg}", fg="red", err=True)
    raise typer.Exit(1)


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"esperado chave=valor: {pair}")
        k, v = pair.split("=", 1)
        out[k.strip()] = _parse_value(v)
    return out


def _asset_view(project, av) -> dict[str, Any]:
    d = av.model_dump(mode="json")
    d["ref"] = av.ref
    d["dir"] = str(project.store.version_dir(av.slug, av.version))
    for f in d["files"]:
        f["abs_path"] = str(project.store.version_dir(av.slug, av.version) / f["path"])
    return d


# ---- projetos -------------------------------------------------------------
@project_app.command("new")
def project_new(slug: str, name: Optional[str] = typer.Option(None), description: str = "",
                style_prompt: str = typer.Option("", "--style", help="prompt de estilo global"),
                negative: str = typer.Option("", help="negative prompt global"),
                system_prompt: str = typer.Option("", help="instruções para agentes/LLM ao escrever prompts"),
                palette: Optional[str] = typer.Option(None, help="cores hex separadas por vírgula"),
                as_json: bool = JsonOpt) -> None:
    style = Style(prompt=style_prompt, negative=negative, system_prompt=system_prompt,
                  palette=[c.strip() for c in palette.split(",")] if palette else [])
    try:
        p = _registry().create(slug, name=name, description=description, style=style)
    except FileExistsError as e:
        _fail(str(e), as_json)
    _out(p.summary(), as_json, f"projeto criado: {p.root}")


@project_app.command("list")
def project_list(as_json: bool = JsonOpt) -> None:
    ps = [p.summary() for p in _registry().list()]
    _out(ps, as_json, "\n".join(f"{p['slug']:24} {p['name']}" for p in ps) or "(nenhum projeto)")


@project_app.command("show")
def project_show(project: Optional[str] = typer.Argument(None, envvar="FORGE_PROJECT"), as_json: bool = JsonOpt) -> None:
    p = _project(project)
    _out(p.summary(), as_json)


@project_app.command("set")
def project_set(assignments: list[str] = typer.Argument(..., help='ex.: style.prompt="pixel art 16-bit" 2d.pixel_size=2'),
                project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Altera valores do project.yaml por caminho pontuado. Valores são lidos como JSON quando possível."""
    p = _project(project)
    for k, v in _parse_kv(assignments).items():
        p.set_value(k, v)
    _out(p.summary(), as_json, "ok")


@project_app.command("nsfw")
def project_nsfw(state: str = typer.Argument(..., help="on | off"), project: Optional[str] = ProjOpt,
                 i_am_18: bool = typer.Option(False, "--i-am-18", help="confirma que você tem 18 anos ou mais"),
                 as_json: bool = JsonOpt) -> None:
    """Liga/desliga o modo +18 do projeto (conteúdo adulto; referências a menores são sempre recusadas)."""
    p = _project(project)
    if state not in ("on", "off"):
        raise typer.BadParameter("use on ou off")
    try:
        p.set_nsfw(state == "on", confirm_adult=i_am_18)
    except PermissionError as e:
        typer.echo(f"{e} (use --i-am-18)", err=True)
        raise typer.Exit(1)
    _out(p.summary(), as_json, f"modo +18: {state}")


@project_app.command("add-ref")
def project_add_ref(files: list[Path], project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    names = [p.add_ref(f) for f in files]
    _out({"refs": names}, as_json, "\n".join(names))


# ---- pipelines ------------------------------------------------------------
@app.command("pipelines")
def pipelines(modality: Optional[str] = typer.Option(None, "--modality", "-m"), as_json: bool = JsonOpt) -> None:
    """Lista pipelines disponíveis (com schema de parâmetros em --json)."""
    reg = get_registry()
    items = [p.describe() for p in reg.pipelines.values() if not modality or p.modality == modality]
    human = "\n".join(f"{p['id']:24} [{','.join(p['resources'])}] {p['title']}" for p in items)
    if reg.errors:
        human += "\n\nmodalidades com erro: " + json.dumps(reg.errors, ensure_ascii=False)
    _out(items, as_json, human or "(nenhum pipeline registrado)")


@app.command("describe")
def describe(pipeline_id: str, as_json: bool = JsonOpt) -> None:
    """Mostra parâmetros de um pipeline."""
    try:
        d = get_registry().get(pipeline_id).describe()
    except KeyError as e:
        _fail(str(e), as_json)
    if as_json:
        _out(d, True)
        return
    typer.echo(f"{d['id']} — {d['title']}\n{d['description']}\nrecursos: {d['resources']}\n")
    props = d["params_schema"].get("properties", {})
    required = set(d["params_schema"].get("required", []))
    for name, spec in props.items():
        t = spec.get("type") or "/".join(x.get("type", "?") for x in spec.get("anyOf", []))
        default = "" if name in required else f" = {spec.get('default')!r}"
        typer.echo(f"  {name}: {t}{default}  {spec.get('description', '')}")


@app.command("run")
def run(pipeline_id: str,
        param: list[str] = typer.Option([], "--param", "-P", help="chave=valor (valor em JSON quando possível)"),
        params_json: Optional[str] = typer.Option(None, "--params", help="objeto JSON com todos os parâmetros"),
        input_asset: Optional[str] = typer.Option(None, "--input", "-i", help="slug, slug@v003 ou arquivo"),
        asset: Optional[str] = typer.Option(None, "--asset", "-a", help="slug de destino (nova versão)"),
        name: Optional[str] = None,
        tag: list[str] = typer.Option([], "--tag"),
        folder: Optional[str] = typer.Option(None, "--folder", "-f", help="pasta do asset (ex.: Personagens/Cavaleiro)"),
        category: Optional[str] = typer.Option(None, "--category", "-c", help="id da categoria (character, item...)"),
        project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Executa um pipeline e grava o resultado como nova versão de asset (registrado em `forge jobs`)."""
    p = _project(project)
    params: dict[str, Any] = json.loads(params_json) if params_json else {}
    params.update(_parse_kv(param))
    if input_asset:
        params["input_asset"] = input_asset
    _run_and_report(p, pipeline_id, params, asset, name, tag, as_json, folder=folder, category=category)


def _run_and_report(p, pipeline_id: str, params: dict[str, Any], asset: str | None, name: str | None,
                    tag: list[str], as_json: bool, retry_of: str | None = None, folder: str | None = None,
                    category: str | None = None) -> None:
    from .jobstore import record_direct_run
    from .runner import PipelineError, run_pipeline

    last = {"pct": -1}

    def on_progress(v: float, msg: str | None) -> None:
        pct = int(v * 100)
        if not as_json and pct != last["pct"]:
            last["pct"] = pct
            typer.echo(f"\r{pct:3d}% {msg or ''}".ljust(60), nl=False, err=True)

    def run(logs: list[str]):
        def on_log(line: str) -> None:
            logs.append(line)
            if not as_json:
                typer.echo(f"\n{line}", err=True)

        return run_pipeline(p, pipeline_id, params, asset=asset, name=name, tags=tag,
                            on_progress=on_progress, on_log=on_log, folder=folder, category=category)

    try:
        av = record_direct_run("cli", p.slug, pipeline_id, params, asset, name, tag, run, retry_of=retry_of)
    except (PipelineError, KeyError) as e:
        _fail(str(e).strip("'\""), as_json)
    except Exception as e:
        _fail(f"{type(e).__name__}: {e}", as_json)
    view = _asset_view(p, av)
    human = f"\n{av.ref}\n" + "\n".join(f"  [{f['role']}] {f['abs_path']}" for f in view["files"])
    _out(view, as_json, human)


@app.command("export")
def export(ref: str, exporter_id: Optional[str] = typer.Argument(None, help="ex.: 2d.gif (vazio = lista os aplicáveis)"),
           param: list[str] = typer.Option([], "--param", "-P", help="chave=valor"),
           params_json: Optional[str] = typer.Option(None, "--params"),
           out: Path = typer.Option(Path("."), "--out", "-o", help="pasta de saída"),
           project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Exporta uma versão de asset (GIF, sprite sheet, frames...) sem criar versão nova."""
    from .export import run_export
    from .runner import PipelineError

    p = _project(project)
    if not exporter_id:
        try:
            av = p.store.get(ref)
        except (FileNotFoundError, ValueError) as e:
            _fail(str(e), as_json)
        items = [e.describe() for e in get_registry().exporters_for(av)]
        _out(items, as_json, "\n".join(f"{e['id']:20} {e['title']}" for e in items) or "(nenhum exportador para este asset)")
        return
    params: dict[str, Any] = json.loads(params_json) if params_json else {}
    params.update(_parse_kv(param))
    try:
        files = run_export(p, ref, exporter_id, params, out)
    except (PipelineError, KeyError, FileNotFoundError, ValueError) as e:
        _fail(str(e).strip("'\""), as_json)
    _out({"files": [str(f.resolve()) for f in files]}, as_json, "\n".join(str(f.resolve()) for f in files))



@app.command("jobs")
def jobs_cmd(project: Optional[str] = typer.Option(None, "--project", "-p", help="filtra por projeto"),
             failed: bool = typer.Option(False, "--failed", help="só erros/cancelados"),
             status: Optional[str] = typer.Option(None, help="ex.: error,cancelled"),
             limit: int = 30, as_json: bool = JsonOpt) -> None:
    """Histórico de jobs (servidor, CLI e MCP)."""
    from .jobstore import default_store

    rows = default_store().list(project=project, status="error,cancelled" if failed else status, limit=limit)
    for r in rows:
        r.pop("logs", None)
    human = "\n".join(
        f"{r['id']}  {r['created'][:19]}  {r['status']:9} {r['pipeline']:16} {r['project']:12} "
        f"{(r['result'] or r['error'] or '')[:70]}" for r in rows)
    _out(rows, as_json, human or "(histórico vazio)")


@app.command("retry")
def retry_cmd(job_id: str, param: list[str] = typer.Option([], "--param", "-P", help="sobrescreve parâmetros"),
              new_seed: bool = typer.Option(False, "--new-seed", help="variação: descarta a seed"),
              as_json: bool = JsonOpt) -> None:
    """Repete um job do histórico (mesmo pipeline/parâmetros), opcionalmente alterando parâmetros."""
    from .jobstore import default_store

    old = default_store().get(job_id)
    if not old:
        _fail(f"job não encontrado: {job_id}", as_json)
    req = old["request"]
    params = {**req.get("params", {}), **_parse_kv(param)}
    if new_seed:
        params.pop("seed", None)
    p = _project(req["project"])
    _run_and_report(p, req["pipeline"], params, req.get("asset"), req.get("name"), req.get("tags") or [], as_json,
                    retry_of=job_id)

# ---- assets ---------------------------------------------------------------
@asset_app.command("list")
def asset_list(q: str = typer.Option("", "--q", help="busca em nome/prompt/tags"),
               modality: Optional[str] = typer.Option(None, "--modality", "-m"),
               kind: Optional[str] = None, tag: Optional[str] = None,
               base: Optional[str] = typer.Option(None, help="só derivados deste asset (ex.: animações de um personagem)"),
               folder: Optional[str] = typer.Option(None, "--folder", "-f", help="pasta ('' = raiz)"),
               recursive: bool = typer.Option(False, "--recursive", "-r", help="inclui subpastas"),
               category: Optional[str] = typer.Option(None, "--category", "-c"),
               project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    rows = p.store.search(q=q, modality=modality, kind=kind, tag=tag, base=base, folder=folder,
                          recursive=recursive, category=category)
    human = "\n".join(f"{r['ref']:32} {r['modality']:3} {r['kind']:9} {('/' + r['folder']):24} {r['category'] or '':10} {r['prompt'][:40]}" for r in rows)
    _out(rows, as_json, human or "(nenhum asset)")


@asset_app.command("show")
def asset_show(ref: str, versions: bool = typer.Option(False, "--versions", help="todas as versões"),
               project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    try:
        if versions:
            data: Any = [_asset_view(p, v) for v in p.store.versions(ref.split("@")[0])]
        else:
            data = _asset_view(p, p.store.get(ref))
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e), as_json)
    _out(data, as_json)


@asset_app.command("import")
def asset_import(file: Path, asset: Optional[str] = typer.Option(None, "--asset", "-a"),
                 modality: Optional[str] = typer.Option(None, "--modality", "-m", help="padrão: pela extensão"),
                 kind: Optional[str] = typer.Option(None, help="padrão: pela extensão"), parent: Optional[str] = None,
                 tag: list[str] = typer.Option([], "--tag"),
                 folder: Optional[str] = typer.Option(None, "--folder", "-f"),
                 category: Optional[str] = typer.Option(None, "--category", "-c"),
                 option: list[str] = typer.Option([], "--option", "-O", help="opção do formato chave=valor (JSON), ex.: -O columns=8"),
                 project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Importa um arquivo como asset (novo ou nova versão de --asset). Formatos com importer (.aseprite, gif
    animado, folha de sprites, áudio, vídeo...) são convertidos; `forge asset formats` lista todos."""
    p = _project(project)
    if not file.exists():
        _fail(f"arquivo não existe: {file}", as_json)
    opts: dict = {}
    for kv in option:
        k, _, v = kv.partition("=")
        try:
            opts[k] = json.loads(v)
        except ValueError:
            opts[k] = v
    av = p.store.import_file(file, slug=asset, modality=modality, kind=kind, parent=parent, tags=tag,
                             folder=folder, category=category, options=opts)
    _out(_asset_view(p, av), as_json, av.ref)


@asset_app.command("formats")
def asset_formats(as_json: bool = JsonOpt) -> None:
    """Lista os formatos importáveis (com importer) e as opções de cada um."""
    data = [i.describe() for i in get_registry().importers.values()]
    lines = [f"{i['id']:<22} {', '.join(i['exts'])}  — {i['title']}" for i in data]
    _out(data, as_json, "\n".join(lines))



@asset_app.command("move")
def asset_move(slugs: list[str], folder: Optional[str] = typer.Option(None, "--folder", "-f", help="pasta destino ('' = raiz)"),
               category: Optional[str] = typer.Option(None, "--category", "-c", help="categoria ('' remove)"),
               project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Organiza assets: move para uma pasta e/ou define a categoria."""
    p = _project(project)
    out = []
    for s in slugs:
        m = p.store.set_meta(s.split("@")[0], folder=folder, category=category or None,
                             clear_category=category == "")
        out.append({"slug": s, **m.model_dump()})
    if folder:
        _remember_folder(p, folder)
    _out(out, as_json, "\n".join(f"{o['slug']} -> /{o['folder']} [{o['category'] or '-'}]" for o in out))


def _remember_folder(p, folder: str) -> None:
    from .project import normalize_folder

    f = normalize_folder(folder)
    if f and f not in p.config.folders:
        p.config.folders.append(f)
        p.save()


folder_app = typer.Typer(help="Pastas do projeto", no_args_is_help=True)
app.add_typer(folder_app, name="folder")


@folder_app.command("list")
def folder_list(project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    counts = p.store.folder_counts()
    paths = sorted(set(p.config.folders) | {f for f in counts if f}, key=str.lower)
    data = [{"path": f, "count": counts.get(f, 0)} for f in paths]
    human = f"/ ({counts.get('', 0)})\n" + "\n".join(f"/{d['path']} ({d['count']})" for d in data)
    _out({"root_count": counts.get("", 0), "folders": data,
          "categories": [c.model_dump() for c in p.config.categories]}, as_json, human)


@folder_app.command("create")
def folder_create(path: str, project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    from .project import normalize_folder

    p = _project(project)
    _remember_folder(p, path)
    _out({"path": normalize_folder(path)}, as_json, "/" + normalize_folder(path))


@folder_app.command("rename")
def folder_rename(old: str, new: str, project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    from .project import normalize_folder

    p = _project(project)
    o, n = normalize_folder(old), normalize_folder(new)
    try:
        moved = p.store.move_folder(o, n)
    except ValueError as e:
        _fail(str(e), as_json)
    p.config.folders = sorted({(n + f[len(o):]) if (f == o or f.startswith(o + "/")) else f
                               for f in p.config.folders} | {n})
    p.save()
    _out({"moved": moved, "path": n}, as_json, f"{moved} assets movidos para /{n}")


@folder_app.command("remove")
def folder_remove(path: str, project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Remove a pasta; os assets sobem para a pasta-mãe (nada é apagado)."""
    from .project import normalize_folder

    p = _project(project)
    f = normalize_folder(path)
    parent = f.rsplit("/", 1)[0] if "/" in f else ""
    moved = 0
    for s in p.store.slugs():
        cur = p.store.get_meta(s).folder
        if cur == f or cur.startswith(f + "/"):
            p.store.set_meta(s, folder=parent)
            moved += 1
    p.config.folders = [x for x in p.config.folders if not (x == f or x.startswith(f + "/"))]
    p.save()
    _out({"moved": moved, "to": parent}, as_json, f"{moved} assets movidos para /{parent}")


@asset_app.command("tag")
def asset_tag(ref: str, tags: list[str], project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    av = p.store.get(ref)
    av = p.store.update_meta(av.ref, tags=sorted(set(av.tags) | set(tags)))
    _out(_asset_view(p, av), as_json, f"{av.ref}: {av.tags}")


@asset_app.command("delete")
def asset_delete(ref: str, project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    """Move para .forge/trash (recuperável)."""
    p = _project(project)
    p.store.delete(ref)
    _out({"deleted": ref}, as_json, f"movido para lixeira: {ref}")


@asset_app.command("reindex")
def asset_reindex(project: Optional[str] = ProjOpt, as_json: bool = JsonOpt) -> None:
    p = _project(project)
    _out({"indexed": p.store.reindex()}, as_json)


# ---- comfy / servidores ---------------------------------------------------
@comfy_app.command("instances")
def comfy_instances(as_json: bool = JsonOpt) -> None:
    """Estado de todas as instâncias do ComfyUI (desktop :8188, 3d :8189, trellis :8190)."""
    from .comfy_supervisor import status

    rows = status()
    human = "\n".join(f"{r['name']:8} :{r['port']}  {'ONLINE ' if r['ready'] else ('subindo' if r['listening'] else 'offline')}"
                      f"  {'' if r['installed'] else '(não instalada) '}{r['title']}" for r in rows)
    _out(rows, as_json, human)


@comfy_app.command("up")
def comfy_up(only: list[str] = typer.Option([], "--only", help="desktop, 3d, trellis (padrão: todas)")) -> None:
    """Sobe e monitora as instâncias do ComfyUI (adota as que já estão rodando; reinicia se caírem). Ctrl+C sai."""
    from .comfy_supervisor import ComfySupervisor

    sup = ComfySupervisor(names=only or None, log=lambda m: typer.echo(m, err=True))
    try:
        sup.run()
    except KeyboardInterrupt:
        sup.stop_event.set()


@comfy_app.command("start")
def comfy_start(name: str) -> None:
    """Inicia uma instância em segundo plano (sem supervisão)."""
    from .comfy_supervisor import INSTANCES, port_open, start

    inst = next((i for i in INSTANCES if i.name == name), None)
    if inst is None:
        _fail(f"instância desconhecida: {name} ({[i.name for i in INSTANCES]})")
    if port_open(inst.port):
        typer.echo(f"{name} já está rodando em :{inst.port}")
        return
    start(inst, detached=True)
    typer.echo(f"{name} iniciando em :{inst.port} (log em .logs/comfy-{name}.log)")


@comfy_app.command("status")
def comfy_status(as_json: bool = JsonOpt) -> None:
    from .comfy import ComfyClient

    s = get_settings()
    c = ComfyClient(s.comfy_url)
    data: dict[str, Any] = {"url": s.comfy_url, "online": c.ping()}
    if data["online"]:
        stats = c.system_stats()
        data["system"] = stats.get("system", {})
        data["devices"] = stats.get("devices", [])
    _out(data, as_json, f"{s.comfy_url}: {'online' if data['online'] else 'OFFLINE'}")


@comfy_app.command("models")
def comfy_models(folder: str = typer.Argument("checkpoints"), as_json: bool = JsonOpt) -> None:
    from .comfy import ComfyClient

    ms = ComfyClient(get_settings().comfy_url).models(folder)
    _out(ms, as_json, "\n".join(ms))


RELOAD_EXIT_CODE = 75  # o servidor sai com este código para pedir ao supervisor que o reinicie


@app.command("serve")
def serve(host: Optional[str] = None, port: Optional[int] = None,
          supervise: bool = typer.Option(True, "--supervise/--no-supervise",
                                         help="roda sob um supervisor que reinicia o servidor em `forge reload`")) -> None:
    """Sobe API + UI web (padrão http://127.0.0.1:8765). Uma instância única para todas as modalidades."""
    import os
    import subprocess

    from .server import serve as _serve

    if not supervise or os.environ.get("FORGE_SUPERVISED") == "1":
        _serve(host, port)
        return
    args = [sys.executable, "-m", "forge_core.cli", "serve", "--no-supervise"]
    if host:
        args += ["--host", host]
    if port:
        args += ["--port", str(port)]
    env = {**os.environ, "FORGE_SUPERVISED": "1"}
    while True:
        proc = subprocess.Popen(args, env=env)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait(10)
            return
        if code == RELOAD_EXIT_CODE:
            typer.secho("recarregando o servidor com o código atual…", fg="cyan", err=True)
            continue
        raise typer.Exit(code)


@app.command("reload")
def reload_cmd(force: bool = typer.Option(False, "--force", help="não espera os jobs em andamento (eles são interrompidos)"),
               build_web: bool = typer.Option(False, "--build-web", help="recompila a UI (npm run build) antes"),
               url: Optional[str] = typer.Option(None, help="URL do servidor (padrão FORGE_HOST/FORGE_PORT)"),
               timeout: float = typer.Option(900, help="segundos esperando jobs terminarem"),
               as_json: bool = JsonOpt) -> None:
    """Recarrega o servidor único com o código atual (depois de um commit). Espera jobs ativos terminarem."""
    import shutil
    import subprocess
    import time

    import httpx

    base = (url or get_settings().server_url).rstrip("/")
    if build_web:
        web = Path(__file__).resolve().parents[4] / "web"
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            _fail("npm não encontrado no PATH", as_json)
        r = subprocess.run([npm, "run", "build", "--prefix", str(web)], capture_output=True, text=True)
        if r.returncode != 0:
            _fail(f"build da UI falhou:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}", as_json)
    try:
        before = httpx.get(f"{base}/api/health", timeout=5).json().get("boot_id")
        r = httpx.post(f"{base}/api/admin/reload", json={"force": force, "timeout": timeout}, timeout=timeout + 30)
    except httpx.HTTPError as e:
        _fail(f"servidor não respondeu em {base}: {e}", as_json)
    if r.status_code != 200:
        _fail(f"reload recusado ({r.status_code}): {r.text[:500]}", as_json)
    for _ in range(120):  # espera o servidor voltar com outro boot_id
        time.sleep(1)
        try:
            h = httpx.get(f"{base}/api/health", timeout=3).json()
            if h.get("boot_id") and h["boot_id"] != before:
                _out({"reloaded": True, "boot_id": h["boot_id"], "pipelines": h.get("pipelines")}, as_json,
                     f"servidor recarregado ({h.get('pipelines')} pipelines)")
                return
        except httpx.HTTPError:
            continue
    _fail("o servidor não voltou em 2 min (rodando sem supervisor? reinicie manualmente)", as_json)


@app.command("up")
def up(host: Optional[str] = None, port: Optional[int] = None,
       comfy: bool = typer.Option(True, "--comfy/--no-comfy", help="também sobe/monitora os ComfyUI")) -> None:
    """Um comando para tudo: ComfyUI (desktop/3d/trellis, supervisionados) + servidor Asset Forge supervisionado."""
    sup = None
    if comfy:
        from .comfy_supervisor import ComfySupervisor

        sup = ComfySupervisor(log=lambda m: typer.echo(m, err=True))
        sup.run_in_thread()
    try:
        serve(host=host, port=port, supervise=True)
    finally:
        if sup is not None:
            sup.stop_event.set()
            time.sleep(1)


@app.command("mcp")
def mcp() -> None:
    """Servidor MCP via stdio (para Claude Code e outros agentes)."""
    from .mcp_server import main

    main()


if __name__ == "__main__":
    app()


# ---------------------------------------------------------------- creator souls
soul_app = typer.Typer(help="Creator Souls: pacotes de referência (RAG + estilo derivado) anexáveis a projetos",
                       no_args_is_help=True)
app.add_typer(soul_app, name="soul")


def _soul(sid: str):
    from .souls import get_library

    try:
        return get_library().get(sid)
    except KeyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)


def _bar(v: float, msg: str) -> None:
    typer.echo(f"\r[{'#' * int(v * 30):<30}] {v * 100:5.1f}% {msg[:60]:<60}", nl=False)


@soul_app.command("create")
def soul_create(name: str, source: Path = typer.Option(..., "--from", help="pasta com as imagens de referência"),
                catalog: Optional[Path] = typer.Option(None, help="CATALOG.md/CSV com nomes (detectado sozinho)"),
                include: list[str] = typer.Option([], help='padrões glob, ex.: --include "Item_*"'),
                exclude: list[str] = typer.Option([]), description: str = "", license: str = "",
                no_describe: bool = typer.Option(False, help="não usar o modelo de visão para descrever o estilo"),
                as_json: bool = JsonOpt) -> None:
    """Cria uma Soul a partir de uma pasta (miniaturas, índice de busca, paleta, estatísticas e estilo)."""
    from .souls import get_library
    from .souls.ingest import build

    s = get_library().create(name, description=description, license=license)
    build(s, source, include=include or None, exclude=exclude or None, catalog=catalog,
          describe=not no_describe, on_progress=None if as_json else _bar)
    if not as_json:
        typer.echo()
    _out(s.summary(), as_json, f"soul '{s.id}': {s.manifest.count} referências\nestilo: {s.manifest.style.prompt}")


@soul_app.command("list")
def soul_list(as_json: bool = JsonOpt) -> None:
    from .souls import get_library

    souls = get_library().list()
    _out([s.summary() for s in souls], as_json,
         "\n".join(f"{s.id:24} {s.manifest.status:8} {s.manifest.count:6} refs  {s.manifest.name}" for s in souls))


@soul_app.command("show")
def soul_show(sid: str, as_json: bool = JsonOpt) -> None:
    s = _soul(sid)
    m = s.manifest
    _out(s.summary(), as_json, f"{m.name} ({m.status}, {m.count} refs)\ncategorias: {m.categories}\n"
                               f"pixel: {m.pixel.model_dump(exclude={'sizes'})}\nestilo: {m.style.prompt}")


@soul_app.command("search")
def soul_search(sid: str, query: str, k: int = 8, as_json: bool = JsonOpt) -> None:
    res = _soul(sid).retrieve(query, k)
    _out(res, as_json, "\n".join(f"{r['score']:6.2f}  {r['id']}  {r.get('name') or r['stem']}  [{r['category']}]" for r in res))


@soul_app.command("rebuild")
def soul_rebuild(sid: str) -> None:
    from .souls.ingest import build

    s = _soul(sid)
    src = s.manifest.source
    build(s, Path(src["folder"]), include=src.get("include") or None, exclude=src.get("exclude") or None,
          catalog=src.get("catalog"), on_progress=_bar)
    typer.echo(f"\nok: {s.manifest.count} referências")


@soul_app.command("caption")
def soul_caption(sid: str, limit: Optional[int] = None) -> None:
    """Legenda (modelo de visão local) as referências sem nome no catálogo."""
    from .souls.captions import caption_entries

    n = caption_entries(_soul(sid), limit=limit, on_progress=_bar)
    typer.echo(f"\n{n} legendas")


@soul_app.command("export")
def soul_export(sid: str, dest: Path = typer.Argument(Path("."))) -> None:
    from .souls.package import export_soul

    typer.echo(str(export_soul(_soul(sid), dest)))


@soul_app.command("import")
def soul_import(file: Path, name: Optional[str] = None) -> None:
    from .souls import get_library
    from .souls.package import import_soul

    s = import_soul(get_library(), file, name)
    typer.echo(f"importada como '{s.id}'")


@soul_app.command("delete")
def soul_delete(sid: str) -> None:
    from .project import ProjectRegistry
    from .souls import get_library

    _soul(sid)
    for p in ProjectRegistry(get_settings().projects_dir).list():
        if any(x.id == sid for x in p.config.souls):
            p.detach_soul(sid)
    typer.echo(f"movida para a lixeira: {get_library().delete(sid)}")


@soul_app.command("attach")
def soul_attach(sid: str, project: Optional[str] = ProjOpt, strength: float = 0.8) -> None:
    p = _project(project)
    try:
        p.attach_soul(sid, strength)
    except (KeyError, PermissionError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"'{sid}' anexada a {p.slug} (força {strength})")


@soul_app.command("detach")
def soul_detach(sid: str, project: Optional[str] = ProjOpt) -> None:
    p = _project(project)
    p.detach_soul(sid)
    typer.echo(f"'{sid}' removida de {p.slug}")


@soul_app.command("add-lora")
def soul_add_lora(sid: str, file: Path, name: str = typer.Option(..., help="ex.: item, projectile, npc"),
                  categories: list[str] = typer.Option(..., "--category", help="categorias da Soul cobertas (repetível)"),
                  template: str = typer.Option("{prompt}", help='formato do prompt de treino, ex.: "trrsprite, item, {prompt}"'),
                  base: str = typer.Option("sd_xl_base_1.0.safetensors", help="checkpoint base"),
                  cell_px: int = typer.Option(32, help="px por pixel de arte no canvas 1024 de treino"),
                  guide: bool = typer.Option(False, help="partir da silhueta da ref mais parecida (img2img)")) -> None:
    """Anexa uma LoRA treinada à Soul (copiada para a Soul e exposta ao ComfyUI)."""
    from .souls.loras import add_lora

    e = add_lora(_soul(sid), file, name=name, categories=categories, template=template, base=base,
                 cell_px=cell_px, guide=guide)
    typer.echo(f"LoRA '{name}' ({e['installed']}) cobre {categories}")


@soul_app.command("import-captions")
def soul_import_captions(sid: str, file: Path, category: list[str] = typer.Option([], help="restringe a categorias"),
                         overwrite: bool = False) -> None:
    """Mescla legendas {nome: descrição} (JSON) no índice da Soul."""
    import json

    from .souls.loras import import_captions

    n = import_captions(_soul(sid), json.loads(file.read_text(encoding="utf-8")), category or None, overwrite)
    typer.echo(f"{n} entradas legendadas")

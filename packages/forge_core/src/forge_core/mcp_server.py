"""Servidor MCP (stdio). Registre no Claude Code com:

    claude mcp add asset-forge -- uv run --directory "<raiz do monorepo>" forge mcp

Os resultados de geração incluem uma miniatura da imagem para o agente ver o que foi criado.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from .config import get_settings
from .pipeline import get_registry
from .project import ProjectRegistry, Style


def _run(project, pipeline: str, params: dict[str, Any], *, asset: str | None = None, name: str | None = None,
         tags: list[str] | None = None, on_log=None, folder: str | None = None, category: str | None = None):
    """Roda o pipeline num subprocesso, como o servidor: cada job carrega o código atual (o processo do MCP vive o
    tempo da sessão do agente e ficaria com o código de quando abriu) e um crash nativo não derruba o MCP."""
    from .worker import WorkerRequest, describe_exit, run_isolated

    res = run_isolated(WorkerRequest(str(project.root), pipeline, params, asset, name, tags or [], None, folder, category),
                       on_log=on_log)
    if res.ref is None:
        if res.reserved:  # worker morreu no meio: recolhe a versão incompleta
            project.store.sweep_incomplete(only=res.reserved)
        raise RuntimeError(res.error or f"falha no worker ({describe_exit(res.exitcode)})")
    return project.store.get(res.ref)


mcp = MCPServer(
    "asset-forge",
    instructions=(
        "Asset Forge gera e edita assets 2D/3D, áudio (SFX, música, sons retrô), VFX (shaders, partículas) e vídeo (Wan 2.2: gerar, animar, estender, regenerar trechos, editar) para jogos localmente (ComfyUI). "
        "Fluxo: list_projects/create_project -> get_project (leia style.system_prompt e siga-o ao escrever prompts) "
        "-> list_pipelines -> run_pipeline. Assets são referenciados como 'slug' ou 'slug@v003'; "
        "use input_asset nos params para editar/derivar de um asset existente (edições gravam nova versão "
        "do mesmo asset; para uma variação separada passe asset='<slug-novo>'). Organize com pastas/categorias "
        "(list_folders, organize_assets). Para resultados exigentes use a geração avançada (list_refiners, "
        "refine_start, refine_review): gere, avalie as prévias e peça ajustes até ficar bom. O estilo do projeto "
        "é aplicado automaticamente pelos pipelines; descreva só o conteúdo no prompt. "
        "VFX: você pode escrever o shader você mesmo (vfx.shader com code = GLSL 'vec4 effect(vec2 uv)' + uniform_defs; "
        "o servidor compila e devolve o erro por linha) ou o sistema de partículas (vfx.particles com definition = JSON). "
        "Áudio: audio.sfx/audio.music precisam dos modelos baixados; audio.retro (sfxr) e audio.edit rodam em CPU. "
        "Projetos marcados como +18 (nsfw) não enviam prévias ao agente e só o usuário pode alterar esse modo. "
        "Creator Souls (list_souls, soul_search, attach_soul) trazem estilo e referências de uma coleção: com uma Soul "
        "anexada, prefira 2d.soul para gerar no estilo dela. "
        "Pixel art exata (canvas/pivô/paleta travados): pixel_config → escreva um pixel map → run_pipeline pixel.map "
        "(ou pixel.fit para encaixar uma imagem) → leia extra.validation / pixel_validate até passar."
    ),
)


def _projects() -> ProjectRegistry:
    s = get_settings()
    s.projects_dir.mkdir(parents=True, exist_ok=True)
    return ProjectRegistry(s.projects_dir)


def _asset_view(project, av) -> dict[str, Any]:
    d = av.model_dump(mode="json", exclude={"params"})
    d["ref"] = av.ref
    vdir = project.store.version_dir(av.slug, av.version)
    d["files"] = [{"path": str(vdir / f["path"]), "role": f["role"], "mime": f["mime"]} for f in d["files"]]
    return d


def _thumb(path: Path, max_side: int = 512) -> Image | None:
    try:
        from PIL import Image as PILImage

        im = PILImage.open(path)
        im.load()
        w, h = im.size
        if max(w, h) < 256:  # pixel art pequena: amplia sem suavizar para ficar legível
            k = max(1, 256 // max(w, h))
            im = im.resize((w * k, h * k), PILImage.NEAREST)
        else:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return Image(data=buf.getvalue(), format="png")
    except Exception:
        return None


def _with_preview(project, av, include_image: bool = True) -> list[Any]:
    from .content import is_nsfw

    out: list[Any] = [json.dumps(_asset_view(project, av), ensure_ascii=False, indent=2)]
    if is_nsfw(project):  # projeto +18: o agente não recebe imagens
        include_image = False
        out.append("(projeto +18: prévias não são enviadas ao agente; veja na UI)")
    if include_image:
        pv = av.preview()
        if pv:
            img = _thumb(project.store.file_path(av, pv))
            if img:
                out.append(img)
    return out


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """Lista projetos (slug, nome, estilo)."""
    return [p.summary() for p in _projects().list()]


@mcp.tool()
def create_project(slug: str, name: str = "", description: str = "", style_prompt: str = "",
                   negative_prompt: str = "", system_prompt: str = "", palette: list[str] | None = None,
                   modality_settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cria um projeto. style_prompt é anexado a toda geração; system_prompt orienta agentes ao escrever prompts;
    palette (hex) força as cores no pixelizer; modality_settings ex.: {"2d": {"size": 64, "pixel_art": true}}."""
    style = Style(prompt=style_prompt, negative=negative_prompt, system_prompt=system_prompt, palette=palette or [])
    p = _projects().create(slug, name=name or slug, description=description, style=style, **(modality_settings or {}))
    return p.summary()


@mcp.tool()
def get_project(project: str) -> dict[str, Any]:
    """Configuração completa do projeto (estilo global e blocos por modalidade)."""
    return _projects().resolve(project).summary()


@mcp.tool()
def update_project(project: str, values: dict[str, Any]) -> dict[str, Any]:
    """Altera o project.yaml por caminho pontuado, ex.: {"style.prompt": "...", "2d.size": 32}."""
    p = _projects().resolve(project)
    for k, v in values.items():
        p.set_value(k, v)
    return p.summary()


@mcp.tool()
def add_reference(project: str, file_path: str) -> dict[str, Any]:
    """Copia uma imagem para refs/ do projeto e a adiciona às referências de estilo."""
    p = _projects().resolve(project)
    return {"ref": p.add_ref(Path(file_path)), "refs": p.config.style.refs}


@mcp.tool()
def list_pipelines(modality: str | None = None) -> list[dict[str, Any]]:
    """Pipelines disponíveis com o JSON schema dos parâmetros."""
    reg = get_registry()
    return [p.describe() for p in reg.pipelines.values() if not modality or p.modality == modality]


@mcp.tool(name="run_pipeline", structured_output=False)
async def run_pipeline_tool(project: str, pipeline: str, params: dict[str, Any] | None = None,
                            asset: str | None = None, name: str | None = None,
                            tags: list[str] | None = None, include_image: bool = True,
                            folder: str | None = None, category: str | None = None) -> list[Any]:
    """Executa um pipeline (ex.: "2d.text2d") e devolve a nova versão (arquivos + miniatura).
    params segue o schema de list_pipelines; params.input_asset = "slug" ou "slug@v003" usa um asset como entrada.

    ONDE O RESULTADO É GRAVADO:
    - Pipelines com same_asset=true (2d.edit, 2d.enhance, 2d.remove_bg, 2d.pixelize, 2d.anim_edit...) gravam por
      padrão uma NOVA VERSÃO do asset de entrada — certo para correções/ajustes do mesmo asset.
    - Para uma variação DERIVADA (outra pose, close-up, outro objeto a partir deste), passe `asset` com um slug
      novo (ex.: asset="saint-germain-pose-sentado"): cria um asset separado, com parent = entrada e mesma
      pasta/categoria da entrada.
    - Pipelines com same_asset=false (text2d, image2d, animate, view...) sempre criam asset novo; `asset` só
      escolhe o nome (se o slug já existir, vira nova versão dele).
    folder/category organizam o asset (padrão: herda da entrada)."""
    from .jobstore import record_direct_run

    p = _projects().resolve(project)
    av = await asyncio.to_thread(
        record_direct_run, "mcp", p.slug, pipeline, params or {}, asset, name, tags,
        lambda logs: _run(p, pipeline, params or {}, asset=asset, name=name, tags=tags, on_log=logs.append,
                          folder=folder, category=category))
    return _with_preview(p, av, include_image)


@mcp.tool()
def list_jobs(project: str | None = None, failed_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    """Histórico de jobs (UI, CLI e MCP) com parâmetros e erro — use para repetir algo que falhou."""
    from .jobstore import default_store

    rows = default_store().list(project=project, status="error,cancelled" if failed_only else None, limit=limit)
    for r in rows:
        r["log_tail"] = r.pop("logs")[-10:]
    return rows


@mcp.tool(structured_output=False)
async def retry_job(job_id: str, params: dict[str, Any] | None = None, new_seed: bool = False,
                    include_image: bool = True) -> list[Any]:
    """Repete um job do histórico com os mesmos parâmetros; `params` sobrescreve campos; new_seed=True gera variação."""
    from .jobstore import default_store, record_direct_run

    old = default_store().get(job_id)
    if not old:
        raise ValueError(f"job não encontrado: {job_id}")
    req = old["request"]
    merged = {**req.get("params", {}), **(params or {})}
    if new_seed:
        merged.pop("seed", None)
    p = _projects().resolve(req["project"])
    av = await asyncio.to_thread(
        record_direct_run, "mcp", p.slug, req["pipeline"], merged, req.get("asset"), req.get("name"), req.get("tags"),
        lambda logs: _run(p, req["pipeline"], merged, asset=req.get("asset"), name=req.get("name"),
                          tags=req.get("tags"), on_log=logs.append),
        job_id)
    return _with_preview(p, av, include_image)


@mcp.tool()
def list_assets(project: str, query: str = "", modality: str | None = None, kind: str | None = None,
                tag: str | None = None, base: str | None = None, folder: str | None = None,
                recursive: bool = True, category: str | None = None) -> list[dict[str, Any]]:
    """Lista a última versão de cada asset (busca em nome/prompt/tags). base="knight" lista os
    derivados de outro asset, ex.: as animações de um personagem (kind="animation")."""
    p = _projects().resolve(project)
    rows = p.store.search(q=query, modality=modality, kind=kind, tag=tag, base=base, folder=folder,
                          recursive=recursive, category=category)
    for r in rows:
        vdir = p.store.version_dir(r["slug"], r["version"])
        r["primary_path"] = str(vdir / r["primary_file"]) if r.get("primary_file") else None
    return rows


@mcp.tool(structured_output=False)
def get_asset(project: str, ref: str, include_image: bool = True, all_versions: bool = False) -> list[Any]:
    """Metadados e arquivos de um asset (ref = slug ou slug@v003), com miniatura."""
    p = _projects().resolve(project)
    if all_versions:
        return [json.dumps([_asset_view(p, v) for v in p.store.versions(ref.split("@")[0])], ensure_ascii=False, indent=2)]
    return _with_preview(p, p.store.get(ref), include_image)


@mcp.tool()
def export_asset(project: str, ref: str, exporter: str | None = None, params: dict[str, Any] | None = None,
                 out_dir: str | None = None) -> dict[str, Any]:
    """Exporta um asset sem criar versão (ex.: "2d.gif", "2d.sheet" com JSON Aseprite, "2d.frames").
    Sem `exporter`, lista os exportadores aplicáveis com seus parâmetros. Devolve os caminhos gerados."""
    from .export import run_export

    p = _projects().resolve(project)
    if not exporter:
        return {"exporters": [e.describe() for e in get_registry().exporters_for(p.store.get(ref))]}
    files = run_export(p, ref, exporter, params or {}, out_dir)
    return {"files": [str(f) for f in files]}


@mcp.tool()
def import_file(project: str, file_path: str, asset: str | None = None, modality: str | None = None,
                kind: str | None = None, tags: list[str] | None = None, folder: str | None = None,
                category: str | None = None, name: str | None = None,
                options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Importa um arquivo local como asset (novo, ou nova versão de `asset`). modality/kind são
    inferidos pela extensão se omitidos (malhas -> 3d/mesh, imagens -> 2d/image). Formatos com importer são
    convertidos: .aseprite/.ase, GIF/APNG/WebP animado, folha de sprites em grade (`options`: columns, rows,
    cell_width, cell_height, margin, spacing, fps...), áudio (waveform), vídeo (poster), partículas JSON...
    `list_import_formats` mostra as opções de cada um.
    Passe `asset` (slug, ex.: "saint-germain-concept") — recomendado. Sem ele o slug vem do nome do arquivo, ou da
    pasta de origem quando o nome é genérico (image.png, ComfyUI_0001_.png). `name` é o nome de exibição."""
    p = _projects().resolve(project)
    av = p.store.import_file(Path(file_path), slug=asset, modality=modality, kind=kind, tags=tags,
                             folder=folder, category=category, name=name or "", options=options)
    return _asset_view(p, av)


@mcp.tool()
def list_import_formats() -> list[dict[str, Any]]:
    """Formatos importáveis por `import_file`: id do importer, extensões, modalidade e schema das opções."""
    return [i.describe() for i in get_registry().importers.values()]


@mcp.tool()
def tag_asset(project: str, ref: str, tags: list[str], name: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Adiciona tags (e opcionalmente nome/notas) a uma versão."""
    p = _projects().resolve(project)
    av = p.store.get(ref)
    av = p.store.update_meta(av.ref, tags=sorted(set(av.tags) | set(tags)), name=name, notes=notes)
    return _asset_view(p, av)


@mcp.tool()
def list_folders(project: str) -> dict[str, Any]:
    """Pastas do projeto (com nº de assets) e categorias disponíveis (id, nome, cor)."""
    p = _projects().resolve(project)
    counts = p.store.folder_counts()
    paths = sorted(set(p.config.folders) | {f for f in counts if f}, key=str.lower)
    return {"root_count": counts.get("", 0), "folders": [{"path": f, "count": counts.get(f, 0)} for f in paths],
            "categories": [c.model_dump() for c in p.config.categories]}


@mcp.tool()
def organize_assets(project: str, slugs: list[str], folder: str | None = None, category: str | None = None,
                    clear_category: bool = False) -> list[dict[str, Any]]:
    """Move assets para uma pasta (ex.: "Personagens/Cavaleiro"; "" = raiz) e/ou define a categoria
    (id de list_folders.categories). Use para agrupar tudo de um mesmo personagem/objeto."""
    from .project import normalize_folder

    p = _projects().resolve(project)
    out = [{"slug": s, **p.store.set_meta(s.split("@")[0], folder=folder, category=category,
                                          clear_category=clear_category).model_dump()} for s in slugs]
    f = normalize_folder(folder) if folder else ""
    if f and f not in p.config.folders:
        p.config.folders.append(f)
        p.save()
    return out


# ---- geração avançada (laço gerar -> avaliar -> ajustar) ------------------

def _refine_out(project, s, include_images: bool = True) -> list[Any]:
    from .refine import session_dir, session_view

    from .content import is_nsfw

    v = session_view(project, s, abs_paths=True)
    out: list[Any] = [json.dumps(v, ensure_ascii=False, indent=2, default=str)]
    if is_nsfw(project):
        include_images = False
    if include_images and s.iterations:
        it = s.iterations[-1]
        for pv in it.previews[:6]:
            img = _thumb(session_dir(project, s.id) / pv.path, 768)
            if img:
                out += [f"prévia — {pv.label}", img]
    if s.status == "awaiting_review":
        out.append("PRÓXIMO PASSO: avalie as prévias contra o brief e chame refine_review (verdict accept, ou "
                   "revise/restart com next_pipeline + next_params do catálogo em list_refiners).")
    return out


@mcp.tool()
def list_refiners() -> dict[str, Any]:
    """Refinadores da geração avançada (ex.: "2d.asset", "3d.model") com o catálogo de ações de ajuste
    e o schema de cada uma. Também diz se o crítico automático (Claude API) está disponível."""
    from .critic import api_available

    ok, why = api_available()
    return {"refiners": [r.describe() for r in get_registry().refiners.values()],
            "api_critic": {"available": ok, "detail": why}}


@mcp.tool(structured_output=False)
async def refine_start(project: str, refiner: str, prompt: str, references: list[str] | None = None,
                       criteria: list[str] | None = None, max_iterations: int = 6, target_score: float = 8.0,
                       critic: str = "agent", title: str = "", asset: str | None = None,
                       folder: str | None = None, category: str | None = None,
                       options: dict[str, Any] | None = None) -> list[Any]:
    """Inicia uma geração avançada: gera, avalia e refina até ficar satisfatório.
    critic="agent" (padrão): VOCÊ é o crítico — esta chamada roda a 1ª iteração e devolve as prévias; avalie e
    chame refine_review até aceitar. critic="api": o Claude via API avalia sozinho em segundo plano (precisa de
    credencial); acompanhe com refine_status. references: imagens (slug@vN ou caminhos). Cada iteração vira uma
    versão do mesmo asset."""
    import threading

    from .refine import Brief, RefineEngine

    p = _projects().resolve(project)
    eng = RefineEngine(p)
    brief = Brief(prompt=prompt, references=references or [], criteria=criteria or [],
                  max_iterations=max_iterations, target_score=target_score, params=options or {})
    s = eng.create(refiner, brief, critic="api" if critic == "api" else "manual", title=title, asset=asset,
                   folder=folder, category=category)
    if critic == "api":
        threading.Thread(target=eng.run_until_pause, args=(s.id,), daemon=True).start()
        return [json.dumps({"id": s.id, "asset": s.asset, "status": "running",
                            "next": "use refine_status(project, id) para acompanhar"}, ensure_ascii=False)]
    s = await asyncio.to_thread(eng.step, s.id)
    return _refine_out(p, s)


@mcp.tool(structured_output=False)
async def refine_review(project: str, session_id: str, score: float, verdict: str, summary: str = "",
                        issues: list[str] | None = None, next_pipeline: str | None = None,
                        next_params: dict[str, Any] | None = None, note: str = "") -> list[Any]:
    """Avalia a última iteração e (se não aceitar) já executa a próxima. verdict: "accept" encerra;
    "revise" ajusta a partir do resultado ("$current" como input_asset); "restart" gera de novo (use "$reference").
    next_pipeline/next_params: uma ação do catálogo do refinador; placeholders "$current", "$best",
    "$reference", "$references". Devolve as novas prévias."""
    from .refine import Action, RefineEngine, Review

    p = _projects().resolve(project)
    eng = RefineEngine(p)
    action = Action(pipeline=next_pipeline, params=next_params or {}, note=note) if next_pipeline else None
    s = eng.review(session_id, Review(score=score, verdict=verdict, summary=summary, issues=issues or [],
                                      next_action=action, reviewer="agent"))
    if s.status == "ready":
        s = await asyncio.to_thread(eng.step, session_id)
    return _refine_out(p, s)


@mcp.tool(structured_output=False)
def refine_status(project: str, session_id: str, include_images: bool = True) -> list[Any]:
    """Estado de uma sessão de geração avançada (iterações, notas, problemas, prévias da última iteração)."""
    from .refine import load_session

    p = _projects().resolve(project)
    return _refine_out(p, load_session(p, session_id), include_images)


@mcp.tool()
def refine_list(project: str) -> list[dict[str, Any]]:
    """Sessões de geração avançada do projeto (mais recentes primeiro)."""
    from .refine import list_sessions

    p = _projects().resolve(project)
    return [{"id": s.id, "title": s.title, "refiner": s.refiner, "status": s.status, "critic": s.critic,
             "iterations": len(s.iterations), "best": s.best, "final": s.final, "message": s.message}
            for s in list_sessions(p)]


@mcp.tool()
def refine_cancel(project: str, session_id: str) -> dict[str, Any]:
    """Cancela uma sessão (mantém as versões já geradas)."""
    from .refine import RefineEngine

    s = RefineEngine(_projects().resolve(project)).cancel(session_id)
    return {"id": s.id, "status": s.status, "final": s.final}


# ---------------------------------------------------------------- creator souls

@mcp.tool()
def list_souls() -> list[dict[str, Any]]:
    """Creator Souls da biblioteca (pacotes de referência com estilo derivado) e em quais projetos estão anexadas."""
    from .souls import get_library

    attached: dict[str, list[str]] = {}
    for p in _projects().list():
        for ref in p.config.souls:
            attached.setdefault(ref.id, []).append(p.slug)
    return [{"id": s.id, "name": s.manifest.name, "status": s.manifest.status, "count": s.manifest.count,
             "categories": s.manifest.categories, "style": s.manifest.style.prompt, "nsfw": s.manifest.nsfw,
             "projects": attached.get(s.id, [])} for s in get_library().list()]


@mcp.tool(structured_output=False)
def soul_search(query: str, soul: str | None = None, project: str | None = None, k: int = 4,
                include_images: bool = True) -> list[Any]:
    """Busca referências numa Soul (ou nas Souls anexadas ao projeto). Cada resultado tem `ref` (soul:<id>/<ref>),
    que pode ir em reference_assets / input_asset de qualquer pipeline (ex.: 2d.image2d, 2d.edit, 2d.soul)."""
    from .souls import get_library, soul_context

    if soul:
        pairs = [(get_library().get(soul), 1.0)]
    elif project:
        pairs = soul_context(_projects().resolve(project))
    else:
        raise ValueError("informe soul ou project")
    out: list[Any] = []
    for s, _ in pairs:
        res = s.retrieve(query, k)
        rows = [{"ref": f"soul:{s.id}/{r['id']}", "name": r.get("name") or r["stem"], "category": r["category"],
                 "size": [r.get("fw"), r.get("fh")], "score": r["score"]} for r in res]
        out.append(json.dumps({"soul": s.id, "results": rows}, ensure_ascii=False, indent=2))
        if include_images and not s.manifest.nsfw:
            for r in res:
                img = _thumb(s.ref_path(r["id"]), 256)
                if img:
                    out += [f"{r.get('name') or r['stem']} ({r['category']})", img]
    return out


@mcp.tool()
def attach_soul(project: str, soul: str, strength: float = 0.8) -> dict[str, Any]:
    """Anexa uma Creator Soul ao projeto (estilo + paleta + referências automáticas). strength 0..1."""
    p = _projects().resolve(project)
    p.attach_soul(soul, strength)
    return {"project": p.slug, "souls": [s.model_dump() for s in p.config.souls]}


# ---------------------------------------------------------------- pixel studio

@mcp.tool()
def pixel_config(project: str) -> dict[str, Any]:
    """Paleta travada (core + grupos) e specs de asset (canvas, pivô a partir do canto INFERIOR esquerdo, área
    segura, contorno, limites) do projeto. Use antes de escrever um pixel map para pixel.map."""
    from forge_pixel import config

    p = _projects().resolve(project)
    pal = config.load_palette(p)
    return {"palette": {"core": pal.core, "groups": pal.groups},
            "specs": {k: s.model_dump(mode="json") for k, s in config.load_specs(p).items()},
            "pixel_map_format": {"format": "asset-forge-pixelmap/1", "width": "W", "height": "H",
                                 "legend": {"k": "#0b0a12"}, "rows": "H strings de W chars, de cima para baixo; '.' = transparente",
                                 "outline": {"auto": True, "color": "#0b0a12"}, "side": "null|L|R", "shade_L": 0.78}}


@mcp.tool()
def pixel_validate(project: str, asset: str, spec: str | None = None, references: list[str] | None = None,
                   material: str | None = None, tags: list[str] | None = None, autotile: bool = False) -> str:
    """Roda os validadores do Pixel Studio num asset (canvas, alfa binário, paleta, contorno, ilhas, área segura,
    âncora, tileável, cores, cobertura, nome, par L/R, cor do material, regras por tag) e devolve o relatório em
    markdown. autotile=True valida um conjunto do pixel.autotile (16 máscaras: emendas e mapa de teste)."""
    from forge_pixel.api import validate_asset, validate_autotile_asset

    p = _projects().resolve(project)
    if autotile:
        return validate_autotile_asset(p, asset).to_markdown()
    rep = validate_asset(p, asset, spec, references, material=material, tags=tags)
    return rep.to_markdown()


def main() -> None:
    mcp.run()

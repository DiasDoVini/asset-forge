"""Geração avançada: laço gerar → avaliar (Claude) → ajustar, até o resultado ficar bom.

Conceitos
- Brief: o que se quer (prompt, imagens de referência, critérios, limite de iterações, nota-alvo).
- Refiner (plugin por modalidade, registrado em Modality.refiners): ação inicial, catálogo de ações de
  ajuste permitidas e as prévias (imagens) que o crítico vê — no 3D, renders de vários ângulos.
- Iteração: ação (pipeline + params) → resultado (nova versão do asset da sessão) → prévias → revisão.
- Crítico: "api" (Claude via API, automático — forge_core.critic) ou "manual" (a sessão pausa em
  awaiting_review e um agente via MCP, ou uma pessoa na UI, envia a revisão).

Placeholders nos params das ações (resolvidos pelo motor):
    "$current"    resultado da última iteração bem-sucedida (ex.: input_asset de uma edição)
    "$best"       resultado com maior nota até agora
    "$reference"  primeira referência do brief;  "$references" lista de todas

Armazenamento: <projeto>/.forge/refine/<id>/session.json e iter_NN/ com as prévias.

Contrato para modalidades:

    class MeshRefiner(Refiner):
        id = "3d.model"
        title = "Modelo 3D"
        def initial_action(self, brief): return Action(pipeline="3d.image_to_3d", params={"input_asset": "$reference"})
        def actions(self): return [ActionSpec("3d.ai_edit", "mudar forma/detalhe via edição da imagem"), ...]
        def previews(self, ctx): ... renderiza ctx.asset em ctx.out_dir -> [Preview(path, "frente"), ...]
    Modality(name="3d", refiners=[MeshRefiner()])
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .assets import AssetVersion
    from .project import Project

Status = Literal["ready", "running", "awaiting_review", "done", "failed", "cancelled"]
Verdict = Literal["accept", "revise", "restart"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- modelos

class Brief(BaseModel):
    prompt: str = Field(..., description="o que se quer obter")
    references: list[str] = Field(default_factory=list, description="imagens de referência (slug@vN ou caminhos)")
    criteria: list[str] = Field(default_factory=list, description="critérios de aceitação extras")
    max_iterations: int = Field(6, ge=1, le=30)
    target_score: float = Field(8.0, ge=1, le=10, description="nota (0-10) que encerra como aceito")
    params: dict[str, Any] = Field(default_factory=dict, description="opções do refinador (ex.: polycount alvo)")


class Action(BaseModel):
    pipeline: str
    params: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class Preview(BaseModel):
    path: str           # relativo à pasta da sessão
    label: str = ""


class Review(BaseModel):
    score: float = Field(..., ge=0, le=10)
    verdict: Verdict
    summary: str = ""
    issues: list[str] = Field(default_factory=list)
    next_action: Action | None = None
    reviewer: str = "manual"
    created: datetime = Field(default_factory=_now)


class Iteration(BaseModel):
    n: int
    action: Action
    resolved_params: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = None
    result: str | None = None     # ref da versão produzida
    error: str | None = None
    previews: list[Preview] = Field(default_factory=list)
    review: Review | None = None
    started: datetime = Field(default_factory=_now)
    finished: datetime | None = None


class RefineSession(BaseModel):
    id: str
    project: str
    refiner: str
    title: str = ""
    brief: Brief
    critic: Literal["api", "manual"] = "manual"
    asset: str                    # slug onde cada iteração vira uma versão
    status: Status = "ready"
    pending_action: Action | None = None
    iterations: list[Iteration] = Field(default_factory=list)
    best: str | None = None
    final: str | None = None
    error: str | None = None
    message: str | None = None    # progresso/nota humana
    driver: Literal["server", "local"] = "local"
    folder: str | None = None     # organização do asset da sessão (aplicada na 1ª iteração)
    category: str | None = None
    created: datetime = Field(default_factory=_now)
    updated: datetime = Field(default_factory=_now)

    def last_result(self) -> str | None:
        for it in reversed(self.iterations):
            if it.result:
                return it.result
        return None

    def best_iteration(self) -> Iteration | None:
        scored = [it for it in self.iterations if it.result and it.review]
        return max(scored, key=lambda it: (it.review.score, it.n)) if scored else None


# ---------------------------------------------------------------- contrato do refinador

@dataclass
class ActionSpec:
    pipeline: str
    when: str                       # quando usar (texto para o crítico)
    hints: str = ""                 # dicas de parâmetros


@dataclass
class PreviewContext:
    project: "Project"
    session: RefineSession
    asset: "AssetVersion"
    out_dir: Path


class Refiner:
    id: ClassVar[str]               # "3d.model", "2d.asset"
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    default_criteria: ClassVar[list[str]] = []

    @property
    def modality(self) -> str:
        return self.id.split(".", 1)[0]

    def initial_action(self, brief: Brief) -> Action:
        raise NotImplementedError

    def actions(self) -> list[ActionSpec]:
        raise NotImplementedError

    def previews(self, ctx: PreviewContext) -> list[Preview]:
        """Imagens que o crítico vê (grave em ctx.out_dir; paths relativos à pasta da sessão)."""
        raise NotImplementedError

    def critic_guidance(self) -> str:
        """Notas de domínio para o crítico (o que olhar, armadilhas comuns)."""
        return ""

    def describe(self) -> dict[str, Any]:
        from .pipeline import get_registry

        reg = get_registry()
        acts = []
        for a in self.actions():
            p = reg.pipelines.get(a.pipeline)
            acts.append({"pipeline": a.pipeline, "when": a.when, "hints": a.hints,
                         "title": p.title if p else a.pipeline, "available": p is not None,
                         "params_schema": p.Params.model_json_schema() if p else None})
        return {"id": self.id, "title": self.title or self.id, "description": self.description,
                "modality": self.modality, "default_criteria": self.default_criteria, "actions": acts}


# ---------------------------------------------------------------- persistência

def sessions_dir(project: "Project") -> Path:
    d = project.forge_dir / "refine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_dir(project: "Project", sid: str) -> Path:
    return sessions_dir(project) / sid


_locks: dict[str, threading.RLock] = {}


def _lock(sid: str) -> threading.RLock:
    return _locks.setdefault(sid, threading.RLock())


def save_session(project: "Project", s: RefineSession) -> RefineSession:
    s.updated = _now()
    d = session_dir(project, s.id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "session.json.tmp"
    tmp.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(d / "session.json")
    return s


def load_session(project: "Project", sid: str) -> RefineSession:
    f = session_dir(project, sid) / "session.json"
    if not f.exists():
        raise FileNotFoundError(f"sessão de refinamento não encontrada: {sid}")
    return RefineSession.model_validate_json(f.read_text(encoding="utf-8"))


def list_sessions(project: "Project") -> list[RefineSession]:
    out = []
    for d in sessions_dir(project).iterdir():
        f = d / "session.json"
        if f.exists():
            try:
                out.append(RefineSession.model_validate_json(f.read_text(encoding="utf-8")))
            except ValueError:
                continue
    return sorted(out, key=lambda s: s.created, reverse=True)


# ---------------------------------------------------------------- motor

RunAction = Callable[[RefineSession, Action, dict[str, Any]], tuple[str | None, str | None, str | None]]
"""(sessão, ação, params resolvidos) -> (ref do resultado, job_id, erro)."""


def get_refiner(refiner_id: str) -> Refiner:
    from .pipeline import get_registry

    reg = get_registry()
    try:
        return reg.refiners[refiner_id]
    except KeyError:
        raise KeyError(f"refinador desconhecido: {refiner_id}. Disponíveis: {sorted(reg.refiners)}") from None


def local_runner(project: "Project", source: str = "local") -> RunAction:
    """Executa ações no próprio processo (CLI/MCP), registrando no histórico de jobs."""
    from .jobstore import record_direct_run
    from .runner import run_pipeline

    def run(s: RefineSession, action: Action, params: dict[str, Any]):
        try:
            av = record_direct_run(
                source, project.slug, action.pipeline, params, s.asset, None, ["refine", s.id],
                lambda logs: run_pipeline(project, action.pipeline, params, asset=s.asset, tags=["refine", s.id],
                                          on_log=logs.append))
            return av.ref, None, None
        except Exception as e:  # noqa: BLE001 - erro vira parte da iteração (o crítico decide o que fazer)
            return None, None, f"{type(e).__name__}: {e}"

    return run


class RefineEngine:
    def __init__(self, project: "Project", run_action: RunAction | None = None,
                 critic: Callable[[RefineSession, Refiner], Review] | None = None):
        self.project = project
        self.run_action = run_action or local_runner(project)
        self._critic = critic

    # -- criação ----------------------------------------------------------
    def create(self, refiner_id: str, brief: Brief, critic: Literal["api", "manual"] = "manual",
               title: str = "", asset: str | None = None, driver: Literal["server", "local"] = "local",
               folder: str | None = None, category: str | None = None) -> RefineSession:
        from .project import slugify

        refiner = get_refiner(refiner_id)
        sid = uuid.uuid4().hex[:10]
        base = slugify(asset or title or " ".join(brief.prompt.split()[:4]) or "refine")
        slug = slugify(asset) if asset else self.project.store.unique_slug(base)
        s = RefineSession(id=sid, project=self.project.slug, refiner=refiner_id, title=title or brief.prompt[:80],
                          brief=brief, critic=critic, asset=slug, driver=driver, folder=folder, category=category,
                          pending_action=refiner.initial_action(brief))
        return save_session(self.project, s)

    # -- execução de uma iteração --------------------------------------
    def resolve(self, s: RefineSession, params: dict[str, Any]) -> dict[str, Any]:
        best = s.best_iteration()
        refs = s.brief.references
        subs = {"$current": s.last_result(), "$best": best.result if best else s.last_result(),
                "$reference": refs[0] if refs else None}

        def r(v: Any) -> Any:
            if isinstance(v, str):
                if v == "$references":
                    return list(refs)
                if v in subs:
                    if subs[v] is None:
                        raise ValueError(f"{v} indisponível nesta etapa")
                    return subs[v]
                return v
            if isinstance(v, list):
                out_list: list[Any] = []
                for y in v:
                    if y == "$references":
                        out_list.extend(refs)
                    else:
                        out_list.append(r(y))
                return out_list
            if isinstance(v, dict):
                return {k: r(x) for k, x in v.items()}
            return v

        return {k: r(v) for k, v in params.items()}

    def step(self, sid: str, on_progress: Callable[[str], None] | None = None) -> RefineSession:
        """Executa a ação pendente, gera prévias e (se crítico=api) avalia. Bloqueante."""
        with _lock(sid):
            s = load_session(self.project, sid)
            if s.status not in ("ready",) or s.pending_action is None:
                return s
            refiner = get_refiner(s.refiner)
            action = s.pending_action
            it = Iteration(n=len(s.iterations) + 1, action=action)
            s.status, s.pending_action, s.message = "running", None, f"iteração {it.n}: {action.pipeline}"
            try:
                it.resolved_params = self.resolve(s, action.params)
            except ValueError as e:
                it.error = str(e)
            s.iterations.append(it)
            save_session(self.project, s)

        if on_progress:
            on_progress(s.message or "")
        if it.error is None:
            ref, job_id, err = self.run_action(s, action, it.resolved_params)
            it.result, it.job_id, it.error = ref, job_id, err
        if it.result:
            if len(s.iterations) == 1:
                self._apply_organization(s)
            try:
                av = self.project.store.get(it.result)
                out = session_dir(self.project, s.id) / f"iter_{it.n:02d}"
                out.mkdir(parents=True, exist_ok=True)
                it.previews = refiner.previews(PreviewContext(self.project, s, av, out))
            except Exception as e:  # noqa: BLE001
                it.error = f"prévias falharam: {type(e).__name__}: {e}"
        it.finished = _now()

        with _lock(sid):
            s.status = "awaiting_review"
            s.message = f"iteração {it.n} pronta para avaliação"
            save_session(self.project, s)

        if s.critic == "api":
            if on_progress:
                on_progress(f"Claude avaliando a iteração {it.n}")
            try:
                review = self.critic(s, refiner)
            except Exception as e:  # noqa: BLE001 - sem crédito/credencial: vira revisão manual
                with _lock(sid):
                    s.message = f"crítico automático indisponível ({type(e).__name__}: {e}); aguardando revisão manual"
                    s.critic = "manual"
                    save_session(self.project, s)
                return s
            return self.review(sid, review)
        return s

    def critic(self, s: RefineSession, refiner: Refiner) -> Review:
        if self._critic:
            return self._critic(s, refiner)
        from .critic import review_iteration

        return review_iteration(self.project, s, refiner)

    # -- revisão -----------------------------------------------------------
    def review(self, sid: str, review: Review) -> RefineSession:
        """Registra a revisão da última iteração e decide: aceitar, parar no limite ou seguir."""
        with _lock(sid):
            s = load_session(self.project, sid)
            if s.status in ("done", "cancelled") or not s.iterations:
                return s
            it = s.iterations[-1]
            it.review = review
            best = s.best_iteration()
            s.best = best.result if best else None
            ok_score = review.score >= s.brief.target_score and it.result is not None
            if review.verdict == "accept" or ok_score:
                s.status, s.final = "done", it.result or s.best
                s.message = f"aceito na iteração {it.n} (nota {review.score:g})"
            elif len(s.iterations) >= s.brief.max_iterations:
                s.status, s.final = "done", s.best
                s.message = f"limite de {s.brief.max_iterations} iterações; melhor resultado: {s.best} "
            elif review.next_action is None:
                s.status = "awaiting_review"
                s.message = "revisão sem próxima ação; informe uma ação ou aceite"
            else:
                self._validate_action(s, review.next_action)
                s.pending_action, s.status = review.next_action, "ready"
                s.message = f"próxima: {review.next_action.pipeline} — {review.next_action.note}"[:300]
            save_session(self.project, s)
            return s

    def _validate_action(self, s: RefineSession, a: Action) -> None:
        from .pipeline import get_registry
        from .runner import validate_params

        refiner = get_refiner(s.refiner)
        allowed = {x.pipeline for x in refiner.actions()}
        if a.pipeline not in allowed:
            raise ValueError(f"ação {a.pipeline} não permitida neste refinador ({sorted(allowed)})")
        # valida com placeholders trocados por valores plausíveis
        probe = {k: ("x@v001" if isinstance(v, str) and v.startswith("$") else v) for k, v in a.params.items()}
        validate_params(get_registry().get(a.pipeline), probe)

    def cancel(self, sid: str) -> RefineSession:
        with _lock(sid):
            s = load_session(self.project, sid)
            if s.status not in ("done",):
                s.status, s.message = "cancelled", "cancelado"
                s.final = s.final or s.best
                save_session(self.project, s)
            return s

    # -- organização -------------------------------------------------------
    def _apply_organization(self, s: RefineSession) -> None:
        try:
            if s.folder is not None or s.category is not None:
                self.project.store.set_meta(s.asset, folder=s.folder, category=s.category)
        except FileNotFoundError:
            pass

    def run_until_pause(self, sid: str, on_progress: Callable[[str], None] | None = None,
                        should_stop: Callable[[], bool] | None = None) -> RefineSession:
        """Roda iterações enquanto houver ação pendente e o crítico for automático."""
        s = load_session(self.project, sid)
        while s.status == "ready" and not (should_stop and should_stop()):
            s = self.step(sid, on_progress)
            if s.critic != "api":
                break
        return s


def session_view(project: "Project", s: RefineSession, url_prefix: str | None = None,
                 abs_paths: bool = False) -> dict[str, Any]:
    """Dict para API/MCP com URLs ou caminhos absolutos das prévias."""
    d = s.model_dump(mode="json")
    base = session_dir(project, s.id)
    for it in d["iterations"]:
        for pv in it["previews"]:
            if url_prefix is not None:
                pv["url"] = f"{url_prefix}/.forge/refine/{s.id}/{pv['path']}"
            if abs_paths:
                pv["abs_path"] = str(base / pv["path"])
    return d


def copy_preview(src: Path, ctx: PreviewContext, name: str, label: str) -> Preview:
    dest = ctx.out_dir / name
    shutil.copy2(src, dest)
    return Preview(path=dest.relative_to(session_dir(ctx.project, ctx.session.id)).as_posix(), label=label)


def preview_from_image(img, ctx: PreviewContext, name: str, label: str) -> Preview:
    dest = ctx.out_dir / name
    img.save(dest)
    return Preview(path=dest.relative_to(session_dir(ctx.project, ctx.session.id)).as_posix(), label=label)


def summarize_history(s: RefineSession, limit: int = 8) -> str:
    lines = []
    for it in s.iterations[-limit:]:
        r = it.review
        lines.append(json.dumps({
            "iteracao": it.n, "acao": it.action.pipeline, "params": it.resolved_params or it.action.params,
            "nota_da_acao": it.action.note, "resultado": it.result, "erro": it.error,
            "nota": r.score if r else None, "veredito": r.verdict if r else None,
            "problemas": r.issues if r else None}, ensure_ascii=False, default=str))
    return "\n".join(lines)

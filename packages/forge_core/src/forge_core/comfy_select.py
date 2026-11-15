"""Escolha da instância do ComfyUI para um job: a configurada (FORGE_COMFY_URL, :8188) se ela servir, senão outra
instância do Asset Forge no ar que sirva.

"Servir" = responder, ter os nós pedidos e enxergar os arquivos pedidos. Sem lista de arquivos, basta enxergar algum
modelo (diffusion_models/checkpoints): isso descarta um ComfyUI recém-instalado com a pasta de modelos vazia (ex.: o
Comfy Desktop apontando para a pasta padrão dele), que antes recebia o job e falhava na validação.
"""
from __future__ import annotations

from typing import Any

from .comfy import ComfyError


def _sees_models(c: Any) -> bool:
    return bool(c.models("diffusion_models") or c.models("checkpoints"))


def use_instance(ctx: Any, *, nodes: tuple[str, ...] = (), files: dict[str, str] | None = None,
                 what: str = "este pipeline") -> None:
    """Troca `ctx.comfy` por uma instância que sirva (mantendo o filtro de segurança dos projetos +18)."""
    from . import comfy_supervisor as cs
    from .comfy import ComfyClient

    base = ctx.comfy
    cands = [base] + [ComfyClient(i.url, cancel_event=base.cancel_event) for i in cs.INSTANCES if i.url != base.url]
    seen: list[str] = []
    for c in cands:
        if not c.ping():
            continue
        try:
            has_nodes = all(c.object_info(n) for n in nodes)
        except ComfyError:
            has_nodes = False
        if not has_nodes:
            seen.append(f"{c.url} (sem os nós {', '.join(nodes)}: ComfyUI antigo)")
            continue
        if files:
            missing = [f"{folder}/{name}" for folder, name in files.items() if name not in c.models(folder)]
            if missing:
                seen.append(f"{c.url} (faltam {', '.join(missing)})")
                continue
        elif not _sees_models(c):
            seen.append(f"{c.url} (não enxerga nenhum modelo: pasta de modelos vazia ou errada)")
            continue
        if c is not base:
            c.graph_filter = base.graph_filter      # mantém a política dos projetos +18
            c.meta = dict(getattr(base, "meta", {}) or {})
            ctx._comfy = c
            ctx.log(f"{what}: usando o ComfyUI em {c.url}" + (f" ({seen[0]})" if seen else ""))
        return
    raise ComfyError(f"nenhum ComfyUI no ar consegue rodar {what}. "
                     + (f"Vistos: {'; '.join(seen)}. " if seen else "Nenhuma instância respondeu: inicie uma em Opções > ComfyUI. ")
                     + "Veja Instalação (modelos e a pasta de modelos de cada ComfyUI).")

"""Creator Souls: pacotes de referências (RAG de imagens + estilo derivado) reaproveitáveis entre projetos."""
from .library import Soul, SoulLibrary, SoulManifest, get_library

__all__ = ["Soul", "SoulLibrary", "SoulManifest", "get_library", "soul_context"]


def soul_context(project, modality: str | None = None) -> list[tuple["Soul", float]]:
    """Souls ativas do projeto (com a força), na ordem anexada. Souls ausentes são ignoradas."""
    out = []
    lib = get_library()
    for ref in getattr(project.config, "souls", []) or []:
        if not ref.enabled or (modality and ref.modalities and modality not in ref.modalities):
            continue
        try:
            out.append((lib.get(ref.id), ref.strength))
        except KeyError:
            continue
    return out

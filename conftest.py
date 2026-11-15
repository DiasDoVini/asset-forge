"""Configuração comum dos testes: nada escreve na instalação real do usuário.

Sem isto, testes que rodam pipelines pelo caminho do CLI/MCP (record_direct_run) gravavam no histórico de jobs real
(`~/AssetForge/projects/.forge/jobs.db`), e preferências salvas pelas telas iam para o `settings.json` real.
"""
import os
import tempfile
from pathlib import Path

import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="forge-tests-"))
os.environ["FORGE_PROJECTS_DIR"] = str(_SANDBOX / "projects")
os.environ.setdefault("FORGE_SOULS_DIR", str(_SANDBOX / "souls"))


@pytest.fixture(autouse=True, scope="session")
def _isolated_settings():
    from forge_core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

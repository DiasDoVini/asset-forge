# Backlog

Pedidos registrados para depois (ordem = prioridade sugerida).

## Em andamento
- [ ] **Áudio** (`forge_audio`): SFX (Stable Audio 3), música (ACE-Step 1.5), sons retrô (sfxr, CPU),
      edição/efeitos/loops, mixagem em camadas, exportação WAV/OGG/MP3, player com forma de onda na UI.
- [ ] **VFX** (`forge_vfx`): shaders portáveis (GLSL → Godot / Unity / HLSL-XNA / Shadertoy) com editor ao vivo,
      sistemas de partículas com prévia, bake para flipbook/sprite sheet/GIF, export Godot/Unity.

## Publicação no GitHub (repositório público)
- [ ] **Documentação em inglês** — README principal em inglês (manter PT como `README.pt-BR.md`),
      guia de instalação, guia do MCP para agentes, referência de pipelines (gerada de `list_pipelines`).
- [ ] **Suporte a instalação em outras máquinas**
  - remover caminhos fixos desta máquina (junctions, `.python` local, porta fixa);
  - instalador/bootstrap (`install.ps1` / `install.sh`): uv, Node (ou web pré-buildada no release), `uv sync`,
    detecção de GPU/CUDA e do ComfyUI (Desktop existente ou instalação portátil dedicada);
  - config por arquivo (`~/.asset-forge/config.toml`) em vez de só variáveis de ambiente;
  - ~~Licença~~ (MIT, feito), CONTRIBUTING, CI (pytest + build web) e release com a web já compilada.
- [ ] **Página de Configurações e Downloads (estilo Modly)**
  - status das dependências (ComfyUI e cada instância, torch/CUDA, Node, espaço em disco);
  - catálogo de modelos por recurso (2D, animação, 3D, áudio…) com tamanho, origem, licença e
    "necessário para", botão baixar/remover, progresso e retomada, token do Hugging Face;
  - pipelines indisponíveis aparecem desabilitados com o que falta instalar;
  - pastas (projetos, modelos), porta, chave da Claude API para o crítico automático.

## Desktop
- [ ] **Versão desktop "port" da web (como o Modly)** — sem Electron pesado: janela nativa leve
      (ex.: pywebview/Tauri ou Edge `--app`) que sobe `forge up` em segundo plano, ícone na bandeja
      (abrir / recarregar / sair encerrando ComfyUI e servidor), instalador com atalhos.
      Hoje já existe o passo intermediário: `scripts/launch.ps1` + `scripts/install-shortcut.ps1`
      (atalho "Asset Forge" na área de trabalho, ícone em `branding/icon.ico`).

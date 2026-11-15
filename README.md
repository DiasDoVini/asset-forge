# Asset Forge

Gerador **local** de assets para jogos, usando [ComfyUI](https://github.com/comfyanonymous/ComfyUI) como motor
para os modelos de IA. Cobre **2D** (incluindo pixel art e animação de sprites), **3D**, **áudio**, **VFX** e
**vídeo**, com três interfaces sobre o mesmo núcleo: **CLI**, **MCP** (para agentes de IA, como o Claude Code) e
**UI web**.

Principais recursos: projetos com estilo global herdado por toda geração, assets versionados com linhagem, fila de
jobs com histórico, geração avançada (o Claude avalia as prévias e pede ajustes), Creator Souls (estilo e referências
reaproveitáveis) e exportação para engines (Godot, Unity, XNA/tModLoader).

## Estrutura do repositório

```
packages/forge_core    projetos, assets versionados, fila de jobs, cliente ComfyUI, API, MCP, CLI, Souls
packages/forge_2d      text→2D, text→pixel, image→2D, edit, enhance, remover fundo, pixelizer, animação de sprites
packages/forge_3d      geometria (simplify, lowpoly, print, split…), text/image→3D, texturas, rig e animação
packages/forge_audio   SFX, música, sons retrô (sfxr), edição e mixagem
packages/forge_vfx     shaders portáveis (GLSL) e sistemas de partículas, com exportação para engines
packages/forge_video   texto/imagem→vídeo (Wan 2.2), extensão, regeneração de trechos e edição
packages/forge_pixel   Pixel Studio: pixel art determinística (sem IA), com validadores de aceite
web/                   UI (Vite + React + shadcn/ui); módulos em web/src/modalities/<nome>
comfy/                 instâncias dedicadas do ComfyUI (Hunyuan3D :8189, TRELLIS.2 :8190), modelos e custom nodes
scripts/               launch.ps1, comfy-desktop.ps1, install-shortcut.ps1, download_models.py
branding/              ícone do app
BACKLOG.md             pedidos registrados para depois
```

Cada pacote Python é um membro do workspace do uv e registra sua modalidade no entry point `forge.modalities`.
Documentação específica: [`packages/forge_3d/README.md`](packages/forge_3d/README.md) (pipelines 3D, animação e
configuração) e [`comfy/README.md`](comfy/README.md) (instâncias dedicadas do ComfyUI).

## Setup

Requer [uv](https://docs.astral.sh/uv/), Node 20+ e ComfyUI ≥ 0.28 (o do Comfy Desktop serve).

```powershell
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"   # Python do projeto dentro do repo (veja a nota abaixo)
uv sync                          # Python 3.12 + todos os pacotes
npm install --prefix web
npm run build --prefix web       # gera web/dist, servido pelo servidor
uv run forge up                  # TUDO: ComfyUI (8188/8189/8190) + API e UI em http://127.0.0.1:8765
```

**Uma instância só** (2D e 3D juntos, um único `projects_dir`). Use `forge up`, que:
- sobe e **monitora** os ComfyUI: desktop :8188 (2D), 3d :8189 (Hunyuan3D) e trellis :8190 (TRELLIS.2).
  São processos separados porque têm dependências incompatíveis. Os que já estão rodando são adotados, e os que caem
  são reiniciados. Logs em `.logs/`;
- roda o servidor sob supervisão. Depois de mudar código: `uv run forge reload` (ou `forge reload --build-web`, ou o
  botão **Recarregar servidor** na UI). Ele espera os jobs em andamento terminarem e reinicia com o código atual.

Comandos avulsos: `forge serve` (só o servidor), `forge comfy up` (só os ComfyUI), `forge comfy instances` (estado)
e `forge comfy start <nome>`.

**Instalação e downloads** (barra lateral, `/setup`): lista tudo o que o Asset Forge usa e o estado nesta máquina.
- **Ferramentas e contas**: Ollama, FFmpeg, Git e uv (instalados pelo `winget`), token do Hugging Face (colado e
  conferido na tela) e a credencial do Claude (opcional).
- **ComfyUI**: Comfy Desktop (link), as instâncias Hunyuan3D e TRELLIS.2 (rodam `comfy/install*.ps1`), os pesos do
  TRELLIS.2/DINOv3 e a conversão da textura PBR do Hunyuan3D.
- **Modelos por recurso**: imagens (Z-Image), edição (Qwen-Image-Edit), upscale/fundo, vídeo 5B e 14B, áudio, áudio HQ,
  3D, 3D multivista e SDXL.
  - Os arquivos vão para a pasta compartilhada do ComfyUI: `FORGE_MODELS_DIR`, senão a do `extra_model_paths.yaml`,
    senão a do Comfy Desktop.
  - A tela compara o tamanho de cada arquivo com o esperado: um download cortado aparece como incompleto e continua
    de onde parou.
- **LLMs locais**: modelos do Ollama (Qwen3.8 27B recomendado).
- **Preparar o essencial** baixa e instala tudo o que é marcado como essencial e ainda falta.
- As tarefas rodam em segundo plano, com progresso, log e cancelar.

API:
- `GET /api/setup`
- `POST /api/setup/items/{id}/install`
- `GET|DELETE /api/setup/tasks`
- `GET /api/setup/tasks/{id}` (com log)
- `POST .../cancel`
- `POST /api/setup/hf-token`

**Tela de Opções** (engrenagem na barra lateral, `/options`):
- **ComfyUI**: estado de cada instância (online, carregando, iniciando, parada, não instalada, com o comando de
  instalação), versão do ComfyUI e do PyTorch, GPU e uso de VRAM, RAM, fila e PID.
  - Botões **Iniciar**, **Parar**, **Reiniciar**, **Liberar VRAM** (descarrega os modelos) e **Cancelar fila**, mais
    **Iniciar todas**, link para a interface do ComfyUI e o **log** ao vivo.
  - **Manter no ar** diz se o supervisor do `forge up` sobe a instância de novo quando ela cai. **Parar** desliga isso,
    para ela não voltar sozinha. A combinação fica em `.logs/comfy-control.json`, e o supervisor grava o próprio estado
    em `.logs/comfy-supervisor.json`.
  - Instâncias iniciadas por aqui continuam no ar quando o servidor recarrega. Parar ou reiniciar com geração em
    andamento pede confirmação.
- **Modelos e LLMs**: o que roda fora do ComfyUI.
  - **Ollama** (assistente de prompt): versão, modelos baixados (tamanho, quantização, VRAM em uso), catálogo
    (Qwen3.8 27B, Qwen3 8B, Qwen3 8B abliterated) com download e progresso, **Descarregar** (libera a VRAM),
    **Apagar** e o **modelo do assistente** (automático ou fixo, gravado em `settings.json` ao lado de `projects/`).
  - Também mostra o modelo de **visão** usado na verificação dos projetos +18 e nas Souls (Ollama), e o **Claude** (API)
    da geração avançada, do VFX com IA e do `3d.animate`.
  - O áudio usa o ComfyUI 2D; o sfxr e a edição de áudio não usam modelo.
- **Servidor**: endereço, início, pipelines, jobs em andamento, **Recarregar servidor**, pastas (com copiar) e as
  variáveis `FORGE_*` em uso.
- **Editor**: atalhos do editor de sprite e limpeza das preferências guardadas no navegador.

API: `GET /api/system`, `GET /api/comfy/instances[/{nome}]`, `POST /api/comfy/instances/{nome}/start|stop|restart|free|interrupt`
(`stop`/`restart` aceitam `{"force": true}`), `PUT .../autostart` e `GET .../log?lines=N`.

> **Nota (Windows + app desktop do Claude):** o app virtualiza `%APPDATA%`. Um Python instalado pelo uv de
> dentro do app em `AppData\Roaming\uv\python` não existe fora dele, e o `.venv` falha no seu terminal com
> "No Python at …". Por isso o Python base fica em `.python\` dentro do repositório (`UV_PYTHON_INSTALL_DIR`).
> Se o erro aparecer: instale com a variável acima (`uv python install 3.12.14`) e aponte o `home` de
> `.venv\pyvenv.cfg` para `.python\cpython-3.12.14-windows-x86_64-none`.

Modelos usados pelo 2D (pasta de modelos do Comfy; todos com nós nativos, sem custom nodes):

| Uso | Arquivo | Pasta |
|---|---|---|
| Text→2D | `z_image_turbo_bf16`, `qwen_3_4b`, `ae` | diffusion_models, text_encoders, vae |
| Image→2D / Edit | `qwen_image_edit_2511_fp8mixed`, `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16`, `qwen_2.5_vl_7b_fp8_scaled`, `qwen_image_vae` | diffusion_models, loras, text_encoders, vae |
| Remover fundo | `birefnet` | background_removal |
| Enhance | `4x-UltraSharp`, `seedvr2_3b_fp8_e4m3fn`, `seedvr2_ema_vae_fp16` | upscale_models, diffusion_models, vae |

Os nomes podem ser trocados por projeto em `project.yaml → 2d.models` (chaves em `forge_2d/graphs.py:DEFAULT_MODELS`).

Variáveis de ambiente: `FORGE_PROJECTS_DIR` (padrão `~/AssetForge/projects`), `FORGE_COMFY_URL`
(padrão `http://127.0.0.1:8188`), `FORGE_PORT` (8765), `FORGE_PROJECT` (projeto padrão da CLI).

## Conceitos

- **Projeto**: uma pasta com `project.yaml`. Guarda o **estilo global** (prompt, negative, system prompt
  para agentes, paleta, LoRAs, imagens de referência em `refs/`) e blocos por modalidade (`2d:`, `3d:`).
  Toda geração herda o estilo.
- **Asset**: `assets/<slug>/vNNN/` com os arquivos e um `asset.json` (pipeline, prompt, seed,
  params, `parent`). Versões são imutáveis. Editar gera `v002`, `v003`… e a linhagem fica registrada.
  Referência: `slug` (última versão) ou `slug@v003`.
- **Pipeline**: operação registrada por uma modalidade (`2d.text2d`, `3d.simplify`…). Recebe params
  validados (pydantic) e grava a nova versão. Jobs de GPU rodam um por vez; os de CPU, em paralelo.

Exemplo de `project.yaml`:

```yaml
name: Meu Jogo
style:
  prompt: 16-bit pixel art, dark fantasy, muted palette, side view
  system_prompt: Itens vistos de lado a 45°. Armas apontam para cima-direita.
  palette: ['#1a1c2c', '#5d275d', '#b13e53', '#ef7d57', '#ffcd75']
  refs: [ref_heroi.png]
2d:
  pixel_art: true          # pixeliza toda saída por padrão
  pixel: {size: 32, colors: 16, outline: '#1a1c2c', scale: 1}
  transparent: true
```

## CLI

```powershell
uv run forge project new meu-jogo --style "16-bit pixel art, dark fantasy"
uv run forge project set -p meu-jogo 2d.pixel_art=true 'style.palette=["#1a1c2c","#b13e53"]'
uv run forge project add-ref -p meu-jogo heroi.png

uv run forge pipelines                      # lista tudo
uv run forge describe 2d.edit               # parâmetros
uv run forge run 2d.text2d -p meu-jogo -P "prompt=frost sword" -P asset_type='"object"'
uv run forge run 2d.text2pixel -p meu-jogo -P "prompt=health potion" -P pixel_size=24
uv run forge run 2d.text2d -p meu-jogo -P "prompt=shop sign that reads 'POTIONS'" -P engine='"qwen-image-2.1"'
uv run forge run 2d.image2d -p meu-jogo -i foto.jpg
```

**Motores do Text → 2D** (`engine`, ou `2d.engine` no project.yaml):
- `z-image` (padrão): Z-Image Turbo, rápido.
- `qwen-image-2.1`: **Qwen-Image 2.1 Turbo**, o modelo de imagem da Alibaba de set/out 2026, com 8 passos e int8 (~7 GB),
  mais o codificador Qwen3-VL 8B. É melhor com texto escrito na imagem, composição e iluminação.
- `qwen-image-2.1-full`: a versão base do Qwen-Image 2.1, com 25 passos.

O Qwen-Image 2.1 usa os nós nativos `TextEncodeQwenImage21`/`QwenImage21Cache`, que exigem ComfyUI 0.38 ou mais
novo:
- O pipeline procura sozinho uma instância no ar com esses nós e os arquivos. A do TRELLIS serve: o
  `install_trellis.ps1` agora aponta ela para a pasta compartilhada de modelos, sem ser a padrão.
- Os modelos estão em Instalação > "Imagens 2D — Qwen-Image 2.1".
- O fundo transparente continua via BiRefNet. A transparência nativa do modelo (RGBA) ainda não é usada.

Os pipelines de 2D, áudio, vídeo e VFX usam o ComfyUI configurado (:8188) só se ele enxergar os modelos necessários.
Se não enxergar, usam outra instância do Asset Forge no ar que enxergue (`forge_core/comfy_select.py`), e o log do job
diz qual instância usou e por quê. Isso cobre o Comfy Desktop recém-instalado, que sobe com a pasta de modelos dele
vazia. Em Instalação, **Conectar pasta de modelos** grava um `extra_model_paths.yaml` ao lado do `main.py` do
Desktop, e ele passa a enxergar `D:\…\comfy-models` depois de reiniciado.

```powershell
uv run forge run 2d.edit -p meu-jogo -i frost-sword -P "instruction=make the blade red"
uv run forge run 2d.enhance -p meu-jogo -i frost-sword -P 'method="seedvr2"' -P scale=2
uv run forge run 2d.pixelize -p meu-jogo -i frost-sword -P size=32 -P 'outline="#1a1c2c"'
uv run forge asset list -p meu-jogo
uv run forge asset show frost-sword --versions -p meu-jogo --json
```

Todo comando aceita `--json`. Em caso de erro, a saída é `{"error": "..."}` com exit code 1.

## Organização: pastas e categorias

- **Pastas** (hierárquicas, ex.: `Personagens/Cavaleiro`, `Itens/Armas`) agrupam tudo de um mesmo objeto ou personagem.
- **Categorias** (uma por asset, com cor; editáveis na aba Estilo) servem para filtrar.
- Valem para todas as versões do asset e ficam em `assets/<slug>/_meta.json`. Pastas vazias e categorias ficam no `project.yaml`.
- **Derivados herdam** a pasta e a categoria da entrada: animações, vistas, edições que viram outro asset, image→3D…
  Organize o personagem uma vez e o resto cai no lugar.
- UI:
  - árvore de pastas com contagens; criar, renomear/mover e remover (os assets sobem para a pasta-mãe, nada é apagado);
  - subpastas aparecem como cards;
  - arraste assets para uma pasta;
  - seleção múltipla (checkbox ou Ctrl+clique) com "Mover para" e "Categoria";
  - filtro por categoria;
  - pasta e categoria também no detalhe do asset e no formulário Gerar.

```powershell
uv run forge folder list -p meu-jogo
uv run forge asset move knight knight-walk -p meu-jogo --folder "Personagens/Cavaleiro" --category character
uv run forge run 2d.text2d -p meu-jogo -P "prompt=goblin" --folder "Inimigos/Goblin" --category enemy
uv run forge asset list -p meu-jogo --folder Personagens -r
uv run forge folder rename "Personagens/Cavaleiro" "Herois/Cavaleiro" -p meu-jogo
```

MCP: `list_folders`, `organize_assets`; `run_pipeline`, `import_file` e `list_assets` aceitam `folder`/`category`.

## Importação de arquivos externos

Qualquer formato que o Asset Forge produz pode ser importado para um projeto (UI: **Importar…** na galeria, ou solte
arquivos; com **Shift** ao soltar, abre o diálogo com as opções de cada formato). Cada modalidade registra
`Importer`s (`Modality(importers=[...])`, contrato em `forge_core/pipeline.py`) que gravam os mesmos arquivos dos
pipelines dela, então viewer, editores e exportadores funcionam igual. O arquivo original fica como `source`.

| Importer | Extensões | Resultado |
|---|---|---|
| `2d.aseprite` | `.aseprite`, `.ase` | animação (camadas, opacidade, modos de mistura, cels ligados, tags, durações); 1 frame vira imagem |
| `2d.animated_image` | `.gif`, `.apng`, `.png`/`.webp` animados | animação com a duração de cada frame |
| `2d.spritesheet` | PNG/WebP/JPG… com opções de grade | animação: grade (colunas/linhas ou tamanho, margem, espaçamento), ilhas (`mode=auto`), retângulos ou o JSON Aseprite/TexturePacker ao lado |
| `2d.image_convert` | BMP, TIFF, TGA, PSD, ICO, DDS, GIF estático… | PNG |
| `2d.zip_frames` | `.zip` | sequência de PNGs, folha + JSON ou um `.aseprite` dentro do ZIP |
| `2d.pixelorama` | `.pxo` (Pixelorama 1.x) | animação achatada (camadas, opacidade, modos, máscara de recorte, quadros, tags) |
| `2d.krita` | `.kra` | imagem (a `mergedimage.png` do arquivo) |
| `3d.mesh` | GLB, glTF, OBJ, STL, PLY, OFF, 3MF | `model.glb` + topologia em `extra.mesh` |
| `audio.import` | WAV, FLAC, OGG, MP3, Opus, AIFF | `audio.wav` 24 bits + forma de onda + espectrograma |
| `video.import` | MP4, WebM, MOV, MKV, M4V | MP4 H.264 + poster (precisa do ffmpeg) |
| `vfx.shader_import` | `.glsl`/`.frag` com `vec4 effect(vec2 uv)`, `shader.json` | shader compilado + prévia |
| `vfx.particles_import` | `particles.json` | sistema de partículas + prévia |

PNG/JPG/WebP comuns entram como estão. **Em camadas**: pela galeria, `.pxo`, `.psd` (RGB 8 bits) e `.kra` (RGBA 8
bits, com keyframes) são lidos no navegador e viram `.aseprite` com camadas, grupos, opacidade e modos de mistura, que o
editor de sprite abre; o que não der para ler em camadas entra achatado pelo servidor. O **Aseprite** também é exportado (`2d.aseprite`: animação com tags ou imagem
de 1 frame), e o `2d.sheet` leva as tags da animação para o JSON.

```powershell
uv run forge asset formats                                   # importers e opções
uv run forge asset import heroi.aseprite -p meu-jogo
uv run forge asset import folha.png -p meu-jogo -O columns=8 -O rows=4 -O fps=12
uv run forge asset import ilhas.png -p meu-jogo -O 'mode="auto"'
```

MCP: `import_file(..., options={...})` e `list_import_formats`. API: `POST /api/projects/{slug}/assets` com o campo
`options` (JSON) e `GET /api/import/formats`.

## Editor de sprite (camadas e quadros)

Aba **Sprite** de imagens e animações 2D, e botão **Novo sprite** na galeria. É um editor no estilo Aseprite/Pixelorama
(veja o plano de paridade em [docs/pixel-editor-parity.md](docs/pixel-editor-parity.md)):
- **Camadas**: visibilidade, bloqueio, renomear (duplo clique), opacidade, 14 modos de mistura, grupos (aninháveis, com
  visibilidade e opacidade herdadas), subir/descer, duplicar, mesclar com a de baixo.
- **Linha do tempo**: quadros × camadas (● cel com conteúdo, ○ vazio, barra = cels ligados), duração por quadro,
  inserir/duplicar/duplicar ligado/apagar/mover (botões ou arrastando o número), ligar/desligar cels, **tags** com cor,
  direção (para frente, ao contrário, ping-pong) e repetições, **play** (tudo ou uma tag) e **onion skin** (quadros antes em
  vermelho e depois em azul, quantidade e opacidade).
- **Ferramentas** (a barra de contexto mostra só as opções da ferramenta atual):
  - lápis com **pixel-perfect** e **estabilizador**, borracha; Shift+clique traça linha desde o último ponto;
  - pincéis quadrado, círculo, linha horizontal/vertical, de 1 a 64 px;
  - **inks**: simples, composição alfa, copiar alfa+cor, travar alfa e **sombreamento** (anda na rampa da paleta:
    esquerdo clareia/avança, direito volta);
  - **simetria** horizontal, vertical ou nas duas, com eixos móveis (inclusive no meio de um pixel);
  - linha, **curva** de Bézier (pontas e depois os 2 controles), retângulo e **elipse** (contorno/preenchido, Shift =
    proporcional), **polígono** (cliques; Enter/duplo clique fecha) e **contorno** (à mão livre, preenche ao soltar);
  - **balde** contíguo ou global, com tolerância e comparação na camada ou em todas as camadas;
  - **gradiente** linear/radial da cor principal à secundária, com dithering Bayer 2×2/4×4/8×8 ou suave, opcionalmente
    só onde já há pixels;
  - conta-gotas (camada atual ou composição) e mover conteúdo do cel.
  - Cor principal (botão esquerdo) e secundária (direito) com alfa; paleta do projeto (ou DB16).
- Atalhos (como no Aseprite; **configuráveis** no ícone de teclado do editor, com conflitos destacados): B lápis, E borracha, G balde, Shift+G gradiente, I conta-gotas, L linha, Shift+L curva,
  U retângulo, Shift+U elipse, D contorno, Shift+D polígono, V mover (U/Shift+U de novo alterna preenchido), X trocar
  cores, [ ] pincel, , . quadro, Enter play, F3 onion, Alt+N / Alt+D quadro, Shift+N camada, Ctrl+Z/Y, Ctrl+S, Esc
  cancela curva/polígono, espaço ou botão do meio para mover a vista, roda para zoom.
- **Seleção** (M retângulo, Shift+M elipse, Q laço, Shift+Q laço poligonal, W varinha, Shift+W por cor; tolerância e
  camada/composição): Shift soma, Alt subtrai, Shift+Alt intersecta; Ctrl+A tudo, Ctrl+D nada, Ctrl+Shift+I inverter.
  A pintura fica restrita à seleção; Del apaga o selecionado.
- **Copiar/colar e transformar**: Ctrl+C (Ctrl+Shift+C copia a composição), Ctrl+X, Ctrl+V (também cola imagens de
  outros programas); a seleção flutuante se move arrastando, escala pelas alças (Shift mantém a proporção) e gira por
  fora dos cantos (Shift: passos de 15°) com **RotSprite** (gira sem borrar nem inventar cores) ou vizinho mais
  próximo; X/Y/L/A/ângulo editáveis, espelhar e ±90°; Enter aplica, Esc cancela; Ctrl+T levanta a seleção.
- **Menu Sprite**: tamanho do canvas (com âncora), escalar sprite, aparar ao conteúdo, cortar à seleção, espelhar e
  girar 90°/180° — em todas as camadas e quadros, mantendo cels ligados.
- **Paleta do sprite** (painel lateral): esquerdo = cor principal, direito = secundária, Shift = intervalo; editar
  cor/hex/alfa (no modo indexado os pixels daquela cor mudam junto), adicionar, remover, mover, ordenar (matiz, brilho,
  saturação), rampa entre as pontas, extrair as cores do sprite, reduzir para 4–64 cores (median cut em OKLab),
  paletas prontas (PICO-8, DB16, DB32, Endesga 32, Game Boy, Sweetie 16, 1-bit), usar/salvar a paleta do projeto e
  importar/exportar **.gpl, .pal (JASC e RIFF), .hex, .txt (Paint.NET), .ase (Adobe), PNG e .aseprite**.
- **Menu Cor**: modo **RGB, Indexado** (com ou sem dithering Bayer) **ou Tons de cinza** — no indexado só entram
  cores da paleta, no cinza só tons de cinza, e o `.aseprite` sai em 8/16 bits como no Aseprite; ajustes com prévia
  ao vivo no canvas (matiz/saturação/brilho, brilho/contraste, inverter, dessaturar, posterizar, substituir cor,
  contorno por fora/dentro, sombra projetada, gradient map, ajustar à paleta) no cel, na camada ou no sprite,
  respeitando a seleção.
- **Tilemaps**: camada de tilemap nova (botão ▦ nas camadas) ou "Converter em tilemap" (recorta pela grade do sprite e
  reaproveita tiles iguais). No modo **Tiles** (Tab alterna), lápis/borracha/balde/conta-gotas colocam e pegam tiles do
  painel do tileset; no modo **Pixels** a pintura vira edição de tiles — *Auto* cria/reaproveita tiles, *Manual* muda o
  tile em todas as cópias. Ajustes de cor numa camada de tilemap mudam os tiles. Exporta o tileset em PNG e o mapa para o
  **Tiled** (.tmj). Vai para o `.aseprite` como no Aseprite 1.3 (e o servidor lê).
- **Fatias** (Shift+C): arraste para criar, clique para selecionar e arraste para mover; nome, cor, **9-slice** e
  **pivô** no painel. Vão no `.aseprite` e no JSON do sprite sheet (formato Aseprite).
- **Menu Exibir**: modo **tiled** (repetição horizontal, vertical ou nos dois, e a pintura dá a volta nas bordas), grade
  configurável (salva no `.aseprite`) com **encaixe** de seleção, transformação e fatias, **réguas e guias** (arraste da
  régua; de volta para a régua apaga), **imagem de referência** (arquivo ou asset do projeto, opacidade, escala, por
  cima/por baixo, Alt+arrastar move) e **prévia flutuante** (arrastável, zoom próprio, toca a animação, respeita o tiled).
- **Efeitos de camada** (painel lateral, não destrutivos como no Pixelorama): contorno com espessura, sombra, matiz/
  saturação/brilho, brilho/contraste, inverter, dessaturar, posterizar, gradient map, substituir cor e ajustar à paleta,
  em ordem, liga/desliga e com parâmetros ao vivo; **máscara de recorte** (a camada só aparece onde a de baixo tem
  pixels). Vão no `.aseprite` e o servidor compõe igual (`forge_2d/effects.py`). Mesclar com a de baixo aplica os efeitos.
- **Desenhar com a animação tocando**: o traço fica no quadro onde começou ou, com **✎ cada quadro** na linha do tempo,
  continua em cada quadro que passa.
- **Texto** (T): fonte pixel 3×5 embutida, fontes pixel do Google Fonts (Silkscreen, Pixelify Sans, Press Start 2P,
  VT323, Tiny5, Jersey 10), qualquer .ttf/.otf carregado ou fonte do sistema, sempre sem antialiasing; entra como
  seleção flutuante para posicionar.
- **Salvar versão** grava um `.aseprite` com tudo (camadas, grupos, ligações, tags) como nova versão do asset: o servidor
  gera a animação (ou imagem, se tiver 1 quadro) e guarda o `.aseprite` como fonte, de onde o editor reabre com as camadas.
  **.aseprite** baixa o arquivo para abrir no Aseprite.

## Chat do projeto (gerar conversando)

Aba **Chat** de cada projeto. Você pede em linguagem natural ("crie um ícone de poção azul", "agora em pixel art 32px",
"tire o fundo do baú") e um modelo controla o Asset Forge:
- Ele consulta os pipelines e os parâmetros, roda os jobs pela fila normal (aparecem em Jobs e Workflows, com origem
  `chat`), olha a prévia e responde.
- Os assets aparecem na conversa (imagem, vídeo ou áudio, com link) e ficam no projeto com versão, como qualquer outro.
- **Provedor e modelo por conversa**: local (Ollama; prefira um modelo com ferramentas, como o Qwen3.8 27B) ou Claude
  pela API, com nível de esforço. O padrão fica em Opções > Assistente de IA.
- As conversas ficam em `<projeto>/.forge/chats/`.
- Projetos +18 não mandam prévias para o modelo.
- Dá para parar no meio, e o progresso do job aparece na conversa.

## AI Assistant (jobs que falharam)

O botão **Diagnosticar com IA** aparece nos jobs com erro (na fila e na aba Jobs). A IA lê:
- o erro e o log;
- o workflow que o ComfyUI rejeitou;
- se cada modelo citado existe no disco e quais instâncias o enxergam;
- o estado das instâncias e o que falta em Instalação.

Ela explica a causa, lista os passos e propõe **uma correção que a tela aplica**: repetir, repetir com outros
parâmetros, abrir a Instalação, ou subir/reiniciar uma instância. O provedor e o modelo vêm de Opções > Assistente de
IA (local ou Claude), e dá para trocar no próprio diálogo. A chave do Claude pode ser salva ali (em `settings.json`) ou
vir de `ANTHROPIC_API_KEY` / `ant auth login`.

## Workflows no ComfyUI

O menu **Workflows** da barra lateral lista cada grafo que o Asset Forge mandou ao ComfyUI (pela interface, pelo MCP
ou pelo CLI), com estado ao vivo, instância, projeto/asset, duração, nós e modelos. Inclui os rejeitados na validação,
com o erro.
- **Abrir no ComfyUI** converte o grafo para o formato do editor de nós e abre na instância certa, pelo nó auxiliar
  `asset_forge_workflows`, ligado em cada instância. Ele só vale depois de reiniciar a instância.
- Também dá para baixar o `.json` (UI ou API) e cancelar o que está na fila.

## Texturas, luz 2D e atlas

- `2d.seamless`: deixa uma textura repetível sem emenda, ou gera do texto e já deixa repetível. Grava uma prévia 3×3 e
  a medida da emenda antes e depois.
- `2d.material`: gera altura, normal (OpenGL para Godot/Blender, DirectX para Unity/Unreal), AO e rugosidade de uma
  textura, com prévia iluminada.
- `2d.normalmap`: normal map de sprite para a luz 2D do Godot (CanvasTexture) e do Unity (Sprite Lit).
  - O chanfro vem da transparência (6% do sprite por padrão) mais o detalhe da luminância.
  - Em animações, roda quadro a quadro.
  - Em pixel art, as normais ficam em 8 direções.
- `2d.atlas`: empacota vários assets (ou uma pasta), incluindo cada quadro das animações, num atlas.
  - Recorta a transparência, com espaçamento e extrusão de borda.
  - Gera o JSON do TexturePacker (Unity com o TexturePacker Importer, Phaser, PixiJS) e um `SpriteFrames` `.tres` do
    Godot 4 com as animações e durações.

Rodam em CPU, sem modelos novos. A pesquisa que levou a eles está em `docs/market-research-2026-10.md`.

## Recortar sprites de uma folha (`2d.slice`)

Aba **Recortar** de qualquer imagem 2D: um editor que mostra a folha com a grade por cima.
- **Grade**: colunas × linhas ou tamanho da célula, margem e espaçamento; **Detectar grade** acha a grade pelos vãos
  entre sprites ou, em tilesets contínuos, pelas emendas.
- **Automático**: cada ilha de pixels vira um recorte (com "juntar vão ≤ N px" para partes soltas).
- **Manual**: arraste para desenhar retângulos; edite X/Y/L/A; Delete remove.
- Clique ou arraste sobre as células para marcar/desmarcar (células vazias já vêm desmarcadas), dê nome a cada recorte.
- Resultado: um asset por recorte e/ou uma animação (com prévia, FPS, loop, alinhamento), aparar ao conteúdo e margem.
  Os pixels não são reamostrados. Os novos assets ficam em `<pasta da folha>/<folha>-slices` e o recorte fica
  registrado num asset `spriteset` (folha marcada, `slices.json` e as peças).

```powershell
uv run forge run 2d.slice -p meu-jogo -i heroi-folha -P 'grid={"columns": 8, "rows": 4}' -P animation=true
uv run forge run 2d.slice -p meu-jogo -i itens -P 'mode="auto"' -P trim=true
```

## Animação de sprites (2D)

Um sprite base (personagem, item…) gera **animações**: assets `kind: animation` com
`extra.base` = sprite de origem. A hierarquia fica:

```
projeto
└── knight                 sprite base (estado inicial)
    ├── knight-walk        animação (frames/, animation.json, sheet.png, preview.gif)
    ├── knight-attack
    └── knight-idle
```

- **Gerar**: `2d.animate` com um preset (`idle`, `walk`, `run`, `attack`, `jump`, `hurt`, `death`, `cast`,
  `spin`, `float`) e/ou um prompt livre ("andando para a frente, de perfil"), mais o nº de frames.
  - Método `video` (Wan 2.2 14B i2v + Lightning): gera o movimento como vídeo e extrai N frames espaçados.
    Em loops, usa primeiro frame = último frame, então o ciclo fecha.
  - Método `edit` (Qwen-Image-Edit): gera pose a pose pelos keyframes do preset. É mais rápido, mas o movimento fica mais rígido.
  - Pós-processo comum: remove o fundo, alinha os frames (pés na mesma linha, "in place") e, se o base for
    pixel art, pixeliza tudo com a mesma grade e a paleta do sprite base.
- **Editar**: `2d.anim_frame` refaz um frame com IA. `2d.anim_edit` aplica operações sem IA (apagar, duplicar, mover,
  duração, fps, loop, inverter, ping-pong, espelhar, substituir/inserir frame). Cada edição gera uma nova versão.
- **Exportar** (UI "Exportar", `forge export`, MCP `export_asset`):
  - `2d.gif`: GIF animado com transparência.
  - `2d.sheet`: grade/linha/coluna + JSON no formato Aseprite ou TexturePacker.
  - `2d.frames`: PNGs separados.
  - `2d.spriteset`: no sprite base, todas as animações dele, uma linha por animação, com tags no JSON.

```powershell
uv run forge run 2d.animate -p meu-jogo -i knight -P 'preset="walk"' -P frames=8 -P "prompt=andando para a direita, de perfil"
uv run forge run 2d.anim_edit -p meu-jogo -i knight-walk -P 'ops=[{"op":"flip_h"}]' --asset knight-walk-left
uv run forge export knight-walk 2d.sheet -p meu-jogo -P 'layout="grid"' -P columns=4 -o export/
uv run forge export knight 2d.spriteset -p meu-jogo -o export/
```

Modelos do método vídeo: `wan2.2_i2v_{high,low}_noise_14B_fp8_scaled` (diffusion_models),
`wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise` (loras), `umt5_xxl_fp8_e4m3fn_scaled` (text_encoders),
`wan_2.1_vae` (vae), do repositório Comfy-Org/Wan_2.2_ComfyUI_Repackaged.

## Personagens 3D animáveis

Transforma um conceito de personagem (imagem ou modelo 3D) num asset pronto para animar, pela aba **Personagem**
(imagens, malhas e personagens riggados) ou pelos pipelines:

1. `3d.character`: a IA (Qwen-Image-Edit) redesenha o personagem em **T-pose** copiando a pose do manequim da
   biblioteca, sem armas, escudos, capas e objetos soltos (`remove`). Roupas largas podem ser ajustadas ao corpo,
   mantidas ou tiradas (`loose_clothing`). Cada item listado em `extract` (ex.: o cajado) vira um **prop 3D separado**.
   O corpo é gerado de novo em 3D (`<slug>-char-base`) e segue para o rig.
2. `3d.character_rig`: endireita o modelo (gerado de uma vista 3/4), ajusta a altura, limpa a malha, encaixa um
   **esqueleto humanoide** (nomes Unreal/UAL ou Mixamo, dedos opcionais), calcula pesos suaves e aplica
   **animações da biblioteca** reajustadas às proporções do personagem (conjuntos básico, combate, social ou
   clipes avulsos; com ou sem root motion). Toca cada clipe e aponta onde a malha rasga (`deformation.json`), além de
   gerar a prévia `poses.png`.
3. `3d.retarget`: adiciona mais clipes da biblioteca a um personagem já riggado.

A biblioteca é configurada em Opções › Animações 3D: pastas com GLBs de esqueleto + clipes, como a Universal Animation
Library 1 e 2 da Quaternius (CC0, pasta `Unreal-Godot`; arquivos `_RM` são as versões com root motion).

## Geração avançada (gerar → Claude avalia → ajustar)

Laço iterativo para resultados exigentes:
1. O asset é gerado.
2. O crítico avalia as **prévias** (no 3D, renders de vários ângulos) contra o brief, os critérios e as referências.
3. O crítico dá nota (0–10) e escolhe a **próxima ação** de um catálogo (editar, regenerar, retexturizar…).
4. Repete até atingir a nota-alvo, o crítico aceitar, ou o limite de iterações.

Cada iteração vira uma versão do mesmo asset.

- **Crítico automático**: Claude Opus 5.5 via API (`forge_core/critic.py`). Recebe imagens das prévias e das
  referências e devolve JSON estruturado; ações inválidas voltam para ele corrigir. Precisa de credencial do SDK:
  `ANTHROPIC_API_KEY` ou um perfil de `ant auth login`. Sem ela, a sessão cai para revisão manual.
  Modelo/esforço: `FORGE_CRITIC_MODEL`, `FORGE_CRITIC_EFFORT`.
- **Crítico manual / agente**: a sessão pausa a cada iteração. Quem avalia é você, na aba **Refinar**, ou o
  Claude Code via MCP: `refine_start(critic="agent")` devolve as prévias como imagens, e `refine_review(...)`
  aplica a avaliação e já roda a próxima iteração.
- Refinadores: `2d.asset` (pronto) e `3d.model` (modalidade 3D). Uma modalidade registra o seu em
  `Modality(refiners=[...])`; o contrato está em `forge_core/refine.py`.
- Placeholders nos parâmetros das ações: `$current` (último resultado), `$best`, `$reference`, `$references`.
- Sessões ficam em `<projeto>/.forge/refine/<id>/` e são retomadas se o servidor reiniciar.

## Áudio (`forge_audio`)

| Pipeline | O que faz | Precisa |
|---|---|---|
| `audio.sfx` | Text → SFX: impactos, passos, magias, UI, criaturas, explosões, ambiências em loop; várias variações por job | Stable Audio 3 small SFX (`quality: high` usa o medium) |
| `audio.music` | Text → Música: tags de estilo, letra opcional, BPM, tom, compasso; `loop` corta em compassos inteiros | ACE-Step 1.5 turbo |
| `audio.retro` | Sons 8/16-bit estilo sfxr (moeda, laser, explosão, pulo…), instantâneo | só CPU |
| `audio.variation` | Áudio → áudio: variações ou novo timbre mantendo a forma | os mesmos modelos |
| `audio.edit` | Cortar, fade, normalizar (pico/LUFS), pitch, velocidade, filtros, EQ, reverb, eco, distorção, bitcrush, loop | só CPU |
| `audio.mix` | Mixa camadas (impacto + whoosh + debris) com offset/ganho/pan | só CPU |

Cada versão grava `audio.wav` (24-bit), `waveform.png` (thumbnail), `spectrogram.png` e variações `var_02.wav…`.
Exporte com **Arquivo de áudio** (OGG/WAV/MP3/FLAC, taxa, mono/estéreo, variações numeradas, `.import` do Godot com loop).
Bloco opcional no `project.yaml`: `audio: {sound: "estilo dos SFX", music: "estilo da trilha", sample_rate: 48000, sfx_lufs: -16, music_lufs: -14}`.

Modelos (≈13,5 GB, vão para a pasta compartilhada do Comfy):

```bash
python scripts/download_models.py audio --list
python scripts/download_models.py audio
```

Na UI: player com forma de onda (arraste para selecionar, espaço toca), variações, “Manter/Remover seleção” e menu de efeitos.
O refinador `audio.sound` deixa o Claude avaliar pela forma de onda + espectrograma.

## VFX (`forge_vfx`)

**Shaders portáveis.** O código de um efeito é só `vec4 effect(vec2 uv)` (uv 0..1, origem embaixo à esquerda) usando
`uTime`, `uProgress` (one-shots), `uResolution`, `sampleTex(uv)` (sprite de entrada), uniforms declarados e o
[prelude](packages/forge_vfx/src/forge_vfx/glsl/prelude.glsl) (hash, vnoise, fbm, voronoi, rot2, polar, sdCircle, glow…).
O servidor valida em GLSL ES 3.00 estrito na GPU (moderngl) e exporta para **Godot 4** (`.gdshader` canvas_item),
**Unity** (ShaderLab), **XNA/tModLoader** (`.fx`), **GLSL/WebGL2**, **Shadertoy** e **HLSL** puro.

- `vfx.shader`: a partir do texto escolhe um dos 16 templates (fogo, escudo, portal, círculo mágico, raio, orbe, onda de
  choque, explosão, dissolve, contorno, holograma, cura, fumaça, cáusticas, brilhos, flash de dano) e aplica as cores do
  prompt; com chave da Claude API o Claude escreve um shader novo (recompila e corrige até 3×); agentes mandam `code`.
  Com um sprite em `input_asset`, usa efeitos aplicados sobre ele (dissolve, contorno, holograma, flash).
- `vfx.shader_edit`: novos valores, código, blend/duração ou instrução em linguagem natural.

**Partículas.** Definição JSON (px, origem no centro, y para cima): emissores com taxa/bursts, formas, direção/spread,
velocidade, vida, gravidade, arrasto, aceleração radial/tangencial, turbulência, curva de tamanho, gradiente de cor,
texturas embutidas ou `asset:<slug>`.

- `vfx.particles`: 15 presets (fogueira, fumaça, faíscas, explosão, magia, cura, veneno, chuva, neve, poeira, moedas,
  impacto, vaga-lumes, vórtice, confete) escolhidos pelo prompt, ou JSON do Claude/agente.
- `vfx.particles_edit`: escala, velocidade, quantidade, cores, JSON ou instrução.
- `vfx.texture`: textura de partícula com Z-Image (fundo preto → alpha pela luminância).

Exportação: **Flipbook** (sprite sheet + JSON Aseprite, GIF, quadros; loops sem salto) para qualquer engine,
**Shader para engine** e **Partículas para engine** (Godot `.tscn` com CPUParticles2D; Unity com JSON + script de
editor `AssetForgeParticles.cs` → menu *Tools ▸ Asset Forge ▸ Build Particle System*).

Na UI: prévia ao vivo em WebGL2 com controles dos uniforms, aba **Código** com compilação ao vivo, prévia de
partículas com escala/velocidade/quantidade/cor e aba **JSON**. O refinador `vfx.effect` avalia pelos quadros.

## Vídeo (`forge_video`)

| Pipeline | O que faz |
|---|---|
| `video.generate` | Text → vídeo. `ti2v5b`: Wan 2.2 TI2V 5B nativo (24 fps, até 720p, ~1 min por 2 s em 480p); `wan14`: o Z-Image gera o 1º quadro e o Wan 2.2 14B anima (melhor movimento) |
| `video.animate` | Imagem → vídeo; quadro final opcional (interpola) ou `loop` (termina onde começa) |
| `video.extend` | Continua a partir do último quadro e emenda no final |
| `video.regen_segment` | Refaz com IA só o trecho entre dois tempos ou **marcações**, mantendo os quadros das pontas (Wan 14B first-last-frame) |
| `video.edit` | Manter/remover trecho, velocidade, inverter, loop/boomerang, fades, emendar vídeos com transição, trilha de áudio (assets de áudio), redimensionar, fps (com interpolação), marcações |
| `video.from_animation` | Animação de sprites 2D → MP4 |

Cada versão grava `video.mp4` (H.264), `poster.png` e `video.json` (metadados + marcações). Exportação: MP4, WebM, GIF,
quadros PNG ou sprite sheet com JSON Aseprite. Precisa do ffmpeg (`winget install Gyan.FFmpeg`) e, para o TI2V 5B,
`python scripts/download_models.py video` (11,4 GB).

Editor na UI: linha do tempo com marcações (tecla **m**), seleção por arraste ou **i**/**o**, quadro a quadro (←/→),
“Manter/Remover trecho”, menu de efeitos, trilha de áudio, “Regenerar trecho” e “Estender” com prompt, e “Quadro”
(salva o quadro atual como imagem 2D).

## Pixel Studio (`forge_pixel`)

Pixel art **determinística**, sem ComfyUI nem IA: sprites com canvas, pivô e área segura exatos, paleta travada,
contorno automático de 1 px e validadores de aceite. Os presets padrão (`head`, `torso`, `limb`, `icon`, `tile`…)
seguem um jogo de plataforma de referência (16 PPU). A configuração do projeto (canvas, pivô, paleta, limites) é lida com a tool MCP
`pixel_config`, e o resultado é conferido com `pixel_validate`.

| Pipeline | O que faz |
|---|---|
| `pixel.map` | Desenha um sprite a partir de um pixel map (texto ASCII + legenda, formato `asset-forge-pixelmap/1`); lado `R`, `L` (22% mais escuro) ou ambos |
| `pixel.fit` | Encaixa qualquer imagem (ex.: render de IA sem fundo) no canvas/pivô/paleta/contorno da spec e grava também o pixel map |
| `pixel.autotile` | A partir de um tile base (16x16 ou 11x11), gera as 16 variantes de borda da máscara cardinal e/ou a lista mínima, com atlas + JSON e mapa de teste |
| `pixel.poses` | Desenha N poses de um inimigo, silhueta de dano e folha + JSON, validando cada pose |
| `pixel.slice` | Fatia peças de uma imagem inteira a partir de um JSON de fatiamento e encaixa cada uma na spec |
| `pixel.parallax` | Publica uma camada de parallax (640x360, repete em x) validada contra os tiles sólidos |

O pacote também traz a montagem do personagem por ossos (`rig.py`) e utilitários de prévia (zoom, contact sheet,
onion skin). Os testes de aceite estão em `packages/forge_pixel/tests/`.

## Conteúdo adulto (+18)

Cada projeto tem o interruptor **Conteúdo adulto (+18)** na aba Estilo. Só o usuário liga (UI ou
`forge project nsfw on --i-am-18 --project <slug>`), com confirmação de maioridade; agentes via MCP não conseguem.

- Projetos normais recusam prompts com conteúdo sexual explícito.
- Projetos +18 aceitam conteúdo adulto entre adultos. **Qualquer referência a menores de idade é sempre recusada**
  (texto dos parâmetros e o estilo do projeto são checados; não há como desligar). Não use fotos de pessoas reais.
- Em **qualquer** projeto, pedidos de automutilação/suicídio, violência sexual e zoofilia são recusados.
- Proteções ocultas em projetos +18 (`forge_core/safety.py`): um prompt de segurança é injetado no grafo do ComfyUI
  (positivo “personagens adultos” em todos os modelos de imagem/vídeo; negativo contra menores/automutilação onde
  cfg > 1), e **todo resultado passa por uma verificação visual local** (modelo de visão do Ollama, ex. `gemma4:12b`)
  antes de ser gravado — se parecer menor de idade ou automutilação, é descartado. Sem o Ollama, projetos +18 não geram.
- Miniaturas ficam desfocadas (clique no selo **+18** para mostrar); o crítico automático (Claude API) e as prévias
  enviadas a agentes via MCP ficam desativados nesses projetos — revise na UI.
- `2d.checkpoint` gera com qualquer checkpoint SD/SDXL/Pony/Illustrious + LoRAs que você colocar em
  `models/checkpoints` e `models/loras` (seletores na UI).
- **Melhorar prompt** (botão no campo de prompt) usa um LLM local via Ollama, funciona offline e em qualquer projeto,
  respeitando a mesma política. O modelo é escolhido nesta ordem:
  - `FORGE_LOCAL_LLM`;
  - o escolhido em Opções > Modelos;
  - em projetos +18, um "abliterated";
  - nos demais, o primeiro do catálogo (**Qwen3.8 27B**, da Alibaba, ago/2026: 256K de contexto, com visão).

## Creator Souls (referências reaproveitáveis entre projetos)

Uma **Soul** é um pacote construído a partir de uma pasta de referências (ex.: todos os sprites de um jogo) que dá a
um projeto a "alma" visual daquela coleção, sem treinar modelo:

- **RAG de imagens**: cada referência é indexada pelo nome (catálogo `CATALOG.md`/CSV na pasta, detectado sozinho),
  categoria, cor e legenda opcional. Para cada prompt, as referências mais parecidas são buscadas (BM25 + sinônimos
  pt→en: “espada de fogo” → *FieryGreatsword*) e usadas como guia de estilo.
- **DNA visual derivado**: paleta (k-means), escala de pixel (ex. 2x), tamanho típico por categoria, cor do
  contorno e um prompt de estilo escrito pelo modelo de visão local (Ollama). Tudo editável.
- **Plugin**: biblioteca global em `<dados>/souls/` (`FORGE_SOULS_DIR`), anexada a projetos na aba **Estilo** com
  força 0–1; exporta/importa como arquivo `.soul`.

Ao anexar, o estilo efetivo do projeto ganha o prompt, a paleta e o bloco de pixel da Soul. O pipeline
**Gerar com Soul** (`2d.soul`) faz o efeito completo:
- `restyle` (padrão): gera o conteúdo com o Z-Image (na orientação das referências — itens diagonais etc.) e o
  Qwen-Image-Edit redesenha no estilo das 2 referências mais parecidas; depois pixeliza com tamanho, escala e contorno
  da coleção.
- `derive`: transforma diretamente a referência mais parecida (mais fiel ao estilo, mais próximo do original).
Referências podem ser escolhidas na faixa “Referências da Soul” do formulário, e `soul:<id>/<ref>` vale como
entrada/referência em qualquer pipeline. `2d.image2d` também usa as refs da Soul quando o projeto não tem refs próprias.

```bash
forge soul create Terraria --from "C:\...\VanillaImages"   # ~13k refs em alguns minutos
forge soul search terraria "espada de fogo"
forge soul attach terraria -p terraria-mods --strength 0.8
forge soul export terraria .   # terraria.soul
```

Agentes (MCP): `list_souls`, `soul_search` (com miniaturas), `attach_soul`. Atenção a direitos: Souls de obras de
terceiros servem para mods/estudo; para jogos comerciais prefira `restyle` com força menor. Fase 2 (planejada):
treinar LoRA a partir de uma Soul.

## Histórico de jobs

Todo job (UI, CLI ou MCP) fica registrado em `<FORGE_PROJECTS_DIR>/.forge/jobs.db`, com os parâmetros, os logs e o erro.
O histórico sobrevive a reinícios do servidor; jobs que estavam rodando quando o servidor caiu viram "interrompido".

- UI: página **Fila de jobs**, com filtros Falhas/Concluídos e as ações **Tentar de novo** (mesmos parâmetros),
  **Variação** (seed nova) e **Editar e repetir** (abre o formulário preenchido).
- CLI: `uv run forge jobs --failed` e `uv run forge retry <job_id> [-P chave=valor] [--new-seed]`.
- MCP: `list_jobs(failed_only=True)` e `retry_job(job_id, params, new_seed)`.
- API: `GET /api/jobs?status=error,cancelled&project=…`, `POST /api/jobs/{id}/retry`, `DELETE /api/jobs/{id}`.

## Agentes (MCP)

Registrado globalmente para o Claude Code (escopo de usuário, em `~/.claude.json`). Para registrar em outra máquina:

```powershell
claude mcp add --scope user asset-forge -- "C:\caminho\Asset Forge\.venv\Scripts\forge.exe" mcp
```

Tools: `list_projects`, `create_project`, `get_project`, `update_project`, `add_reference`,
`list_pipelines`, `run_pipeline`, `list_assets`, `get_asset`, `import_file`, `tag_asset`, `export_asset`,
`list_folders`, `organize_assets`, `list_jobs`, `retry_job`, `list_souls`, `soul_search`, `attach_soul`,
`list_refiners`, `refine_start`, `refine_review`, `refine_status`, `refine_list`, `refine_cancel`,
`pixel_config`, `pixel_validate`.
`run_pipeline` e `get_asset` devolvem uma miniatura da imagem.

## Atalho na área de trabalho

`powershell -ExecutionPolicy Bypass -File scripts/install-shortcut.ps1` cria o atalho **Asset Forge** (ícone em
`branding/icon.ico`, gerado pelo próprio app). Ele sobe `forge up` em segundo plano quando preciso e abre a UI numa
janela de app (Edge `--app`). Logs em `.logs/forge-up*.log`.

## UI web

`uv run forge serve` e abra http://127.0.0.1:8765. Para desenvolver a UI com hot reload:
`npm run dev --prefix web` (porta 5173, com proxy para a API).

- **Assets**: galeria com busca e filtro por modalidade; arraste arquivos para importar.
- **Gerar**: qualquer pipeline, com formulário gerado do schema dos parâmetros.
- **Estilo**: estilo global, system prompt, paleta, referências e blocos por modalidade.
- **Detalhe do asset**: viewer, histórico de versões, metadados e tags, "usar como entrada".
  No 2D, a aba **Editar** tem lápis, borracha, conta-gotas, espelhar/girar e máscara com
  instrução para editar uma região com IA.

## 3D

Os pipelines `3d.*` (geração com Hunyuan3D 2.1 ou TRELLIS.2, edição, lowpoly, reparo, UV e texturas, rig e animação, impressão 3D e divisão de peças) estão documentados em [`packages/forge_3d/README.md`](packages/forge_3d/README.md). Os ComfyUI dedicados são instalados por `comfy/` (veja [`comfy/README.md`](comfy/README.md)).

## Estendendo

Uma modalidade é um pacote Python que expõe `Modality` no entry point `forge.modalities`
(veja o topo de `forge_core/pipeline.py`) e, opcionalmente, `web/src/modalities/<nome>/index.tsx`
chamando `registerModality({viewers, panels, thumbnail, routes})` (veja `web/src/core/registry.ts`).

## Testes

```powershell
uv run pytest packages
npx --prefix web tsc -b
npm test --prefix web            # vitest: documento de sprite e .aseprite no navegador
```

## Licença

O **código** deste repositório está sob a [licença MIT](LICENSE): uso pessoal e comercial, modificação e
redistribuição livres, mantendo o aviso de copyright.

### Aviso sobre os modelos de IA e as ferramentas de terceiros

A licença MIT vale **só para o código do Asset Forge**. O Asset Forge não inclui nem redistribui pesos de modelos:
o `forge` (tela Instalação, `download_models.py`) baixa cada modelo da fonte original, e cada um vem com a **sua
própria licença**, que vale para o modelo e, em muitos casos, para o que você gera com ele. Algumas dessas licenças:

- proíbem ou limitam o **uso comercial** (ou exigem licença paga acima de um faturamento ou número de usuários);
- **excluem territórios** (por exemplo, a Tencent Hunyuan Community License, do Hunyuan3D, não vale na União
  Europeia, no Reino Unido e na Coreia do Sul);
- são **restritas** (é preciso aceitar os termos no Hugging Face antes de baixar, como o DINOv3 do TRELLIS.2);
- trazem **regras de uso aceitável** (licenças OpenRAIL, como a do SDXL).

| Recurso | Modelos usados |
|---|---|
| Imagens 2D e edição | Z-Image Turbo, Qwen-Image 2.1, Qwen-Image-Edit 2511, SDXL base 1.0 |
| 3D | Hunyuan3D 2.0 MV / 2.1 (forma e textura PBR), TRELLIS.2 + DINOv3 |
| Vídeo e animação | Wan 2.2 (TI2V 5B, I2V 14B) e LoRAs de aceleração |
| Áudio | Stable Audio 3, ACE-Step 1.5 |
| Upscale e remover fundo | BiRefNet e modelos de upscale |
| LLMs | modelos locais via Ollama (ex.: Qwen3) e, opcionalmente, a API do Claude (termos da Anthropic) |
| Motor | ComfyUI (GPL-3.0) e nós personalizados de terceiros (licenças próprias), rodando como processos separados |
| Animações 3D | Universal Animation Library (Quaternius, CC0), que você baixa à parte |

**Antes de usar comercialmente um asset gerado, leia a licença atual do modelo que o gerou** na página oficial
dele (model card no Hugging Face ou no site do autor). As licenças mudam entre versões, e esta lista é só um
ponto de partida, não aconselhamento jurídico. O Asset Forge e seus autores não dão nenhuma garantia sobre
direitos dos modelos ou dos assets gerados; a responsabilidade pelo uso é de quem gera.

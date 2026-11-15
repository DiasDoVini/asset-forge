# Paridade com Aseprite e Pixelorama — pesquisa e plano

Pesquisa feita em 15/10/2026. Versões de referência:
- **Aseprite v1.3.x**: a v1.3 saiu em nov/2023 e trouxe camadas de tilemap, skew e estabilizador; o ramo atual é o 1.3.18.
- **Pixelorama v1.2.3** (set/2026). A v1.1 trouxe tilemap, camadas de áudio, modo indexado e texto; a 1.1.1 trouxe tiles isométricos/hexagonais; a 1.2.x trouxe ferramenta de gradiente, exportação .aseprite, SVG e efeito Corner Pin.

Fontes: [docs do Aseprite](https://www.aseprite.org/docs/), [ink](https://www.aseprite.org/docs/ink/),
[dynamics](https://www.aseprite.org/docs/dynamics/), [sprite sheet](https://www.aseprite.org/docs/sprite-sheet/),
[blog v1.3](https://blog.aseprite.org/2023/11/27/aseprite-v13/), [site do Pixelorama](https://pixelorama.org/),
[ferramentas do Pixelorama](https://pixelorama.org/user_manual/tools/),
[releases do Pixelorama](https://github.com/Orama-Interactive/Pixelorama/releases),
[devlog Pixelorama v1.1](https://orama-interactive.itch.io/pixelorama/devlog/913933/pixelorama-v11-is-out).

## O que o Asset Forge já tem

- **Pixel Studio** (`web/src/modalities/pixel`): um editor de 1 camada e 1 quadro com a paleta travada pela spec. Ferramentas: lápis, borracha, balde 4-conexo, conta-gotas, linha, retângulo, selecionar/mover, espelho X, desfazer/refazer e contorno automático. Também tem a prévia do lado L, a área segura e o pivô, os validadores de aceite, e a edição como texto (pixel map).
- **Editor 2D** (`web/src/modalities/2d/ImageEditor.tsx`): lápis, borracha, conta-gotas, espelhar/girar e máscara para edição com IA.
- **Animação**: o asset `animation` guarda os quadros com duração e as tags. Há visualizador, edição sem IA pelo pipeline `2d.anim_edit` (apagar, duplicar, mover, fps, ping-pong, espelhar) e exportação para GIF, sprite sheet + JSON Aseprite/TexturePacker, quadros e `.aseprite`.
- **Formatos**: importa e exporta `.aseprite`, importa GIF/APNG, folha de sprites e ZIP, e tem o editor de recorte de sprites.
- **Diferenciais que os dois não têm**: geração e edição com IA, Souls, validadores de spec, pipeline e MCP.

## Comparativo (✅ tem · 🟡 parcial · ❌ falta)

| Área | Aseprite | Pixelorama | Asset Forge hoje |
|---|---|---|---|
| Camadas (visibilidade, bloqueio, opacidade, renomear, ordem) | ✅ | ✅ | ❌ (1 camada) |
| Grupos de camadas, modos de mistura | ✅ | ✅ (+ group blending) | 🟡 só lê do .aseprite |
| Máscara de recorte (clipping mask) | ❌ | ✅ | ❌ |
| Linha do tempo: quadros × camadas (cels), cels ligados | ✅ | ✅ | 🟡 quadros só no visualizador |
| Duração por quadro, tags com direção/repetição | ✅ | ✅ | 🟡 dados sim, editor não |
| Onion skin (antes/depois, cores, opacidade) | ✅ | ✅ | ❌ |
| Desenhar com a animação tocando | ❌ | ✅ | ❌ |
| Lápis pixel-perfect, tamanhos/formas de pincel, pincel personalizado | ✅ | ✅ | 🟡 só 1 px quadrado |
| Inks: simples, alpha, copiar alpha+cor, travar alpha, shading | ✅ | 🟡 (shading tool) | ❌ |
| Dinâmica (pressão/velocidade), estabilizador | ✅ | 🟡 | ❌ |
| Simetria horizontal/vertical/diagonal, eixo móvel | ✅ | ✅ | 🟡 só X no centro |
| Linha, curva Bézier, retângulo, elipse, polígono, contorno | ✅ | ✅ (sem contorno) | 🟡 linha e retângulo |
| Balde contíguo/global, tolerância, gradiente (dithering) | ✅ | ✅ (gradiente na 1.2.2) | 🟡 balde contíguo |
| Texto (fontes pixel) | 🟡 (via script/ext.) | ✅ | ❌ |
| Seleção: retângulo, elipse, laço, polígono, varinha, por cor, por desenho; somar/subtrair/intersectar | ✅ | ✅ | 🟡 só retângulo |
| Transformar: mover, girar, escalar, skew com algoritmos pixel art (RotSprite, cleanEdge, OmniScale) | ✅ (RotSprite) | ✅ | ❌ |
| Modo de cor RGB / Indexado / Tons de cinza | ✅ | ✅ (indexado na 1.1) | 🟡 paleta travada |
| Paletas: editar, ordenar, rampas, carregar/salvar (.gpl, .pal, .ase, .png, .hex) | ✅ | ✅ | 🟡 só a paleta do projeto |
| Ajustes: HSV, brilho/contraste, curvas, substituir cor, inverter, contorno, sombra, gradient map, pixelizar, posterizar | ✅ | ✅ (efeitos não destrutivos) | 🟡 contorno auto |
| Efeitos de camada não destrutivos | ❌ | ✅ | ❌ |
| Modo tiled (prévia em mosaico) | ✅ | ✅ | ❌ |
| Camadas de tilemap e tilesets (retangular; Pixelorama: iso/hex) | ✅ | ✅ | 🟡 autotile (pipeline) |
| Slices (9-slice, pivô) | ✅ | ❌ | 🟡 pivô/área segura da spec |
| Grade de pixels, grade customizada, guias, réguas | ✅ | ✅ | 🟡 grade de pixel |
| Imagem de referência | ✅ | ✅ | ❌ |
| Prévia em janela separada / mini-mapa | ✅ | ✅ | 🟡 prévias fixas |
| Canvas: redimensionar sprite/canvas, cortar, girar/espelhar tudo | ✅ | ✅ | 🟡 2D: girar/espelhar |
| Importar folha de sprites | ✅ | ✅ | ✅ (Recortar) |
| Exportar sheet (+JSON), GIF, PNG seq., vídeo | ✅ | ✅ (+ vídeo, SVG) | ✅ (vídeo pelo forge_video) |
| Formatos nativos (.aseprite/.pxo), PSD/Krita | .aseprite | .pxo, lê .aseprite/.psd/.kra | ✅ .aseprite; ❌ .pxo/.psd em camadas |
| Dados de usuário em elementos | ✅ | ✅ | 🟡 `extra` do asset |
| Camadas de áudio / camadas 3D | ❌ | ✅ | ❌ (áudio é outra modalidade) |
| Scripting (Lua), extensões | ✅ | ✅ (extensões) | 🟡 pipelines Python + MCP |
| CLI de exportação em lote | ✅ | ✅ | ✅ (`forge export`) |
| Atalhos configuráveis, layout de painéis | ✅ | ✅ | ❌ |

## Arquitetura proposta

Hoje há **dois** editores (Pixel Studio com paleta travada e o ImageEditor 2D). A proposta é um **único documento de sprite** no front: o formato `asset-forge-sprite/1`, com camadas, quadros, cels, tags, slices, paleta e modo de cor. Ele seria salvo como `sprite.json` + PNGs dos cels (ou direto como `.aseprite`, que já sabemos ler e gravar).

Sobre esse documento ficaria um **editor completo**, usado pelos assets 2D, de animação e de pixel art. O Pixel Studio continuaria sendo um "modo spec" desse editor, com paleta travada, validadores e pivô.

Ao salvar, o servidor gera uma nova versão (composição, `animation.json`, sheet, GIF), e assim a IA, os exportadores e o MCP continuam funcionando.

## Plano em fases

Cada fase é entregável e testável sozinha.

1. ✅ **Núcleo do documento + camadas e quadros** (feito em 15/10/2026; aba **Sprite** e botão **Novo sprite**).
   - Modelo de sprite (camadas, grupos, opacidade, visibilidade, bloqueio, modos de mistura, cels ligados), com undo/redo por comando.
   - Linha do tempo (quadros × camadas, arrastar, duração, tags), onion skin e play/loop.
   - Salvar e abrir como versão do asset e como `.aseprite` com camadas, reaproveitando o leitor/gravador atual.
2. ✅ **Ferramentas de desenho completas** (feito em 15/10/2026; ficou de fora: pincel personalizado a partir da seleção, que depende da Fase 3, e dinâmica por pressão de caneta).
   - Pixel-perfect, pincéis (tamanho, quadrado/redondo, pincel da seleção) e inks (simples, alpha, travar alpha, shading).
   - Simetria X/Y com eixo móvel, curva, elipse, polígono e contorno.
   - Balde global com tolerância, gradiente com dithering, estabilizador e conta-gotas com camada/composição.
3. ✅ **Seleção e transformação** (feito em 15/10/2026; ficou de fora: skew/inclinar e o algoritmo cleanEdge — o RotSprite cobre girar e escalar).
   - Seleções (laço, polígono, varinha, por cor, por desenho) com somar/subtrair/intersectar.
   - Copiar/colar entre quadros, mover/escalar/girar/skew com RotSprite ou cleanEdge, espelhar e girar a seleção.
   - Redimensionar o sprite e o canvas, e cortar.
4. ✅ **Cor e paletas** (feito em 15/10/2026; ficou de fora: curvas de cor e paleta por quadro).
   - Modos RGB/indexado/cinza, editor de paleta (ordenar, rampas, substituir cor em todo o sprite).
   - Importar e exportar `.gpl`, `.pal`, `.hex`, `.ase` e PNG.
   - Ajustes: HSV, brilho/contraste, inverter, posterizar, contorno, sombra e gradient map.
5. ✅ **Recursos de jogo** (feito em 15/10/2026; ficou de fora: tiles isométricos/hexagonais, espelhar/girar tiles e fatias com chaves diferentes por quadro).
   - Modo tiled, camadas de tilemap e tilesets (retangular primeiro; iso/hex depois), integrando com o autotile do Pixel Studio.
   - Slices com 9-slice e pivô, exportados no JSON.
   - Grade customizada, guias, imagem de referência, prévia flutuante e texto com fontes pixel.
6. ✅ **Extras do Pixelorama** (feito em 15/10/2026; ficou de fora: PSD de 16 bits/CMYK e KRA fora de RGBA 8 bits em camadas — esses entram achatados — e layout de painéis configurável).
   - Efeitos de camada não destrutivos (contorno com espessura, sombra, HSV, brilho/contraste, inverter, cinza, posterizar, gradient map, substituir cor, ajustar à paleta), em ordem e liga/desliga; máscara de recorte. Os dois vão no `.aseprite` (user data da camada) e o servidor compõe igual ao editor (`forge_2d/effects.py`).
   - Desenhar com a animação tocando: o traço fica no quadro onde começou ou, com **✎ cada quadro**, continua em cada quadro que passa.
   - Importar `.pxo` (camadas, grupos, máscara, quadros, tags), PSD (camadas, grupos, nomes unicode, RLE) e KRA (árvore de camadas, deslocamento, modos, keyframes). Pela galeria o navegador converte para `.aseprite` com as camadas; pelo CLI/MCP o servidor importa `.pxo` achatado (animação) e KRA pela `mergedimage.png`.
   - Atalhos configuráveis (ícone de teclado no editor): várias combinações por ação, conflitos destacados, restaurar padrão; salvos no navegador.
   - Camadas 3D e de áudio ficam fora: no Asset Forge, 3D e áudio já são modalidades próprias.

A Fase 1 é a base de todas as outras e a maior mudança de arquitetura. As fases 2 a 4 entregam a maior parte do que se usa no dia a dia.

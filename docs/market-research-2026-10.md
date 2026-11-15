# Pesquisa de mercado: o que falta no Asset Forge (out/2026)

Concorrentes avaliados:
- **2D:** Scenario, Layer, Leonardo, PixelLab, Retro Diffusion e Ludo.
- **3D:** Meshy, Tripo, Rodin, Sloyd, CSM e Kaedim.
- **Áudio:** ElevenLabs SFX v2.
- **Engines:** Unity AI e Roblox Cube.

## O Asset Forge já tem

Estilo treinado (Souls + LoRA), sprites animados, tilesets autotile, imagem/texto → 3D, segmentação 3D, controle por
agente via MCP, sheet + JSON (Aseprite/TexturePacker), direções de personagem e rig por partes.

## Lacunas, da mais para a menos valiosa

| # | Lacuna | Quem tem | Viável localmente com |
|---|---|---|---|
| 1 | Textura seamless + conjunto PBR (altura, normal, AO, roughness) a partir de 2D | Scenario, Layer, Leonardo, Unity AI | numpy/scipy + Qwen-Edit (já instalado) |
| 2 | Normal map para sprites 2D (luz 2D do Godot/Unity) | Laigter, SpriteIlluminator (sem IA) | numpy/scipy |
| 3 | Atlas com vários assets + presets de engine (Godot `.tres`, Unity, Phaser) | Layer/Scenario (plugins), Ludo | Python puro |
| 4 | Variações de paleta e cor em lote | Layer, Scenario | numpy |
| 5 | Folha de personagem consistente (turnaround) | Scenario, Layer, PixelLab | Qwen-Image-Edit 2511 (já instalado) |
| 6 | Loop de áudio sem emenda (OGG com LOOPSTART) | ElevenLabs SFX v2 | numpy/scipy |
| 7 | Cadeia de LOD + malha de colisão | Sloyd, Rodin | trimesh (já usado) |
| 8 | Sprite → partes do Spine | Layer | OCS (download grande) |

Mais adiante: rig humanoide automático com retarget (UniRig / Make-It-Animatable).

## Implementado nesta rodada

As lacunas 1, 2 e 3 (veja o README: `2d.seamless`, `2d.material`, `2d.normalmap` e o exportador de atlas).

Fontes:
- [Scenario textures](https://scenario.com/features/textures)
- [Layer Tileable Texture](https://layer.ai/tools/layer--tileable-texture.md)
- [Godot 2D lights](https://docs.godotengine.org/en/stable/tutorials/2d/2d_lights_and_shadows.html)
- [Aseprite Wizard (Godot)](https://godotengine.org/asset-library/asset/edit/21233)
- [PixelLab](https://pixellab.ai/)
- [ElevenLabs SFX v2](https://the-decoder.com/elevenlabs-releases-version-2-of-its-ai-sound-effects-model-with-longer-clips-and-better-audio-quality/)
- [Sloyd](https://sloyd.ai)
- [Layer Spine Components](https://layer.ai/tools/layer--create-spine-components)
- [UniRig](https://github.com/VAST-AI-Research/UniRig)

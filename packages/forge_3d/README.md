# forge_3d

3D modality of Asset Forge: AI mesh generation (ComfyUI) plus mesh processing for games and 3D printing.

Run everything from the monorepo root: `uv run forge ...`. Every pipeline writes a new asset version
(`assets/<slug>/vNNN/`) with an `asset.json` holding params, parent, seed and mesh stats in `extra`.

## Pipelines

| id | GPU | input | output | what it does |
|---|---|---|---|---|
| `3d.text_to_3d` | yes | – | new asset | prompt → concept image in the project style (Z-Image Turbo) → mesh (Hunyuan3D 2.1) |
| `3d.image_to_3d` | yes | image | new asset `<slug>-3d` | background removal + centering → mesh (Hunyuan3D 2.1). Optional `side_views` ({"left"/"back"/"right": asset or file}, input = front) switch to Hunyuan3D 2.0 multi-view; views are scaled to one common subject height. Views must be straight (exact front, sides at 90°; "left" = the subject's own left side, facing the left of the image). Sticker-style outlines are stripped from every image (`strip_outline`) |
| `3d.edit` | – | mesh | new version | list of `ops` in Y-up viewer coordinates: `transform`, `cut`, `primitive` (box/cylinder/sphere/cone, add/subtract/intersect), `text` (emboss/engrave), `mirror` (mirror/symmetrize), `mesh` (combine another asset) |
| `3d.ai_edit` | yes | generated mesh | new version | edits the asset's source image with Qwen-Image-Edit from an instruction and regenerates the mesh (same seed) |
| `3d.unwrap` | – | mesh / animation | new version | automatic UV (xatlas) + texture keeping the current colors (per part on animation assets) |
| `3d.refine_region` | yes | generated mesh | new version | regenerate a region (face, hands, a weapon) with much more detail: the region (brush `points`+`radius`, volumes `shapes`, `image_box` on the source image or a text `prompt` located by Claude) is cropped from the best view (source or real side views) with enough context, 4x-upscaled, regenerated with Hunyuan3D 2.1, placed by camera + ICP and stitched (single-surface blend band, screened Poisson). Keeps the triangle count; the region gets more of them. ~5 min |
| `3d.from_raw` | no | generated mesh | new version | rebuild model.glb from the dense surface kept with it (`highres.glb`, or `raw.glb` on older assets): cleanup none/clean/reconstruct, Taubin smoothing, any triangle budget; lined up with the current model by ICP |
| `3d.bake_pbr` | no | textured mesh / animation | new version | complete the PBR set: normal map baked from the dense surface, uniform metallic/roughness where none exist; saves `basecolor/metallic/roughness/normal.png` |
| `3d.skeleton` | no | mesh / skeleton animation | animation asset `<slug>-anim` | skinned skeleton: auto-fit Mixamo-compatible humanoid (neck/shoulders from the silhouette, hands from the arm mass, legs over the feet) or creature (spine, head, tail, one leg per foot, arched knees), or edited `joints`; skin weights by geodesic voxel distance + bone heat diffusion; skinned glTF (joints/weights/inverse bind matrices). Clips use the same format as part rigs (bone = part) |
| `3d.texture_ai` | yes | generated mesh / animation | new version | `method: "hunyuan3d-paint"` (default in the GUI): Hunyuan3D-Paint 2.1 generates 6 consistent views from the source image and bakes them into the UVs (needs the dedicated instance + converted paint weights, see `comfy/`). `method: "project"`: projects the source image (camera found by silhouette matching) + back/side views generated with Qwen-Image-Edit; unseen areas take the nearest seen color |
| `3d.apply_texture` | – | textured mesh / animation | new version | replace textures with painted images (`textures: {"model" or part: path}`) - used by the Pintar tab |
| `3d.rig` | – | mesh (or animation) | new asset `<slug>-anim` (kind `animation`) | split into animatable parts by regions (box/cylinder/sphere) with pivots and hierarchy, `components`, add other assets as `props`; on an animation asset it splits more parts and keeps the clips (pass `-a <same slug>`) |
| `3d.animate` | – | animation | new version | add/replace a clip: explicit `clip` keyframes, procedural `presets` (hinge, slide, spin, bob, recoil, shake, pulse), or a `prompt` planned by Claude (`@slug` mentions become props; reports missing parts) |
| `3d.character` | yes | image / mesh | animation asset `<slug>-char` (+ `<slug>-char-base`, one mesh asset per extracted prop) | concept → animatable character: Qwen-Image-Edit redraws it in a T-pose copying the animation library's mannequin, without `remove` (weapons, held items, capes...), `loose_clothing` fitted/kept/removed; every `extract` item is redrawn alone and generated as its own 3D prop; the body is regenerated in 3D (`3d.image_to_3d`) and rigged as `3d.character_rig` |
| `3d.character_rig` | no | mesh / skeleton animation | animation asset `<slug>-rig` | standing character (T/A pose) → squared up to face +Z, scaled, cleaned (`mesh_fix` clean/rebuild), humanoid skeleton with Unreal/UAL (`root`, `pelvis`, `upperarm_l`...) or Mixamo names, optional finger bones, skin weights, library animations retargeted (`animations.sets` basic/combat/social/all + `names`, `root_motion`), deformation check (`deformation.json`) and `poses.png` |
| `3d.retarget` | no | skeleton animation | new version | add library animations to a humanoid skeleton asset (`3d.character_rig` or `3d.skeleton`), keeping (or `replace`-ing) its clips |
| `3d.simplify` | – | mesh | new version | quadric decimation, keeps UVs/texture |
| `3d.lowpoly` | – | mesh | new asset `<slug>-lowpoly` | stylized low poly, flat colors sampled from the source, project palette |
| `3d.repair` | – | mesh | new version | duplicates, non-manifold, floaters, holes, normals; `make_solid` for a watertight solid |
| `3d.transform` | – | mesh | new version | scale / rotate / translate / pivot (bottom_center = stands on the ground) |
| `3d.convert` | – | mesh | new version | glb, obj, stl, ply, 3mf |
| `3d.print` | – | mesh | new asset `<slug>-print` | mm scale, watertight solid, auto-orientation, wall + build-volume checks → STL + 3MF + report |
| `3d.split` | – | mesh | new asset `<slug>-parts` | cuts to fit the printer, dowel-pin holes, one STL per part + pin + exploded preview |

`uv run forge describe <id>` prints every parameter. Examples:

```bash
uv run forge run 3d.text_to_3d -p my-game -P prompt="wooden treasure chest with iron bands" --json
uv run forge run 3d.image_to_3d -p my-game -i concept-art.png -P octree_resolution=384
uv run forge run 3d.lowpoly -p my-game -i treasure-chest -P target_faces=600
uv run forge run 3d.print -p my-game -i treasure-chest -P target_height_mm=80
uv run forge run 3d.split -p my-game -i treasure-chest-print -P pin_diameter_mm=6
uv run forge run 3d.ai_edit -p my-game -i treasure-chest -P "instruction=open the lid, showing gold coins inside"
uv run forge run 3d.edit -p my-game -i treasure-chest --json -P 'ops=[{"op":"primitive","shape":"cylinder","mode":"subtract","size":[0.1,2,0.1]},{"op":"text","text":"V1","mode":"subtract","height":0.1,"depth":0.02,"matrix":[[1,0,0,0],[0,1,0,0.5],[0,0,1,0.7],[0,0,0,1]]}]'
```

The web UI has an **Editar** tab on every mesh asset with the same tools (gizmo placement, live preview),
browser sculpting, AI edit and "generate a part with AI and attach it". See `ROADMAP.md` for what's next.

## Animation

An animation asset holds `parts/<name>.glb` (Y-up, world coordinates), `rig.json` (pivots, hierarchy),
`clips.json` and `animated.glb` (part hierarchy + one glTF animation per clip, game-engine ready).

Keyframes are offsets from the rest pose in the parent's frame: `position` (units), `rotation_deg`
(intrinsic XYZ Euler, three.js order) and `scale`. `attach` makes a root part (a prop) follow another part
between `start` and `end` (picking up / carrying / dropping); it is baked into keyframes on export.

```bash
# lid of a chest: everything inside the region becomes "lid", hinged at the back edge
uv run forge run 3d.rig -p my-game -i chest --json -P 'parts=[{"name":"lid","region":{"shape":"box","size":[2.4,0.75,1.8],"matrix":[[1,0,0,0],[0,1,0,1.1],[0,0,1,0],[0,0,0,1]]},"pivot":[0,0.76,-0.66]}]'
uv run forge run 3d.animate -p my-game -i chest-anim -P name=open -P duration=2.5 -P 'presets=[{"part":"lid","preset":"hinge","axis":"x","amount":-95}]'
uv run forge run 3d.animate -p my-game -i soldier-anim -P "prompt=picks up the @army-rifle from the floor and puts it on his back"
uv run forge export chest-anim 3d.animation_glb -p my-game -o out/
```

Prompt planning calls Claude (`claude-opus-5-5`, structured output) and needs `ANTHROPIC_API_KEY` or an
`ant auth login` profile on the server. Exporters: `3d.animation_glb` (GLB with selected clips) and
`3d.animation_media` (GIF/MP4/WebM from frames the web UI renders). The web UI has **Peças** (regions, pivots,
props) and **Animar** (timeline, gizmo keyframes, presets, grab/release, AI prompt, export) tabs.

## Characters

`3d.character` and `3d.character_rig` make a character usable in a game: a skinned humanoid with standard clips.

- **Animation library**: folders of GLB files with a skinned humanoid and animations - made for Quaternius'
  Universal Animation Library 1 and 2 (CC0, `Unreal-Godot/UAL*_Standard.glb`; `*_RM.glb` = root motion). Set the
  folders in Options > Animações 3D (`settings.json` → `3d.animation_libraries`), `FORGE_ANIM_LIBRARY` or
  `<data>/animations`. The first in-place library is also the T-pose mannequin shown to the image editor.
- **Retargeting** (`geometry/retarget.py`) works on world rotations: each library bone's rotation away from its
  bind pose drives the matching bone, after lining up the character's rest bone directions with the library's
  (A-posed characters play T-pose libraries). Hips translation scales with leg length. Mixamo names are mapped.
  Keyframes are reduced (0.4°) - a 13-clip set is ~0.5 MB of `clips.json`.
- **Skin weights**: bone heat on the welded surface (UV seams can't open), Laplacian-smoothed, and the 4-influence
  limit applied continuously (the first dropped weight is subtracted), so neighbouring vertices never jump apart.
  `rig.binding: volume` measures bone distance through the volume (long hair, skirts).
- **Deformation check** (`geometry/deform.py`): every clip is played with linear blend skinning; edges stretched
  past 2.5x count as tears (graded ok < 0.1% < warn < 0.5% < bad), with the bones around them.

```bash
uv run forge run 3d.character -p my-game -i bishop-concept --json -P 'extract=["staff"]' -P 'animations={"sets":["basic","combat"]}'
uv run forge run 3d.character_rig -p my-game -i knight-3d -P 'rig={"fingers":true}' -P 'animations={"sets":["basic"],"root_motion":true}'
uv run forge run 3d.retarget -p my-game -i knight-3d-rig -P 'animations={"names":["Sword_Regular_Combo","Shield_Dash"]}'
```

The web UI has a **Personagem** tab on images, meshes and rigged characters.

## Project settings (`project.yaml`)

```yaml
style:
  prompt: "stylized hand-painted, chunky proportions"   # appended to concept images
  palette: ["#264653", "#2a9d8f", "#e9c46a"]            # used by 3d.lowpoly
3d:
  units: m                 # units of generated / imported meshes
  default_height: 1.8      # generated meshes are scaled to this height (optional)
  poly_budget: 20000       # default target for simplify / generation
  lowpoly_faces: 800
  export_format: glb
  comfy_url: http://127.0.0.1:8189
  models: {hunyuan3d: hunyuan_3d_v2.1.safetensors}
  printer:
    name: Bambu A1
    build_volume_mm: [256, 256, 256]
    nozzle_mm: 0.4
    min_wall_mm: 0.8
    tolerance_mm: 0.2
    max_overhang_deg: 45
```

## Conventions

- Axes: glb/gltf/obj are **Y-up**, stl/3mf/ply are **Z-up** (CAD/printing). Pipelines convert automatically.
- Units: printing outputs are always millimeters; other outputs use `3d.units`.
- Roles: `primary` (main mesh), `print` (3MF), `part` (split parts / pin), `source` (raw mesh, concept/input image), `report` (JSON).

## ComfyUI

Generation uses native ComfyUI nodes only (Hunyuan3D 2.x, BiRefNet, Z-Image), so it runs on Comfy Desktop
(:8188) or on the dedicated instance (:8189, see `comfy/`). Hunyuan3D-Paint needs the dedicated instance (pinned to
the native-paint PR branch). Lookup order: `3d.comfy_url`, `FORGE_COMFY_URL_3D`,
`:8189`, `FORGE_COMFY_URL`. Models: `uv run python comfy/download_models.py --list`.

## Library

`forge_3d.geometry` has no dependency on forge_core and can be used directly:

```python
from forge_3d import geometry as g
mesh = g.load_mesh("model.glb")
out, report = g.print_optimize(mesh, g.PrinterProfile(build_volume_mm=(256, 256, 256)), source_units="m", target_height_mm=120)
res = g.split_for_print(out, build_volume_mm=(256, 256, 256))
```

Tests: `uv run pytest packages/forge_3d`.

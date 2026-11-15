# forge_3d — roadmap / backlog

## Done
- Text → 3D, Image → 3D (Hunyuan3D 2.1, native ComfyUI nodes), Poisson cleanup of generated meshes
- Simplify, Low Poly, Repair, Transform, Convert, Print Optimizer (auto-fit to printer), Part Splitter (dowel pins)
- Editing: `3d.edit` (transform, plane cut, primitive/text/mesh booleans, mirror/symmetrize), browser sculpting
  (inflate/deflate/smooth/flatten), `3d.ai_edit` (Qwen-Image-Edit on the source image + regeneration), AI part + attach
- Exporters: `3d.mesh` (formats/units/axis/pivot), `3d.print_kit`
- **Animation phase 1 (rigid parts)** — `3d.rig` (regions → parts, pivots, hierarchy, props, re-rig keeps clips),
  `3d.animate` (keyframes, presets, Claude-planned prompts with `@asset` mentions and missing-part detection),
  attachments (grab / carry / release, baked), `animated.glb` with glTF animations, `3d.animation_glb` and
  `3d.animation_media` (GIF/MP4/WebM) exporters; web **Peças** + **Animar** tabs (timeline, gizmo keyframes,
  presets, grab/release, AI prompt, export)

- **Texturing** — `3d.unwrap` (xatlas UV + baked colors), `3d.texture_ai` (source-image projection with
  silhouette-matched camera + Qwen-generated back/side views), `3d.apply_texture`; web **Pintar** tab
  (3D-space brush that crosses UV seams, bucket per part, eyedropper, project palette, undo)

- **Hunyuan3D-Paint 2.1** (`3d.texture_ai` `method: "hunyuan3d-paint"`, default in the Pintar tab): 6 consistent
  views generated for the mesh and baked into its UVs (~30-50 s). Sticker-style outlines/halos are stripped from
  the reference first (`texture.strip_outline`), otherwise the model paints the halo color everywhere.

- **Multi-image input** for Image → 3D (`side_views`, Hunyuan3D 2.0 multi-view)

- **Generated mesh finish**: GLBs export smooth normals split at hard edges (60°) — without normals every viewer
  and engine shades triangles flat; generation decimates with quality 0.6 and no planar quadric

- **Region refinement** (`3d.refine_region`, web **Detalhar** tab: brush / volumes / text) and **advanced generation**
  refiner (`3d.model`: server-side renders `geometry/render.py`, action catalog, critic guidance)

- **TRELLIS.2-4B generator** (own ComfyUI :8190, default when running): sharper shape, face detail and an optional
  texture in the same run; CuMesh only for the coarse decimation (corrupts low budgets on RTX 50)

- **PBR texture sets**: base color + metallic/roughness (TRELLIS.2 / Hunyuan3D-Paint maps, uniform otherwise) + a
  tangent-space normal map baked from the dense surface (`highres.glb`, kept with every generated model in its
  own space); glTF material + the 4 maps as images; painting keeps the other maps
- **Rework from raw** (`3d.from_raw`, "Ações" in the Malha panel) and the per-method generation options UI

- **Skeletons** (`3d.skeleton`, web **Esqueleto** tab, bone picking + walk cycle in **Animar**, Claude planner aware of
  bones, skinned GLB export)

## Next
- Skeletons: weight painting (fix capes/cloth bound to limbs), Mixamo/BVH animation import with retargeting,
  IK (feet on the ground, hands to targets), finger chains, better creature fitting (curled tails, extra torsos)
- TRELLIS.2: skin/albedo tones come out darker than the reference (try Hunyuan3D-Paint on TRELLIS shapes);
  higher budgets + normal map bake to keep the raw 470k-face detail
- Region refinement: carry colors/textures over (re-texture only the region), refine regions hidden in every image
  (generate the missing view with Qwen first), several regions in one run
- **Normal map bake** from the high-res surface onto the game mesh (UV unwrap + bake): keeps raw-level detail
  (seams, embroidery) at 20-40k triangles
- Use the real side views of an asset in `3d.texture_ai` (projection) instead of generating them with Qwen
- Hunyuan3D-Paint follow-ups: move back to an official ComfyUI release once Comfy-Org/ComfyUI#15020 merges
  (instance pinned to the PR branch); DINOv2 reference features (optional input, improves fidelity); apply the
  metallic-roughness map (saved as `paint_mr.png`, not yet wired into the material); `paint_views.png` keeps only
  the first view of the batch.
- Texture projection quality: multi-view generation with a dedicated model (consistent views), seam blending,
  delighting (remove baked lighting from the source image)
- Quad retopology for characters (Instant Meshes / QuadriFlow), UV unwrap (xatlas) + texture bake
- Hollowing for printing (SDF offset via manifold3d `level_set`), drain holes
- FBX export (Blender headless)

## Animation — next phases

> "Faça uma animação desse personagem andando"
> "Faça a animação desse rifle sendo carregado"
> "Faça esse personagem pegando o @rifle-exercito do chão e colocando nas costas"

Phase 1 covers objects/mechanisms (rifle reload, chest lid, doors, props being carried by a part).
Open items:

1. **Automatic part segmentation with AI** — today the planner *reports* which parts are missing
   ("magazine: the box under the grip") and the user carves them in the Peças tab. Next: Hunyuan3D-Part
   (P3-SAM) or a render-and-segment approach (SAM on multi-view renders → face labels) to propose regions
   automatically. Verify license, VRAM (16 GB), Windows + Blackwell.
2. **Characters (walk cycles etc.)** — skeleton + skinning (auto-rig: UniRig / Puppeteer), text-to-motion
   (HY-Motion, MoMask/MDM family) retargeted to the rig, skinned glTF export. Rigid parts are not enough for
   organic deformation; this is a separate pipeline (`3d.autorig`, `3d.motion`).
3. **Hand IK for attachments** — grab poses that reach the prop (needs skeletons from item 2).
4. **Server-side media rendering** for agents (today GIF/MP4 frames are rendered by the browser):
   headless GL (EGL/OSMesa) or Blender, or ComfyUI's 3D render nodes.
5. **Video shortcut for media only** — render a pose, animate with Wan 2.2 i2v (2D module:
   `forge_2d.graphs.wan_i2v`). GIF/MP4 quickly, not game-usable.

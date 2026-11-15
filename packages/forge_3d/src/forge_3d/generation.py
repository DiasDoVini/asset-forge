"""ComfyUI graphs and image prep for AI mesh generation (native Hunyuan3D 2.x nodes, no custom nodes).

Graph structure follows the official template ``3d_hunyuan3d-v2.1`` shipped with ComfyUI 0.28.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading

import numpy as np
from forge_2d.graphs import Graph
from forge_core import ComfyClient, ComfyError, Context
from PIL import Image

DEFAULT_MODELS = {
    "hunyuan3d": "hunyuan_3d_v2.1.safetensors",
    "hunyuan3d_mv": "hunyuan3d-dit-v2-mv_fp16.safetensors",
    "bg_removal": "birefnet.safetensors",
    "paint_unet": "hunyuan3d-paintpbr-v2-1.safetensors",
    "paint_vae": "hunyuan3d_paintpbr_v2_1_vae.safetensors",
}

DEDICATED_URL = "http://127.0.0.1:8189"
TRELLIS_URL = "http://127.0.0.1:8190"  # separate instance: TRELLIS.2 needs torch 2.10 (comfy/install_trellis.ps1)


def comfy_for(ctx: Context, configured: str | None = None) -> ComfyClient:
    """First reachable ComfyUI among: project 3d.comfy_url, FORGE_COMFY_URL_3D, dedicated instance, global default.

    Native Hunyuan3D nodes exist in any ComfyUI >= 0.28, so falling back to Comfy Desktop is fine.
    """
    candidates = [configured, os.environ.get("FORGE_COMFY_URL_3D"), DEDICATED_URL, ctx.comfy.url]
    tried = []
    for url in dict.fromkeys(u.rstrip("/") for u in candidates if u):
        client = ctx.comfy if url == ctx.comfy.url else ComfyClient(url, cancel_event=getattr(ctx, "_cancel", threading.Event()))
        if client.ping():
            return client
        tried.append(url)
    raise ComfyError(f"no ComfyUI reachable (tried {tried}). Start one with: powershell -File comfy\\start.ps1")


TRELLIS_MODELS = Path(__file__).resolve().parents[4] / "comfy" / "instance-trellis" / "ComfyUI" / "models"


def trellis_for(ctx: Context, configured: str | None = None) -> ComfyClient:
    """The TRELLIS.2 ComfyUI instance: project 3d.trellis_url, FORGE_COMFY_URL_TRELLIS, then :8190.

    For the local instance, the official DINOv3 weights must be in place: otherwise ComfyUI-Trellis2 would
    download an unofficial mirror of that gated model on its own."""
    local = not (configured or os.environ.get("FORGE_COMFY_URL_TRELLIS"))
    dinov3 = TRELLIS_MODELS / "facebook" / "dinov3-vitl16-pretrain-lvd1689m" / "model.safetensors"
    if local and TRELLIS_MODELS.parent.exists() and not dinov3.exists():
        raise ComfyError("TRELLIS.2 needs the DINOv3 weights (gated on HuggingFace): accept the license at "
                         "https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m, run `hf auth login`, then "
                         "comfy/download_trellis_models.py with the TRELLIS instance's Python")
    for url in dict.fromkeys(u.rstrip("/") for u in (configured, os.environ.get("FORGE_COMFY_URL_TRELLIS"), TRELLIS_URL) if u):
        client = ComfyClient(url, timeout=1800, cancel_event=getattr(ctx, "_cancel", threading.Event()))
        if client.ping():
            return client
    raise ComfyError("the TRELLIS.2 ComfyUI is not running. Install/start it with: powershell -File comfy/install_trellis.ps1 "
                     "then comfy/start_trellis.ps1 (port 8190)")


def trellis_image_to_mesh(image_name: str, seed: int, *, resolution: int = 1024, texture: bool = False,
                          texture_size: int = 2048, max_faces: int = 500_000, steps: int = 12,
                          guidance: float = 7.5, prefix: str = "forge/3d/trellis") -> tuple[Graph, dict[str, str]]:
    """TRELLIS.2-4B (ComfyUI-Trellis2 nodes): RGBA image -> mesh, optionally textured (PBR baked to a texture).

    ``resolution``: 512 (fast), 1024, 1536 (cascade from 512 -> 1024/1536, the high-quality path).
    Returns (graph, {"mesh": save node[, "textured": save node]}). The meshes are Y-up GLBs facing +Z.
    """
    g = Graph()
    pipe = g("Trellis2LoadModel", modelname="microsoft/TRELLIS.2-4B", backend="sdpa", device="cuda", low_vram=True,
             keep_models_loaded=False, conv_backend="flex_gemm", sparse_backend="xformers", use_reconviagen=False,
             pixal3d_multiview=False)
    img = g("Trellis2LoadImageWithTransparency", image=image_name)
    pre = g("Trellis2PreProcessImage", image=img[2], padding=10, remove_background=False, max_size=1024)
    cond = g("Trellis2ImageCondGenerator", pipeline=pipe, image=pre, max_views=1)
    sampling = dict(verbose=False, dino_substeps=4, dino_foundation_cap=1.0)
    sparse = g("Trellis2SparseGenerator", pipeline=cond[2], image_cond=cond[0], seed=seed, sparse_structure_steps=steps,
               sparse_structure_guidance_strength=guidance, sparse_structure_guidance_rescale=0.01,
               sparse_structure_rescale_t=5.0, sparse_structure_sampler="heun",
               sparse_structure_resolution=64 if resolution > 512 else 32, sparse_structure_guidance_interval_start=0.1,
               sparse_structure_guidance_interval_end=1.0, fill_holes=True, hole_iterations=1, dino_lock=1.0,
               hole_fill_algorithm="flood_fill", keep_only_shell=True, **{**sampling, "dino_substeps": 8})
    shape_kw = dict(shape_steps=steps, shape_guidance_strength=guidance, shape_guidance_rescale=0.01, shape_rescale_t=3.0,
                    shape_sampler="heun", shape_guidance_interval_start=0.1, shape_guidance_interval_end=1.0,
                    dino_lock=0.0, **sampling)
    first = 512 if resolution != 1024 else 1024
    shape = g("Trellis2ShapeGenerator", pipeline=sparse[2], image_cond=cond[1] if first == 1024 else cond[0],
              coords=sparse[0], resolution=first, **shape_kw)
    slat, res, last = shape[0], shape[1], shape[2]
    if resolution != first:
        casc = g("Trellis2ShapeCascadeGenerator", pipeline=shape[2], image_cond=cond[1], shape_slat=shape[0],
                 from_resolution=shape[1], to_resolution=resolution, sparse_structure_resolution=sparse[1],
                 max_num_tokens=999999, **shape_kw)
        slat, res, last = casc[0], casc[1], casc[2]
    dec = g("Trellis2DecodeLatents", pipeline=last, shape_slat=slat, resolution=res, use_tiled_decoder=True)
    mesh = g("Trellis2FillHolesWithCuMesh", mesh=dec[0], max_permieters=1.0)
    mesh = g("Trellis2ReconstructMeshWithQuad", mesh=mesh, remesh_band=1.0, resolution=1024, remove_floaters=True,
             remove_inner_faces=True)
    # CuMesh decimation is fine down to ~500k faces but corrupts low budgets on RTX 50 (Blackwell): long
    # garbage triangles. Coarse pass with CuMesh, the final one (the asset's budget) with Meshlib.
    mesh = g("Trellis2SimplifyMesh", mesh=mesh, target_face_num=max(max_faces, 500_000), method="Cumesh")
    dense = g("Trellis2FillHolesNicelyWithMeshlib", mesh=mesh)
    mesh = g("Trellis2SimplifyMesh", mesh=dense[0], target_face_num=max_faces, method="Meshlib")
    tri = g("Trellis2MeshWithVoxelToTrimesh", mesh=mesh, reorient_vertices="90 degrees")
    saves = {"mesh": g("Trellis2ExportMesh", trimesh=tri, filename_prefix=prefix, file_format="glb").id}
    if texture and max_faces < 500_000:  # the dense surface, for the normal map (the textured mesh is low-poly)
        dense_tri = g("Trellis2MeshWithVoxelToTrimesh", mesh=dense[0], reorient_vertices="90 degrees")
        saves["dense"] = g("Trellis2ExportMesh", trimesh=dense_tri, filename_prefix=prefix + "_dense", file_format="glb").id
    if texture:
        tex = g("Trellis2MeshTexturing", pipeline=dec[2], image=pre, trimesh=tri, seed=seed, texture_steps=12,
                texture_guidance_strength=5.0, texture_guidance_rescale=0.05, texture_rescale_t=3.0, resolution=1024,
                texture_size=texture_size, texture_alpha_mode="OPAQUE", double_side_material=False,
                texture_guidance_interval_start=0.0, texture_guidance_interval_end=0.99, max_views=4,
                bake_on_vertices=False, use_custom_normals=False, mesh_cluster_threshold_cone_half_angle_rad=60.0,
                sampler="heun", inpainting="telea", verbose=False, dino_lock=0.0, dino_substeps=4,
                dino_foundation_cap=1.0)
        saves["textured"] = g("Trellis2ExportMesh", trimesh=tex[0], filename_prefix=prefix + "_tex", file_format="glb").id
    return g, saves


def hunyuan_image_to_mesh(image_name: str, seed: int, *, models: dict[str, str], steps: int = 30, cfg: float = 5.0,
                          latent_resolution: int = 4096, octree_resolution: int = 256, threshold: float = 0.6,
                          algorithm: str = "surface net", prefix: str = "forge/3d/mesh") -> tuple[Graph, str]:
    """Single image -> untextured mesh (GLB). Returns (graph, save node id)."""
    g = Graph()
    ckpt = g("ImageOnlyCheckpointLoader", ckpt_name=models["hunyuan3d"])
    model = g("ModelSamplingAuraFlow", model=ckpt[0], shift=1.0)
    img = g("LoadImage", image=image_name)
    vision = g("CLIPVisionEncode", clip_vision=ckpt[1], image=img, crop="center")
    cond = g("Hunyuan3Dv2Conditioning", clip_vision_output=vision)
    latent = g("EmptyLatentHunyuan3Dv2", resolution=latent_resolution, batch_size=1)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg, sampler_name="euler", scheduler="normal",
                positive=cond[0], negative=cond[1], latent_image=latent, denoise=1.0)
    voxel = g("VAEDecodeHunyuan3D", samples=samples, vae=ckpt[2], num_chunks=8000, octree_resolution=octree_resolution)
    mesh = g("VoxelToMesh", voxel=voxel, algorithm=algorithm, threshold=threshold)
    save = g("SaveGLB", mesh=mesh, filename_prefix=prefix)
    return g, save.id


def hunyuan_multiview_to_mesh(views: dict[str, str], seed: int, *, models: dict[str, str], steps: int = 30,
                              cfg: float = 5.0, latent_resolution: int = 3072, octree_resolution: int = 256,
                              threshold: float = 0.6, prefix: str = "forge/3d/mesh") -> tuple[Graph, str]:
    """front/left/back/right images (any subset, front recommended) -> mesh, Hunyuan3D 2.0-mv."""
    g = Graph()
    ckpt = g("ImageOnlyCheckpointLoader", ckpt_name=models["hunyuan3d_mv"])
    model = g("ModelSamplingAuraFlow", model=ckpt[0], shift=1.0)
    encoded = {}
    for side, name in views.items():
        img = g("LoadImage", image=name)
        encoded[side] = g("CLIPVisionEncode", clip_vision=ckpt[1], image=img, crop="center")
    cond = g("Hunyuan3Dv2ConditioningMultiView", **encoded)
    latent = g("EmptyLatentHunyuan3Dv2", resolution=latent_resolution, batch_size=1)
    samples = g("KSampler", model=model, seed=seed, steps=steps, cfg=cfg, sampler_name="euler", scheduler="normal",
                positive=cond[0], negative=cond[1], latent_image=latent, denoise=1.0)
    voxel = g("VAEDecodeHunyuan3D", samples=samples, vae=ckpt[2], num_chunks=8000, octree_resolution=octree_resolution)
    mesh = g("VoxelToMesh", voxel=voxel, algorithm="surface net", threshold=threshold)
    save = g("SaveGLB", mesh=mesh, filename_prefix=prefix)
    return g, save.id


def hunyuan_paint(mesh_name: str, image_name: str, seed: int, *, models: dict[str, str], steps: int = 20,
                  cfg: float = 6.0, num_views: int = 6, view_resolution: int = 512, texture_size: int = 2048,
                  prefix: str = "forge/3d/paint") -> tuple[Graph, dict[str, str]]:
    """Hunyuan3D 2.1 PBR paint (native ComfyUI nodes, PR #15020): mesh + reference image -> baked textures.

    The mesh is baked onto its own UVs (unwrap it first). Returns (graph, {"albedo": id, "mr": id, "views": id}).
    """
    g = Graph()
    mesh = g("Get3DComponents", model_3d=g("ForgeLoad3DFile", path=mesh_name))
    model = g("Hunyuan3DPaintModelLoader", model_name=models["paint_unet"])
    vae = g("VAELoader", vae_name=models["paint_vae"])
    ref = g("LoadImage", image=image_name)
    cond = g("Hunyuan3DPaintConditioning", model=model, vae=vae, mesh=mesh, reference_image=ref,
             num_views=num_views, resolution=view_resolution)
    samples = g("KSampler", model=cond[0], seed=seed, steps=steps, cfg=cfg, sampler_name="euler",
                scheduler="sgm_uniform", positive=cond[1], negative=cond[2], latent_image=cond[3], denoise=1.0)
    split = g("Hunyuan3DPaintSplitLatent", model=cond[0], samples=samples)
    albedo = g("VAEDecode", samples=split[0], vae=vae)
    mr = g("VAEDecode", samples=split[1], vae=vae)
    bake = g("Hunyuan3DBakeMultiView", mesh=mesh, albedo=albedo, mr=mr, cameras=cond[4], texture_size=texture_size,
             bake_exponent=4.0, fill_holes=True)
    out = {
        "albedo": g("SaveImage", images=bake[1], filename_prefix=f"{prefix}_albedo").id,
        "mr": g("SaveImage", images=bake[2], filename_prefix=f"{prefix}_mr").id,
        "views": g("SaveImage", images=bake[3], filename_prefix=f"{prefix}_views").id,
    }
    return g, out


def prepare_subject(img: Image.Image, mask: Image.Image | None = None, size: int = 1024, margin: float = 0.12,
                    keep_alpha: bool = False) -> Image.Image:
    """Crop to the subject, center it on a white square with a margin — what image-to-3D models expect.

    Uses ``mask`` (white = subject) or the image's own alpha; with neither, the whole image is the subject.
    """
    rgba = img.convert("RGBA")
    if mask is not None:
        rgba.putalpha(mask.convert("L").resize(rgba.size))
    alpha = np.asarray(rgba.getchannel("A"))
    ys, xs = np.nonzero(alpha > 16)
    if len(xs):
        rgba = rgba.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    side = int(max(rgba.size) / (1 - 2 * margin))
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0 if keep_alpha else 255))
    canvas.alpha_composite(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
    canvas = canvas.resize((size, size), Image.LANCZOS)
    return canvas if keep_alpha else canvas.convert("RGB")


def _cutout(img: Image.Image, mask: Image.Image | None) -> Image.Image:
    rgba = img.convert("RGBA")
    if mask is not None:
        rgba.putalpha(mask.convert("L").resize(rgba.size))
    ys, xs = np.nonzero(np.asarray(rgba.getchannel("A")) > 16)
    return rgba.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)) if len(xs) else rgba


def prepare_views(views: dict[str, tuple[Image.Image, Image.Image | None]], size: int = 1024,
                  margin: float = 0.12) -> dict[str, Image.Image]:
    """Like ``prepare_subject`` for several views of one subject (front/left/back/right): every view gets the
    same subject height in pixels, so the multi-view model sees one consistent object instead of each view
    zoomed to fit on its own."""
    crops = {side: _cutout(img, mask) for side, (img, mask) in views.items()}
    avail = size * (1 - 2 * margin)
    height = min([avail] + [avail * c.height / c.width for c in crops.values()])
    out = {}
    for side, c in crops.items():
        w, h = max(1, round(c.width * height / c.height)), max(1, round(height))
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        canvas.alpha_composite(c.resize((w, h), Image.LANCZOS), ((size - w) // 2, (size - h) // 2))
        out[side] = canvas.convert("RGB")
    return out


def has_alpha(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA", "PA"):
        return False
    return bool(np.asarray(img.getchannel("A")).min() < 250)

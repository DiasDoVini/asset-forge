import { Box } from "lucide-react"

import { registerModality } from "@/core/registry"

import { AnimationEditor } from "./anim/AnimationEditor"
import { AnimationViewer } from "./anim/AnimationViewer"
import { CharacterPanel } from "./anim/CharacterPanel"
import { RigEditor } from "./anim/RigEditor"
import { SkeletonEditor } from "./anim/SkeletonEditor"
import { MeshEditor } from "./editor/MeshEditor"
import { MeshInfoPanel } from "./MeshInfoPanel"
import { MeshThumb } from "./MeshThumb"
import { MeshViewer } from "./MeshViewer"
import { PaintEditor } from "./paint/PaintEditor"
import { RefineEditor } from "./refine/RefineEditor"
import { GeneratorField } from "./GeneratorField"
import { SideViewsField } from "./SideViewsField"
import { isMeshFile } from "./mesh"

const hasMesh = (a: { files: { path: string; mime?: string | null }[] }) => a.files.some((f) => isMeshFile(f))

registerModality({
  name: "3d",
  title: "3D",
  icon: Box,
  viewers: [
    { id: "mesh", match: isMeshFile, component: MeshViewer, priority: 10 },
    // the animation asset's primary file: plays its clips
    { id: "animation", match: (f) => f.path.endsWith("animated.glb"), component: AnimationViewer, priority: 20 },
  ],
  thumbnail: MeshThumb,
  fields: {
    generator: { render: (ctx) => <GeneratorField {...ctx} />, placement: "input", hides: ["trellis", "hunyuan"] },
    side_views: { render: (ctx) => <SideViewsField {...ctx} />, placement: "input" },
  },
  panels: [
    {
      id: "mesh-edit",
      title: "Editar",
      placement: "tab",
      component: MeshEditor,
      when: (a) => hasMesh(a) && a.kind !== "animation",
    },
    {
      id: "anim-edit",
      title: "Animar",
      placement: "tab",
      component: AnimationEditor,
      when: (a) => a.kind === "animation" && a.modality === "3d",
    },
    {
      id: "refine",
      title: "Detalhar",
      placement: "tab",
      component: RefineEditor,
      when: (a) => hasMesh(a) && a.kind === "mesh" && a.modality === "3d",
    },
    {
      id: "paint",
      title: "Pintar",
      placement: "tab",
      component: PaintEditor,
      when: (a) => a.modality === "3d" && a.kind !== "print" && a.files.some((f) => f.role === "primary" && /\.glb$/i.test(f.path)),
    },
    {
      id: "skeleton",
      title: "Esqueleto",
      placement: "tab",
      component: SkeletonEditor,
      when: (a) => a.modality === "3d" && (a.kind === "mesh" ? hasMesh(a) : a.kind === "animation" && (a.extra?.animation as { type?: string } | undefined)?.type === "skeleton"),
    },
    {
      // concept (2D image or mesh) -> animatable character; rigged character -> more library animations
      id: "character",
      title: "Personagem",
      placement: "tab",
      component: CharacterPanel,
      when: (a) => (a.modality === "3d" && (a.kind === "mesh" ? hasMesh(a) : a.kind === "animation" && (a.extra?.animation as { type?: string } | undefined)?.type === "skeleton"))
        || (a.modality === "2d" && a.kind === "image" && a.files.some((f) => f.role === "primary" && /\.(png|jpe?g|webp)$/i.test(f.path))),
    },
    {
      id: "rig",
      title: "Peças",
      placement: "tab",
      component: RigEditor,
      when: (a) => hasMesh(a) && a.kind !== "print" && a.modality === "3d",
    },
    {
      id: "mesh-info",
      title: "Malha",
      placement: "inspector",
      component: MeshInfoPanel,
      when: (a) => hasMesh(a),
    },
  ],
})

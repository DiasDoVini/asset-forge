import * as THREE from "three"

// Editor-side operations. Everything lives in the viewer's Y-up world space, which is exactly the
// space 3d.edit applies its operations in, so what you see is what the server computes.

export type Vec3 = [number, number, number]
export type Quat = [number, number, number, number]
export type BoolMode = "add" | "subtract" | "intersect"
export type Shape = "box" | "cylinder" | "sphere" | "cone"

interface Placed {
  id: string
  position: Vec3
  quaternion: Quat
}

export interface CutTool extends Placed {
  kind: "cut"
  keep: "back" | "front"
}

export interface PrimitiveTool extends Placed {
  kind: "primitive"
  shape: Shape
  mode: BoolMode
  scale: Vec3 // = primitive size
}

export interface TextTool extends Placed {
  kind: "text"
  text: string
  mode: BoolMode
  height: number
  depth: number
}

export interface MirrorTool {
  id: string
  kind: "mirror"
  axis: "x" | "y" | "z"
  origin: number
  mode: "mirror" | "symmetrize_positive" | "symmetrize_negative"
}

export interface MeshTool extends Placed {
  kind: "mesh"
  asset: string // asset ref
  url: string // file url for the preview
  mode: BoolMode
  scale: Vec3
}

export type Tool = CutTool | PrimitiveTool | TextTool | MirrorTool | MeshTool
export type PlacedTool = Exclude<Tool, MirrorTool>

export const isPlaced = (t: Tool): t is PlacedTool => t.kind !== "mirror"

let counter = 0
export const newId = () => `op${Date.now().toString(36)}${(counter++).toString(36)}`

const IDENTITY_Q: Quat = [0, 0, 0, 1]

export function defaultTool(kind: Tool["kind"], center: THREE.Vector3, size: number): Tool {
  const p: Vec3 = [center.x, center.y, center.z]
  const s = Math.max(size, 1e-6)
  switch (kind) {
    case "cut":
      return { id: newId(), kind, position: p, quaternion: IDENTITY_Q, keep: "back" }
    case "primitive":
      return { id: newId(), kind, position: p, quaternion: IDENTITY_Q, shape: "cylinder", mode: "subtract", scale: [s * 0.2, s * 1.2, s * 0.2] }
    case "text":
      return { id: newId(), kind, position: p, quaternion: IDENTITY_Q, text: "TEXTO", mode: "add", height: s * 0.12, depth: s * 0.03 }
    case "mirror":
      return { id: newId(), kind, axis: "x", origin: center.x, mode: "symmetrize_positive" }
    case "mesh":
      throw new Error("mesh tools are created from an asset")
  }
}

function rowMajor(m: THREE.Matrix4): number[][] {
  const e = m.clone().transpose().elements // three stores column-major
  return [0, 1, 2, 3].map((r) => [e[r * 4], e[r * 4 + 1], e[r * 4 + 2], e[r * 4 + 3]])
}

function compose(position: Vec3, quaternion: Quat, scale: Vec3 = [1, 1, 1]): THREE.Matrix4 {
  return new THREE.Matrix4().compose(new THREE.Vector3(...position), new THREE.Quaternion(...quaternion), new THREE.Vector3(...scale))
}

/** Plane normal of a cut tool: the tool's local +Y. */
export function cutNormal(t: CutTool): THREE.Vector3 {
  return new THREE.Vector3(0, 1, 0).applyQuaternion(new THREE.Quaternion(...t.quaternion)).normalize()
}

/** World-space plane that keeps (positive side, three.js clipping convention) what the cut keeps. */
export function cutClipPlane(t: CutTool): THREE.Plane {
  const n = cutNormal(t)
  if (t.keep === "back") n.negate()
  return new THREE.Plane().setFromNormalAndCoplanarPoint(n, new THREE.Vector3(...t.position))
}

/** Serialize for the 3d.edit pipeline: model transform first, then the tools in order. */
export function toEditOps(model: THREE.Matrix4, tools: Tool[]): Record<string, unknown>[] {
  const ops: Record<string, unknown>[] = []
  if (!model.equals(new THREE.Matrix4())) ops.push({ op: "transform", matrix: rowMajor(model) })
  for (const t of tools) {
    switch (t.kind) {
      case "cut":
        ops.push({ op: "cut", point: t.position, normal: cutNormal(t).toArray(), keep: t.keep, cap: true })
        break
      case "primitive":
        ops.push({ op: "primitive", shape: t.shape, mode: t.mode, size: t.scale.map(Math.abs), matrix: rowMajor(compose(t.position, t.quaternion)) })
        break
      case "text":
        ops.push({ op: "text", text: t.text, mode: t.mode, height: t.height, depth: t.depth, matrix: rowMajor(compose(t.position, t.quaternion)) })
        break
      case "mirror":
        ops.push({ op: "mirror", axis: t.axis, origin: t.origin, mode: t.mode })
        break
      case "mesh":
        ops.push({ op: "mesh", asset: t.asset, mode: t.mode, matrix: rowMajor(compose(t.position, t.quaternion, t.scale)) })
        break
    }
  }
  return ops
}

export const MODE_COLOR: Record<BoolMode, string> = { add: "#22c55e", subtract: "#ef4444", intersect: "#eab308" }

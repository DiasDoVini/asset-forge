import * as THREE from "three"

// Mirrors forge_3d.geometry.anim: keyframes are offsets from each part's rest pose, in the parent's
// frame; rotation is intrinsic XYZ Euler in degrees (three.js order). Part nodes in animated.glb are
// named after the parts and sit on their pivots, so animating them rotates around the pivot.

export type V3 = [number, number, number]

export interface Key {
  t: number
  position: V3
  rotation_deg: V3
  scale: V3
}

export interface Track {
  part: string
  keys: Key[]
}

export interface Attach {
  part: string
  to: string
  start: number
  end: number | null
}

export interface Clip {
  name: string
  duration: number
  loop: boolean
  tracks: Track[]
  attach: Attach[]
}

export interface PartInfo {
  name: string
  parent: string | null
  pivot: V3
  source: string
  faces?: number
  bone?: boolean
  deform?: boolean
}

export interface AnimationInfo {
  type?: "parts" | "skeleton"
  template?: string | null
  parts: PartInfo[]
  clips: { name: string; duration: number; loop: boolean }[]
  units: string
}

/** A looping walk cycle for a Mixamo-style humanoid skeleton (legs, knees, arm swing, hip bob, torso twist).
 *  Models face +Z; a negative X rotation swings a limb hanging down forward. */
export function walkCycle(duration = 1.0, stride = 1, names: Set<string> = new Set()): Track[] {
  const steps = 8
  const has = (n: string) => !names.size || names.has(n)
  const track = (part: string, fn: (ph: number) => Partial<Pick<Key, "position" | "rotation_deg">>): Track => ({
    part,
    keys: Array.from({ length: steps + 1 }, (_, i) => {
      const ph = (i / steps) * Math.PI * 2
      const v = fn(ph)
      return { t: +((i / steps) * duration).toFixed(4), position: v.position ?? ZERO, rotation_deg: v.rotation_deg ?? ZERO, scale: ONE }
    }),
  })
  const s = stride
  const tracks: Track[] = [
    track("LeftUpLeg", (ph) => ({ rotation_deg: [-28 * s * Math.sin(ph), 0, 0] })),
    track("RightUpLeg", (ph) => ({ rotation_deg: [28 * s * Math.sin(ph), 0, 0] })),
    // the knee bends while its leg swings forward (phase ahead of the hip)
    track("LeftLeg", (ph) => ({ rotation_deg: [40 * s * Math.max(0, Math.sin(ph + Math.PI / 2)), 0, 0] })),
    track("RightLeg", (ph) => ({ rotation_deg: [40 * s * Math.max(0, Math.sin(ph + Math.PI * 1.5)), 0, 0] })),
    track("LeftFoot", (ph) => ({ rotation_deg: [10 * s * Math.sin(ph + Math.PI / 4), 0, 0] })),
    track("RightFoot", (ph) => ({ rotation_deg: [-10 * s * Math.sin(ph + Math.PI / 4), 0, 0] })),
    // arms swing against the legs
    track("LeftArm", (ph) => ({ rotation_deg: [22 * s * Math.sin(ph), 0, 0] })),
    track("RightArm", (ph) => ({ rotation_deg: [-22 * s * Math.sin(ph), 0, 0] })),
    track("LeftForeArm", (ph) => ({ rotation_deg: [-12 * s * (1 + Math.sin(ph)), 0, 0] })),
    track("RightForeArm", (ph) => ({ rotation_deg: [-12 * s * (1 - Math.sin(ph)), 0, 0] })),
    track("Hips", (ph) => ({ position: [0, -0.012 * s * Math.cos(2 * ph), 0], rotation_deg: [0, 6 * s * Math.sin(ph), 0] })),
    track("Spine1", (ph) => ({ rotation_deg: [0, -8 * s * Math.sin(ph), 0] })),
  ]
  return tracks.filter((t) => has(t.part))
}

export const ZERO: V3 = [0, 0, 0]
export const ONE: V3 = [1, 1, 1]

export function emptyClip(name: string, duration = 2, loop = false): Clip {
  return { name, duration, loop, tracks: [], attach: [] }
}

/** Rest translation of every part node, read from the freshly loaded scene. */
export function captureRest(scene: THREE.Object3D, parts: PartInfo[]): Record<string, THREE.Vector3> {
  const rest: Record<string, THREE.Vector3> = {}
  for (const p of parts) {
    const node = scene.getObjectByName(p.name)
    if (node) rest[p.name] = node.position.clone()
  }
  return rest
}

/** Key from a node's current pose (offset from rest). */
export function keyFromNode(node: THREE.Object3D, rest: THREE.Vector3, t: number): Key {
  const e = new THREE.Euler().setFromQuaternion(node.quaternion, "XYZ")
  const r = (v: number) => Math.round(v * 1e4) / 1e4
  return {
    t: Math.round(t * 100) / 100,
    position: node.position.clone().sub(rest).toArray().map(r) as V3,
    rotation_deg: [e.x, e.y, e.z].map((v) => r(THREE.MathUtils.radToDeg(v))) as V3,
    scale: node.scale.toArray().map(r) as V3,
  }
}

export function upsertKey(clip: Clip, part: string, key: Key): Clip {
  const tracks = clip.tracks.some((tr) => tr.part === part) ? clip.tracks : [...clip.tracks, { part, keys: [] }]
  return {
    ...clip,
    tracks: tracks.map((tr) =>
      tr.part !== part ? tr : { ...tr, keys: [...tr.keys.filter((k) => Math.abs(k.t - key.t) > 1e-3), key].sort((a, b) => a.t - b.t) },
    ),
  }
}

export function removeKey(clip: Clip, part: string, t: number): Clip {
  return {
    ...clip,
    tracks: clip.tracks
      .map((tr) => (tr.part !== part ? tr : { ...tr, keys: tr.keys.filter((k) => Math.abs(k.t - t) > 1e-3) }))
      .filter((tr) => tr.keys.length > 0),
  }
}

/** Client-side preview clip (attachments are baked by the server on save, not previewed here). */
export function toThreeClip(clip: Clip, rest: Record<string, THREE.Vector3>): THREE.AnimationClip {
  const tracks: THREE.KeyframeTrack[] = []
  for (const tr of clip.tracks) {
    const base = rest[tr.part]
    if (!base || !tr.keys.length) continue
    const keys = [...tr.keys].sort((a, b) => a.t - b.t)
    const times = keys.map((k) => k.t)
    tracks.push(new THREE.VectorKeyframeTrack(`${tr.part}.position`, times, keys.flatMap((k) => [base.x + k.position[0], base.y + k.position[1], base.z + k.position[2]])))
    const q = new THREE.Quaternion()
    const prev = new THREE.Quaternion()
    const quats: number[] = []
    keys.forEach((k, i) => {
      q.setFromEuler(new THREE.Euler(...k.rotation_deg.map(THREE.MathUtils.degToRad) as V3, "XYZ"))
      if (i > 0 && q.dot(prev) < 0) q.set(-q.x, -q.y, -q.z, -q.w) // shortest path, like the exporter
      prev.copy(q)
      quats.push(q.x, q.y, q.z, q.w)
    })
    tracks.push(new THREE.QuaternionKeyframeTrack(`${tr.part}.quaternion`, times, quats))
    tracks.push(new THREE.VectorKeyframeTrack(`${tr.part}.scale`, times, keys.flatMap((k) => k.scale)))
  }
  return new THREE.AnimationClip(clip.name, clip.duration, tracks)
}

export const PRESETS = [
  { id: "hinge", label: "Dobradiça (abrir/fechar)" },
  { id: "slide", label: "Deslizar e voltar" },
  { id: "spin", label: "Girar" },
  { id: "bob", label: "Flutuar" },
  { id: "recoil", label: "Recuo" },
  { id: "shake", label: "Tremer" },
  { id: "pulse", label: "Pulsar" },
] as const

export type PresetId = (typeof PRESETS)[number]["id"]

/** Same motions as forge_3d.geometry.anim.preset_keys (keep in sync). */
export function presetKeys(name: PresetId, duration: number, axis: "x" | "y" | "z", amount: number, distance: number): Key[] {
  const i = "xyz".indexOf(axis)
  const rot = (deg: number): V3 => { const r: V3 = [0, 0, 0]; r[i] = deg; return r }
  const off = (d: number): V3 => { const p: V3 = [0, 0, 0]; p[i] = d; return p }
  const k = (t: number, position: V3 = ZERO, rotation_deg: V3 = ZERO, scale: V3 = ONE): Key => ({ t: Math.round(t * 1000) / 1000, position, rotation_deg, scale })
  const d = duration
  switch (name) {
    case "spin": {
      const turns = amount > 4 ? amount / 360 : amount
      const steps = Math.max(4, Math.floor(Math.abs(turns) * 4))
      return Array.from({ length: steps + 1 }, (_, s) => k((d * s) / steps, ZERO, rot((360 * turns * s) / steps)))
    }
    case "hinge":
      return [k(0), k(d * 0.4, ZERO, rot(amount)), k(d * 0.6, ZERO, rot(amount)), k(d)]
    case "slide":
      return [k(0), k(d * 0.4, off(distance)), k(d * 0.6, off(distance)), k(d)]
    case "bob":
      return [k(0), k(d * 0.5, off(distance)), k(d)]
    case "recoil":
      return [k(0), k(d * 0.08, off(-distance), rot(amount * 0.1)), k(d * 0.5), k(d)]
    case "shake":
      return Array.from({ length: 9 }, (_, s) => k((d * s) / 8, off(s > 0 && s < 8 ? distance * (s % 2 ? 1 : -1) : 0)))
    case "pulse": {
      const s = 1 + amount / 100
      return [k(0), k(d * 0.5, ZERO, ZERO, [s, s, s]), k(d)]
    }
  }
}

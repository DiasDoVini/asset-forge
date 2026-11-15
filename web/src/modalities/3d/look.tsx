// How the viewer shows a model: preview color/material, lighting preset with real shadows, and exposure.
// Pure presentation - nothing here changes the asset. The choice is remembered per browser.
import { useThree } from "@react-three/fiber"
import { useEffect, useMemo, useState } from "react"
import * as THREE from "three"

import { StudioEnvironment } from "./environment"
import type { MeshStats } from "./mesh"

/** "auto" keeps textures/vertex colors and gives plain white models a clay tone; "original" shows the file as is. */
export type LookColor = "auto" | "original" | "normals" | `#${string}`
export type LookLight = "studio" | "dramatic" | "flat"
export interface Look {
  color: LookColor
  light: LookLight
  shadows: boolean
  exposure: number
}

export const DEFAULT_LOOK: Look = { color: "auto", light: "studio", shadows: true, exposure: 1 }
export const CLAY = "#9a9389"
export const SWATCHES: { color: LookColor; label: string }[] = [
  { color: "auto", label: "Automático (texturas; argila nos modelos sem cor)" },
  { color: "original", label: "Original do arquivo" },
  { color: CLAY, label: "Argila" },
  { color: "#c9b48f", label: "Areia" },
  { color: "#7d838c", label: "Cinza" },
  { color: "#4a4f57", label: "Grafite" },
  { color: "normals", label: "Normais (cores por direção)" },
]
export const LIGHTS: { id: LookLight; label: string }[] = [
  { id: "studio", label: "Estúdio" },
  { id: "dramatic", label: "Dramática" },
  { id: "flat", label: "Plana" },
]

const KEY = "forge3d.viewer.look"

export function useLook(): [Look, (patch: Partial<Look>) => void] {
  const [look, setLook] = useState<Look>(() => {
    try {
      return { ...DEFAULT_LOOK, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") }
    } catch {
      return DEFAULT_LOOK
    }
  })
  const update = (patch: Partial<Look>) =>
    setLook((prev) => {
      const next = { ...prev, ...patch }
      try {
        localStorage.setItem(KEY, JSON.stringify(next))
      } catch {
        /* storage unavailable: keep it for this session only */
      }
      return next
    })
  return [look, update]
}

type Mat = THREE.Material | THREE.Material[]
const ORIGINAL = "forgeOriginalMaterial"

function isPlain(m: THREE.Material): boolean {
  const s = m as THREE.MeshStandardMaterial
  return !s.map && !s.vertexColors && (!s.color || s.color.getHSL({ h: 0, s: 0, l: 0 }).l > 0.85)
}

/** Apply the color mode (and wireframe/shadow flags) to every mesh; the file's own materials are kept aside. */
export function applyLook(obj: THREE.Object3D, look: Look, wireframe: boolean): () => void {
  const made: THREE.Material[] = []
  const clay = (color: string, from?: THREE.Material) => {
    const src = from as THREE.MeshStandardMaterial | undefined
    const m = new THREE.MeshStandardMaterial({
      color, roughness: 0.62, metalness: 0, side: src?.side ?? THREE.FrontSide,
      normalMap: src?.normalMap ?? null, // keep baked surface detail under the preview color
    })
    made.push(m)
    return m
  }
  obj.traverse((o) => {
    if (!(o instanceof THREE.Mesh)) return
    const original: Mat = (o.userData[ORIGINAL] ??= o.material)
    const list = [original].flat()
    let next: THREE.Material[]
    if (look.color === "original") next = list
    else if (look.color === "auto") next = list.map((m) => (isPlain(m) ? clay(CLAY, m) : m))
    else if (look.color === "normals") {
      next = list.map(() => {
        const m = new THREE.MeshNormalMaterial()
        made.push(m)
        return m
      })
    } else next = list.map((m) => clay(look.color, m))
    for (const m of next) (m as THREE.MeshStandardMaterial).wireframe = wireframe
    o.material = Array.isArray(original) ? next : next[0]
    o.castShadow = look.shadows && look.light !== "flat"
    o.receiveShadow = o.castShadow
  })
  return () => made.forEach((m) => m.dispose())
}

/** Lights for a preset, sized to the model: a shadow-casting key light, fill/rim, and a ground shadow catcher. */
export function ViewerLights({ look, stats }: { look: Look; stats: MeshStats | null }) {
  const gl = useThree((s) => s.gl)
  useEffect(() => {
    gl.toneMappingExposure = look.exposure
  }, [gl, look.exposure])

  const target = useMemo(() => new THREE.Object3D(), [])
  const center = stats ? stats.min.clone().addScaledVector(stats.size, 0.5) : new THREE.Vector3()
  const radius = stats ? Math.max(stats.size.length() / 2, 1e-4) : 1
  const at = (x: number, y: number, z: number) => center.clone().add(new THREE.Vector3(x, y, z).multiplyScalar(radius * 4))
  const shadows = look.shadows && look.light !== "flat" && !!stats

  if (look.light === "flat") {
    return (
      <>
        <ambientLight intensity={0.45} />
        <StudioEnvironment />
        <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
        <directionalLight position={[5, 10, 7]} intensity={1.6} />
        <directionalLight position={[-6, 4, -5]} intensity={0.5} />
      </>
    )
  }
  const dramatic = look.light === "dramatic"
  const key = dramatic ? at(1.2, 0.9, 0.5) : at(0.8, 1.3, 1)
  return (
    <>
      <StudioEnvironment intensity={dramatic ? 0.12 : 0.22} />
      <hemisphereLight args={[0xffffff, 0x6b6b78, dramatic ? 0.12 : 0.25]} />
      <primitive object={target} position={center} />
      <directionalLight
        position={key}
        target={target}
        intensity={dramatic ? 3.4 : 1.9}
        castShadow={shadows}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={radius * 0.004}
        shadow-camera-left={-radius * 1.6}
        shadow-camera-right={radius * 1.6}
        shadow-camera-top={radius * 1.6}
        shadow-camera-bottom={-radius * 1.6}
        shadow-camera-near={radius * 0.5}
        shadow-camera-far={radius * 12}
      />
      {/* fill from the other side, rim from behind to separate the silhouette */}
      <directionalLight position={at(-1, 0.4, 0.6)} intensity={dramatic ? 0.15 : 0.3} />
      <directionalLight position={at(-0.3, 0.8, -1.2)} intensity={dramatic ? 1.3 : 0.55} />
      {shadows && stats && (
        <mesh rotation-x={-Math.PI / 2} position={[center.x, stats.min.y - radius * 2e-3, center.z]} receiveShadow>
          <planeGeometry args={[radius * 12, radius * 12]} />
          <shadowMaterial transparent opacity={dramatic ? 0.45 : 0.28} />
        </mesh>
      )}
    </>
  )
}

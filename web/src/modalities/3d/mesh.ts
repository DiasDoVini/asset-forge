import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js"
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js"
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js"
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js"

export const MESH_EXTENSIONS = ["glb", "gltf", "obj", "stl", "ply", "3mf"] as const
export type MeshExt = (typeof MESH_EXTENSIONS)[number]

/** Mirrors forge_3d: glTF/OBJ are Y-up, STL/3MF/PLY are Z-up (CAD / printing, millimeters). */
export const Z_UP: ReadonlySet<string> = new Set(["stl", "3mf", "ply"])

export function extOf(path: string): string {
  const clean = path.split("?")[0]
  return clean.slice(clean.lastIndexOf(".") + 1).toLowerCase()
}

export function isMeshFile(file: { path: string; mime?: string | null }): boolean {
  return (MESH_EXTENSIONS as readonly string[]).includes(extOf(file.path)) || (file.mime ?? "").startsWith("model/")
}

const neutral = () =>
  new THREE.MeshStandardMaterial({ color: 0xb8bcc4, roughness: 0.65, metalness: 0.05, flatShading: false })

/** Load any supported file into an Object3D, converted to Y-up. */
export async function loadMesh(url: string, ext: string): Promise<THREE.Object3D> {
  let obj: THREE.Object3D
  switch (ext) {
    case "glb":
    case "gltf":
      obj = (await new GLTFLoader().loadAsync(url)).scene
      // files without normals (older exports) would render flat-shaded; low-poly files have unshared vertices,
      // so computed normals stay flat for them
      obj.traverse((o) => {
        if (o instanceof THREE.Mesh && !o.geometry.getAttribute("normal")) {
          o.geometry.computeVertexNormals()
          for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
            m.flatShading = false
            m.needsUpdate = true
          }
        }
      })
      break
    case "obj":
      obj = await new OBJLoader().loadAsync(url)
      obj.traverse((o) => {
        if (o instanceof THREE.Mesh) o.material = neutral()
      })
      break
    case "stl": {
      const geo = await new STLLoader().loadAsync(url)
      geo.computeVertexNormals()
      obj = new THREE.Mesh(geo, neutral())
      break
    }
    case "ply": {
      const geo = await new PLYLoader().loadAsync(url)
      geo.computeVertexNormals()
      const mat = neutral()
      if (geo.getAttribute("color")) mat.vertexColors = true
      obj = new THREE.Mesh(geo, mat)
      break
    }
    case "3mf":
      obj = await new ThreeMFLoader().loadAsync(url)
      break
    default:
      throw new Error(`unsupported mesh format: .${ext}`)
  }
  if (Z_UP.has(ext)) {
    const wrapper = new THREE.Group()
    obj.rotation.x = -Math.PI / 2
    wrapper.add(obj)
    obj = wrapper
  }
  obj.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.castShadow = true
      o.receiveShadow = true
    }
  })
  return obj
}

export interface MeshStats {
  triangles: number
  vertices: number
  size: THREE.Vector3
  min: THREE.Vector3
}

export function meshStats(obj: THREE.Object3D): MeshStats {
  let triangles = 0
  let vertices = 0
  obj.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      const g = o.geometry as THREE.BufferGeometry
      const pos = g.getAttribute("position")
      vertices += pos ? pos.count : 0
      triangles += g.index ? g.index.count / 3 : pos ? pos.count / 3 : 0
    }
  })
  obj.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(obj)
  return { triangles: Math.round(triangles), vertices, size: box.getSize(new THREE.Vector3()), min: box.min.clone() }
}

export function formatLength(value: number, units: string): string {
  if (units === "mm") return `${value.toFixed(value < 10 ? 1 : 0)} mm`
  if (units === "m" && value < 1) return `${(value * 100).toFixed(1)} cm`
  return `${value.toFixed(2)} ${units}`
}

/** Project "3d" block (subset used by the UI). */
export interface Project3D {
  units?: string
  printer?: { name?: string; build_volume_mm?: [number, number, number] }
}

export function project3d(project: unknown): Project3D {
  const p = project as Record<string, unknown> | null | undefined
  const block = (p?.["3d"] ?? (p?.config as Record<string, unknown> | undefined)?.["3d"]) as Project3D | undefined
  return block ?? {}
}

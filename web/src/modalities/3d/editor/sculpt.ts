import * as THREE from "three"
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js"

export type BrushMode = "inflate" | "deflate" | "smooth" | "flatten"

export interface Brush {
  mode: BrushMode
  radius: number // world units
  strength: number // 0..1
}

/**
 * Vertex sculpting on a single merged mesh in world space.
 *
 * Vertices that share a position (split by UV seams / flat normals) are welded into "groups"
 * and always move together, so strokes never tear the surface.
 */
export class Sculptor {
  readonly mesh: THREE.Mesh
  private pos: THREE.BufferAttribute
  private groupOf: Int32Array // vertex -> group
  private groupStart: Int32Array // CSR: group -> vertices
  private groupVerts: Int32Array
  private nbStart: Int32Array // CSR: group -> neighbor groups
  private nbList: Int32Array
  private gp: Float32Array // group positions (xyz)
  private gn: Float32Array // group normals
  private undoStack: Float32Array[] = []

  constructor(source: THREE.Object3D) {
    source.updateMatrixWorld(true)
    const geos: THREE.BufferGeometry[] = []
    let material: THREE.Material | null = null
    source.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return
      const g = (o.geometry as THREE.BufferGeometry).clone().applyMatrix4(o.matrixWorld)
      geos.push(g)
      material ??= ([o.material].flat()[0] as THREE.Material) ?? null
    })
    if (!geos.length) throw new Error("no mesh to sculpt")
    // keep only attributes every part has, so they can be merged
    const common = Object.keys(geos[0].attributes).filter((a) => geos.every((g) => g.getAttribute(a)))
    for (const g of geos) for (const a of Object.keys(g.attributes)) if (!common.includes(a)) g.deleteAttribute(a)
    const indexed = geos.every((g) => g.index)
    const merged = geos.length === 1 ? geos[0] : mergeGeometries(indexed ? geos : geos.map((g) => (g.index ? g.toNonIndexed() : g)))
    if (!merged) throw new Error("could not merge the model's parts")
    const mat = (material as THREE.Material | null)?.clone() ?? new THREE.MeshStandardMaterial({ color: 0xb8bcc4, roughness: 0.65 })
    this.mesh = new THREE.Mesh(merged, mat)
    this.pos = merged.getAttribute("position") as THREE.BufferAttribute

    // weld by position
    const n = this.pos.count
    const box = new THREE.Box3().setFromBufferAttribute(this.pos)
    const eps = Math.max(box.getSize(new THREE.Vector3()).length() * 1e-6, 1e-9)
    const key = new Map<string, number>()
    this.groupOf = new Int32Array(n)
    for (let i = 0; i < n; i++) {
      const k = `${Math.round(this.pos.getX(i) / eps)},${Math.round(this.pos.getY(i) / eps)},${Math.round(this.pos.getZ(i) / eps)}`
      let gid = key.get(k)
      if (gid === undefined) {
        gid = key.size
        key.set(k, gid)
      }
      this.groupOf[i] = gid
    }
    const groups = key.size
    this.groupStart = new Int32Array(groups + 1)
    for (let i = 0; i < n; i++) this.groupStart[this.groupOf[i] + 1]++
    for (let g = 0; g < groups; g++) this.groupStart[g + 1] += this.groupStart[g]
    this.groupVerts = new Int32Array(n)
    const fill = this.groupStart.slice(0, groups)
    for (let i = 0; i < n; i++) this.groupVerts[fill[this.groupOf[i]]++] = i

    // neighbors from triangles
    const sets: Set<number>[] = Array.from({ length: groups }, () => new Set())
    const idx = merged.index
    const tri = (k: number) => (idx ? idx.getX(k) : k)
    const triCount = (idx ? idx.count : n) / 3
    for (let t = 0; t < triCount; t++) {
      const a = this.groupOf[tri(3 * t)], b = this.groupOf[tri(3 * t + 1)], c = this.groupOf[tri(3 * t + 2)]
      sets[a].add(b).add(c)
      sets[b].add(a).add(c)
      sets[c].add(a).add(b)
    }
    this.nbStart = new Int32Array(groups + 1)
    for (let g = 0; g < groups; g++) this.nbStart[g + 1] = this.nbStart[g] + sets[g].size
    this.nbList = new Int32Array(this.nbStart[groups])
    for (let g = 0; g < groups; g++) {
      let j = this.nbStart[g]
      for (const v of sets[g]) this.nbList[j++] = v
    }

    this.gp = new Float32Array(groups * 3)
    for (let g = 0; g < groups; g++) {
      const v = this.groupVerts[this.groupStart[g]]
      this.gp[3 * g] = this.pos.getX(v)
      this.gp[3 * g + 1] = this.pos.getY(v)
      this.gp[3 * g + 2] = this.pos.getZ(v)
    }
    this.gn = new Float32Array(groups * 3)
    this.refreshNormals()
  }

  get groups() {
    return this.gp.length / 3
  }

  /** Call when a stroke starts: saves an undo snapshot. */
  begin() {
    this.undoStack.push(this.gp.slice())
    if (this.undoStack.length > 30) this.undoStack.shift()
  }

  /** Call when a stroke ends: recompute normals and bounds (raycasting needs fresh bounds). */
  end() {
    this.refreshNormals()
    this.mesh.geometry.computeBoundingBox()
    this.mesh.geometry.computeBoundingSphere()
  }

  get canUndo() {
    return this.undoStack.length > 0
  }

  undo() {
    const prev = this.undoStack.pop()
    if (!prev) return
    this.gp.set(prev)
    for (let g = 0; g < this.groups; g++) this.writeGroup(g)
    this.pos.needsUpdate = true
    this.end()
  }

  /** One brush dab at ``hit`` (world). */
  dab(hit: THREE.Vector3, brush: Brush) {
    const r = brush.radius
    const r2 = r * r
    const hx = hit.x, hy = hit.y, hz = hit.z
    const affected: number[] = []
    const weights: number[] = []
    for (let g = 0; g < this.groups; g++) {
      const dx = this.gp[3 * g] - hx, dy = this.gp[3 * g + 1] - hy, dz = this.gp[3 * g + 2] - hz
      const d2 = dx * dx + dy * dy + dz * dz
      if (d2 < r2) {
        const t = 1 - d2 / r2
        affected.push(g)
        weights.push(t * t) // smooth falloff
      }
    }
    if (!affected.length) return
    const k = brush.strength

    if (brush.mode === "inflate" || brush.mode === "deflate") {
      const sign = brush.mode === "inflate" ? 1 : -1
      const step = sign * k * r * 0.12
      affected.forEach((g, i) => {
        for (let c = 0; c < 3; c++) this.gp[3 * g + c] += this.gn[3 * g + c] * step * weights[i]
      })
    } else if (brush.mode === "smooth") {
      const target = new Float32Array(affected.length * 3)
      affected.forEach((g, i) => {
        const a = this.nbStart[g], b = this.nbStart[g + 1]
        if (a === b) return
        for (let j = a; j < b; j++) {
          const nb = this.nbList[j]
          for (let c = 0; c < 3; c++) target[3 * i + c] += this.gp[3 * nb + c]
        }
        for (let c = 0; c < 3; c++) target[3 * i + c] /= b - a
      })
      affected.forEach((g, i) => {
        if (this.nbStart[g] === this.nbStart[g + 1]) return
        const w = Math.min(1, k * weights[i] * 0.8)
        for (let c = 0; c < 3; c++) this.gp[3 * g + c] += (target[3 * i + c] - this.gp[3 * g + c]) * w
      })
    } else {
      // flatten: pull toward the plane through the hit with the area's average normal
      const n = new THREE.Vector3()
      affected.forEach((g, i) => n.add(new THREE.Vector3(this.gn[3 * g], this.gn[3 * g + 1], this.gn[3 * g + 2]).multiplyScalar(weights[i])))
      if (n.lengthSq() < 1e-12) return
      n.normalize()
      affected.forEach((g, i) => {
        const d = (this.gp[3 * g] - hx) * n.x + (this.gp[3 * g + 1] - hy) * n.y + (this.gp[3 * g + 2] - hz) * n.z
        const w = Math.min(1, k * weights[i] * 0.6)
        this.gp[3 * g] -= n.x * d * w
        this.gp[3 * g + 1] -= n.y * d * w
        this.gp[3 * g + 2] -= n.z * d * w
      })
    }
    for (const g of affected) this.writeGroup(g)
    this.pos.needsUpdate = true
    // cheap live shading; full normals + bounds are refreshed in end()
    if (this.pos.count < 250_000) this.mesh.geometry.computeVertexNormals()
  }

  private writeGroup(g: number) {
    for (let j = this.groupStart[g]; j < this.groupStart[g + 1]; j++) {
      this.pos.setXYZ(this.groupVerts[j], this.gp[3 * g], this.gp[3 * g + 1], this.gp[3 * g + 2])
    }
  }

  private refreshNormals() {
    const geo = this.mesh.geometry
    geo.computeVertexNormals()
    const nrm = geo.getAttribute("normal") as THREE.BufferAttribute
    this.gn.fill(0)
    for (let i = 0; i < this.pos.count; i++) {
      const g = this.groupOf[i]
      this.gn[3 * g] += nrm.getX(i)
      this.gn[3 * g + 1] += nrm.getY(i)
      this.gn[3 * g + 2] += nrm.getZ(i)
    }
    for (let g = 0; g < this.groups; g++) {
      const l = Math.hypot(this.gn[3 * g], this.gn[3 * g + 1], this.gn[3 * g + 2]) || 1
      this.gn[3 * g] /= l
      this.gn[3 * g + 1] /= l
      this.gn[3 * g + 2] /= l
    }
  }
}

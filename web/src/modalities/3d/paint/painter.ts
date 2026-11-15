import * as THREE from "three"

export type RGB = [number, number, number]

export interface BrushSettings {
  radius: number // world units
  color: RGB
  opacity: number // 0..1 per dab
  hardness: number // 0 = soft, 1 = hard edge
}

/**
 * Paints one textured mesh.
 *
 * Dabs work in 3D: every triangle near the brush is rasterized in UV space and each texel is shaded by
 * its *3D* distance to the brush center - so strokes cross UV seams without gaps, and the brush keeps its
 * size whatever the texel density.
 */
export class TexturePainter {
  readonly mesh: THREE.Mesh
  readonly canvas: HTMLCanvasElement
  readonly texture: THREE.CanvasTexture
  private ctx: CanvasRenderingContext2D
  private data: ImageData
  private pos: Float32Array // world positions per vertex
  private uv: Float32Array // pixel coords per vertex
  private tris: Uint32Array
  private centers: Float32Array // per triangle center (xyz) + bounding radius
  private undoStack: Uint8ClampedArray[] = []
  dirty = false

  constructor(mesh: THREE.Mesh, image: CanvasImageSource & { width: number; height: number }) {
    this.mesh = mesh
    this.canvas = document.createElement("canvas")
    this.canvas.width = image.width
    this.canvas.height = image.height
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: true })!
    this.ctx.drawImage(image, 0, 0)
    this.data = this.ctx.getImageData(0, 0, this.canvas.width, this.canvas.height)

    this.texture = new THREE.CanvasTexture(this.canvas)
    this.texture.flipY = false // glTF convention: uv (0,0) = top-left of the image
    this.texture.colorSpace = THREE.SRGBColorSpace
    this.texture.anisotropy = 4
    const old = [mesh.material].flat()[0] as THREE.MeshStandardMaterial
    const geo = mesh.geometry as THREE.BufferGeometry
    // meshes exported without normals rely on flat shading (GLTFLoader sets it); keep the same look
    mesh.material = new THREE.MeshStandardMaterial({
      map: this.texture, roughness: old?.roughness ?? 0.85, metalness: 0, side: old?.side ?? THREE.FrontSide,
      flatShading: old?.flatShading ?? !geo.getAttribute("normal"),
    })

    mesh.updateWorldMatrix(true, false)
    const p = geo.getAttribute("position")
    const t = geo.getAttribute("uv")
    const n = p.count
    this.pos = new Float32Array(n * 3)
    this.uv = new Float32Array(n * 2)
    const v = new THREE.Vector3()
    for (let i = 0; i < n; i++) {
      v.fromBufferAttribute(p, i).applyMatrix4(mesh.matrixWorld)
      this.pos.set([v.x, v.y, v.z], i * 3)
      this.uv[i * 2] = t.getX(i) * this.canvas.width - 0.5
      this.uv[i * 2 + 1] = t.getY(i) * this.canvas.height - 0.5
    }
    this.tris = geo.index ? Uint32Array.from(geo.index.array as ArrayLike<number>) : Uint32Array.from({ length: n }, (_, i) => i)
    const nt = this.tris.length / 3
    this.centers = new Float32Array(nt * 4)
    for (let f = 0; f < nt; f++) {
      const [a, b, c] = [this.tris[f * 3], this.tris[f * 3 + 1], this.tris[f * 3 + 2]]
      const cx = (this.pos[a * 3] + this.pos[b * 3] + this.pos[c * 3]) / 3
      const cy = (this.pos[a * 3 + 1] + this.pos[b * 3 + 1] + this.pos[c * 3 + 1]) / 3
      const cz = (this.pos[a * 3 + 2] + this.pos[b * 3 + 2] + this.pos[c * 3 + 2]) / 3
      let r = 0
      for (const k of [a, b, c]) r = Math.max(r, Math.hypot(this.pos[k * 3] - cx, this.pos[k * 3 + 1] - cy, this.pos[k * 3 + 2] - cz))
      this.centers.set([cx, cy, cz, r], f * 4)
    }
  }

  begin() {
    this.undoStack.push(this.data.data.slice())
    if (this.undoStack.length > 12) this.undoStack.shift()
  }

  get canUndo() {
    return this.undoStack.length > 0
  }

  undo() {
    const prev = this.undoStack.pop()
    if (!prev) return
    this.data.data.set(prev)
    this.ctx.putImageData(this.data, 0, 0)
    this.texture.needsUpdate = true
  }

  dab(hit: THREE.Vector3, b: BrushSettings) {
    const { width: W, height: H } = this.canvas
    const d = this.data.data
    const r = b.radius
    const inner = r * Math.min(0.999, Math.max(0, b.hardness))
    let x0 = W, y0 = H, x1 = -1, y1 = -1
    const nt = this.tris.length / 3
    for (let f = 0; f < nt; f++) {
      const cx = this.centers[f * 4] - hit.x, cy = this.centers[f * 4 + 1] - hit.y, cz = this.centers[f * 4 + 2] - hit.z
      const reach = r + this.centers[f * 4 + 3]
      if (cx * cx + cy * cy + cz * cz > reach * reach) continue
      const ia = this.tris[f * 3], ib = this.tris[f * 3 + 1], ic = this.tris[f * 3 + 2]
      const ax = this.uv[ia * 2], ay = this.uv[ia * 2 + 1], bx = this.uv[ib * 2], by = this.uv[ib * 2 + 1], qx = this.uv[ic * 2], qy = this.uv[ic * 2 + 1]
      const den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
      if (Math.abs(den) < 1e-12) continue
      const minX = Math.max(0, Math.floor(Math.min(ax, bx, qx))), maxX = Math.min(W - 1, Math.ceil(Math.max(ax, bx, qx)))
      const minY = Math.max(0, Math.floor(Math.min(ay, by, qy))), maxY = Math.min(H - 1, Math.ceil(Math.max(ay, by, qy)))
      for (let y = minY; y <= maxY; y++) {
        for (let x = minX; x <= maxX; x++) {
          const l0 = ((by - qy) * (x - qx) + (qx - bx) * (y - qy)) / den
          const l1 = ((qy - ay) * (x - qx) + (ax - qx) * (y - qy)) / den
          const l2 = 1 - l0 - l1
          // a little slack so texels on triangle edges (and the padding just outside) get painted too
          if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) continue
          const px = l0 * this.pos[ia * 3] + l1 * this.pos[ib * 3] + l2 * this.pos[ic * 3] - hit.x
          const py = l0 * this.pos[ia * 3 + 1] + l1 * this.pos[ib * 3 + 1] + l2 * this.pos[ic * 3 + 1] - hit.y
          const pz = l0 * this.pos[ia * 3 + 2] + l1 * this.pos[ib * 3 + 2] + l2 * this.pos[ic * 3 + 2] - hit.z
          const dist = Math.sqrt(px * px + py * py + pz * pz)
          if (dist >= r) continue
          const fall = dist <= inner ? 1 : 1 - (dist - inner) / (r - inner)
          const a = b.opacity * fall * fall * (3 - 2 * fall)
          const o = (y * W + x) * 4
          d[o] += (b.color[0] - d[o]) * a
          d[o + 1] += (b.color[1] - d[o + 1]) * a
          d[o + 2] += (b.color[2] - d[o + 2]) * a
          d[o + 3] = 255
          if (x < x0) x0 = x
          if (y < y0) y0 = y
          if (x > x1) x1 = x
          if (y > y1) y1 = y
        }
      }
    }
    if (x1 >= x0) {
      this.ctx.putImageData(this.data, 0, 0, x0, y0, x1 - x0 + 1, y1 - y0 + 1)
      this.texture.needsUpdate = true
      this.dirty = true
    }
  }

  fill(color: RGB) {
    const d = this.data.data
    for (let i = 0; i < d.length; i += 4) {
      d[i] = color[0]
      d[i + 1] = color[1]
      d[i + 2] = color[2]
      d[i + 3] = 255
    }
    this.ctx.putImageData(this.data, 0, 0)
    this.texture.needsUpdate = true
    this.dirty = true
  }

  /** Color under a uv coordinate (eyedropper). */
  sample(uv: THREE.Vector2): RGB {
    const x = Math.min(this.canvas.width - 1, Math.max(0, Math.floor(uv.x * this.canvas.width)))
    const y = Math.min(this.canvas.height - 1, Math.max(0, Math.floor(uv.y * this.canvas.height)))
    const o = (y * this.canvas.width + x) * 4
    const d = this.data.data
    return [d[o], d[o + 1], d[o + 2]]
  }

  toBlob(): Promise<Blob> {
    return new Promise((res, rej) => this.canvas.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), "image/png"))
  }
}

export const hexToRgb = (hex: string): RGB => {
  const h = hex.replace("#", "")
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB
}

export const rgbToHex = (c: RGB) => "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")

/** Texture image of a loaded mesh, if it is paintable (has UVs and a color map). */
export function paintableImage(mesh: THREE.Mesh): (CanvasImageSource & { width: number; height: number }) | null {
  const geo = mesh.geometry as THREE.BufferGeometry
  if (!geo.getAttribute("uv")) return null
  const mat = [mesh.material].flat()[0] as THREE.MeshStandardMaterial | undefined
  const img = mat?.map?.image as (CanvasImageSource & { width: number; height: number }) | undefined
  return img && img.width ? img : null
}

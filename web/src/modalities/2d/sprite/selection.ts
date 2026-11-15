// Seleção do editor de sprite: máscara de 1 byte por pixel (1 = selecionado). Pura, sem React.
import type { Pt } from '@/modalities/pixel/tools'
import { ellipsePoints, fillRegion, polygonPoints, rectPoints } from './paint'

export type Mask = Uint8Array
export type SelMode = 'replace' | 'add' | 'subtract' | 'intersect'
export type Box = { x: number; y: number; w: number; h: number }

export const emptyMask = (w: number, h: number): Mask => new Uint8Array(w * h)

function fromPoints(w: number, h: number, pts: Iterable<Pt>): Mask {
  const m = emptyMask(w, h)
  for (const [x, y] of pts) if (x >= 0 && y >= 0 && x < w && y < h) m[y * w + x] = 1
  return m
}

export const rectMask = (w: number, h: number, a: Pt, b: Pt) => fromPoints(w, h, rectPoints(a, b, true))
export const ellipseMask = (w: number, h: number, a: Pt, b: Pt) => fromPoints(w, h, ellipsePoints(a, b, true))
export const polygonMask = (w: number, h: number, verts: Pt[]) => fromPoints(w, h, polygonPoints(verts))

/** Varinha (contígua) ou por cor (global), comparando em `sample` (RGBA). */
export function colorMask(sample: Uint8ClampedArray, w: number, h: number, at: Pt, contiguous: boolean, tolerance: number): Mask {
  const m = emptyMask(w, h)
  for (const p of fillRegion(sample, w, h, at[0], at[1], { contiguous, tolerance })) m[p] = 1
  return m
}

export function combine(cur: Mask | null, next: Mask, mode: SelMode): Mask {
  if (!cur || mode === 'replace') return next
  const out = new Uint8Array(next.length)
  for (let i = 0; i < out.length; i++) {
    const a = cur[i]
    const b = next[i]
    out[i] = mode === 'add' ? a | b : mode === 'subtract' ? (a && !b ? 1 : 0) : a & b
  }
  return out
}

export function invert(m: Mask): Mask {
  const out = new Uint8Array(m.length)
  for (let i = 0; i < m.length; i++) out[i] = m[i] ? 0 : 1
  return out
}

export function bounds(m: Mask, w: number): Box | null {
  let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1
  for (let i = 0; i < m.length; i++)
    if (m[i]) {
      const x = i % w
      const y = (i / w) | 0
      if (x < x0) x0 = x
      if (x > x1) x1 = x
      if (y < y0) y0 = y
      if (y > y1) y1 = y
    }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 }
}

export const isEmptyMask = (m: Mask | null) => !m || !m.some((v) => v)

/** Restringe uma pintura à seleção: fora dela volta a imagem de antes. */
export function clipToMask(out: Uint8ClampedArray, base: Uint8ClampedArray, m: Mask | null) {
  if (!m) return out
  for (let i = 0; i < m.length; i++)
    if (!m[i]) {
      const o = i * 4
      out[o] = base[o]
      out[o + 1] = base[o + 1]
      out[o + 2] = base[o + 2]
      out[o + 3] = base[o + 3]
    }
  return out
}

export type Img = { w: number; h: number; data: Uint8ClampedArray }

/** Copia os pixels selecionados (recortados ao retângulo da seleção; fora da máscara fica transparente). */
export function extract(data: Uint8ClampedArray, W: number, m: Mask): (Img & { x: number; y: number }) | null {
  const b = bounds(m, W)
  if (!b) return null
  const out = new Uint8ClampedArray(b.w * b.h * 4)
  for (let y = 0; y < b.h; y++)
    for (let x = 0; x < b.w; x++) {
      const si = (b.y + y) * W + (b.x + x)
      if (!m[si]) continue
      out.set(data.subarray(si * 4, si * 4 + 4), (y * b.w + x) * 4)
    }
  return { x: b.x, y: b.y, w: b.w, h: b.h, data: out }
}

/** Apaga os pixels selecionados (cópia nova). */
export function cleared(data: Uint8ClampedArray, m: Mask): Uint8ClampedArray {
  const out = data.slice()
  for (let i = 0; i < m.length; i++) if (m[i]) out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = out[i * 4 + 3] = 0
  return out
}

/** Máscara a partir do alfa de uma imagem posicionada (seleção depois de aplicar uma transformação). */
export function maskFromImage(W: number, H: number, img: Img, x: number, y: number): Mask {
  const m = emptyMask(W, H)
  for (let yy = 0; yy < img.h; yy++)
    for (let xx = 0; xx < img.w; xx++) {
      const tx = x + xx
      const ty = y + yy
      if (tx < 0 || ty < 0 || tx >= W || ty >= H) continue
      if (img.data[(yy * img.w + xx) * 4 + 3]) m[ty * W + tx] = 1
    }
  return m
}

/** Segmentos da borda da seleção (para as "formigas marchando"), em coordenadas de pixel. */
export function outline(m: Mask, W: number, H: number): [number, number, number, number][] {
  const segs: [number, number, number, number][] = []
  const on = (x: number, y: number) => x >= 0 && y >= 0 && x < W && y < H && m[y * W + x] === 1
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      if (!on(x, y)) continue
      if (!on(x, y - 1)) segs.push([x, y, x + 1, y])
      if (!on(x, y + 1)) segs.push([x, y + 1, x + 1, y + 1])
      if (!on(x - 1, y)) segs.push([x, y, x, y + 1])
      if (!on(x + 1, y)) segs.push([x + 1, y, x + 1, y + 1])
    }
  return segs
}

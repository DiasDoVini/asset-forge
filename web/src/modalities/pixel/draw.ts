// Desenho em <canvas> do Pixel Studio (editor e viewer): xadrez, sprite, grade, overlay da spec,
// destaques de checagens, seleção e cursor.
import type { PixelSpec } from '@/core/api'
import { gridToImageData, pivotTopLeft, safeAreaTopLeft, type Grid, type Highlight } from './render'
import type { Pt, Rect } from './tools'

export type View = { z: number; ox: number; oy: number }

export const SLOT_DARK = '#1a1830'
export const SLOT_LIGHT = '#e9e6f2'
const CRIMSON = '#dc143c'
const GOLD = '#ffd23f'

let scratch: HTMLCanvasElement | null = null

/** Canvas W×H com a grade (reaproveitado entre quadros). */
export function gridCanvas(g: Grid): HTMLCanvasElement {
  scratch ??= document.createElement('canvas')
  if (scratch.width !== g.w) scratch.width = g.w
  if (scratch.height !== g.h) scratch.height = g.h
  scratch.getContext('2d')!.putImageData(gridToImageData(g), 0, 0)
  return scratch
}

export function drawChecker(ctx: CanvasRenderingContext2D, w: number, h: number, v: View) {
  const { z, ox, oy } = v
  ctx.fillStyle = '#26262c'
  ctx.fillRect(ox, oy, w * z, h * z)
  ctx.fillStyle = '#33333b'
  const c = Math.max(2, Math.floor(z / 2))
  for (let y = 0; y < h * z; y += c)
    for (let x = ((y / c) % 2) * c; x < w * z; x += 2 * c) ctx.fillRect(ox + x, oy + y, Math.min(c, w * z - x), Math.min(c, h * z - y))
}

export function drawImageSource(ctx: CanvasRenderingContext2D, src: CanvasImageSource, w: number, h: number, v: View) {
  ctx.imageSmoothingEnabled = false
  ctx.drawImage(src, v.ox, v.oy, w * v.z, h * v.z)
}

export function drawGridLines(ctx: CanvasRenderingContext2D, w: number, h: number, v: View) {
  const { z, ox, oy } = v
  const line = (x0: number, y0: number, x1: number, y1: number) => {
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.lineTo(x1, y1)
    ctx.stroke()
  }
  ctx.lineWidth = 1
  for (const strong of [false, true]) {
    ctx.strokeStyle = strong ? 'rgba(255,255,255,0.28)' : 'rgba(255,255,255,0.09)'
    for (let x = 0; x <= w; x++) if ((x % 4 === 0) === strong) line(ox + x * z + 0.5, oy, ox + x * z + 0.5, oy + h * z)
    for (let y = 0; y <= h; y++) if ((y % 4 === 0) === strong) line(ox, oy + y * z + 0.5, ox + w * z, oy + y * z + 0.5)
  }
}

/** Área segura (verde) e pivô (cruz carmesim, centro dourado). Spec com origem inferior esquerda. */
export function drawSpecOverlay(ctx: CanvasRenderingContext2D, spec: Pick<PixelSpec, 'pivot' | 'safe_area'>, h: number, v: View, show: { safe: boolean; pivot: boolean }) {
  const { z, ox, oy } = v
  if (show.safe && spec.safe_area) {
    const [x, y, sw, sh] = safeAreaTopLeft(spec.safe_area, h)
    ctx.fillStyle = 'rgba(34,197,94,0.14)'
    ctx.fillRect(ox + x * z, oy + y * z, sw * z, sh * z)
    ctx.strokeStyle = 'rgba(34,197,94,0.8)'
    ctx.setLineDash([4, 3])
    ctx.strokeRect(ox + x * z + 0.5, oy + y * z + 0.5, sw * z - 1, sh * z - 1)
    ctx.setLineDash([])
  }
  if (show.pivot && spec.pivot) {
    const [px, py] = pivotTopLeft(spec.pivot, h)
    const cx = ox + px * z, cy = oy + py * z
    const arm = Math.max(8, z * 1.5)
    ctx.strokeStyle = CRIMSON
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(cx - arm, cy)
    ctx.lineTo(cx + arm, cy)
    ctx.moveTo(cx, cy - arm)
    ctx.lineTo(cx, cy + arm)
    ctx.stroke()
    ctx.lineWidth = 1
    ctx.fillStyle = GOLD
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(2.5, z / 5), 0, Math.PI * 2)
    ctx.fill()
    ctx.strokeStyle = CRIMSON
    ctx.stroke()
  }
}

export function drawHighlight(ctx: CanvasRenderingContext2D, hl: Highlight, v: View) {
  const { z, ox, oy } = v
  ctx.fillStyle = 'rgba(255,0,200,0.45)'
  ctx.strokeStyle = 'rgba(255,90,230,1)'
  ctx.lineWidth = 2
  for (const [x, y] of hl.pixels) {
    ctx.fillRect(ox + x * z, oy + y * z, z, z)
    ctx.strokeRect(ox + x * z + 1, oy + y * z + 1, z - 2, z - 2)
  }
  for (const [x, y, w, h] of hl.rects) ctx.strokeRect(ox + x * z + 1, oy + y * z + 1, w * z - 2, h * z - 2)
  ctx.lineWidth = 1
}

export function drawSelection(ctx: CanvasRenderingContext2D, r: Rect, v: View, t: number) {
  const { z, ox, oy } = v
  ctx.lineWidth = 1
  ctx.setLineDash([4, 4])
  ctx.lineDashOffset = -t
  ctx.strokeStyle = '#fff'
  ctx.strokeRect(ox + r.x * z + 0.5, oy + r.y * z + 0.5, r.w * z - 1, r.h * z - 1)
  ctx.lineDashOffset = -t + 4
  ctx.strokeStyle = '#000'
  ctx.strokeRect(ox + r.x * z + 0.5, oy + r.y * z + 0.5, r.w * z - 1, r.h * z - 1)
  ctx.setLineDash([])
  ctx.lineDashOffset = 0
}

export function drawCursor(ctx: CanvasRenderingContext2D, pts: Pt[], v: View) {
  const { z, ox, oy } = v
  ctx.lineWidth = 1
  for (const [x, y] of pts) {
    ctx.strokeStyle = 'rgba(0,0,0,0.8)'
    ctx.strokeRect(ox + x * z - 0.5, oy + y * z - 0.5, z + 1, z + 1)
    ctx.strokeStyle = 'rgba(255,255,255,0.9)'
    ctx.strokeRect(ox + x * z + 0.5, oy + y * z + 0.5, z - 1, z - 1)
  }
}

export function drawBorder(ctx: CanvasRenderingContext2D, w: number, h: number, v: View) {
  ctx.strokeStyle = 'rgba(255,255,255,0.35)'
  ctx.lineWidth = 1
  ctx.strokeRect(v.ox - 0.5, v.oy - 0.5, w * v.z + 1, h * v.z + 1)
}

/** Linha do eixo de espelho (centro X). */
export function drawMirrorAxis(ctx: CanvasRenderingContext2D, w: number, h: number, v: View) {
  const x = v.ox + (w / 2) * v.z
  ctx.strokeStyle = 'rgba(56,189,248,0.7)'
  ctx.setLineDash([6, 4])
  ctx.beginPath()
  ctx.moveTo(x + 0.5, v.oy)
  ctx.lineTo(x + 0.5, v.oy + h * v.z)
  ctx.stroke()
  ctx.setLineDash([])
}

// Ferramentas puras do Pixel Studio: rasterização (linha/retângulo), balde 4-conexo,
// espelho X, deslocamento e mover região. Todas devolvem uma grade NOVA.
import { cloneGrid, emptyGrid, getPx, TRANSPARENT, type Grid } from './render'

export type Pt = [number, number]
export type Rect = { x: number; y: number; w: number; h: number }

export type Tool = 'pencil' | 'eraser' | 'fill' | 'picker' | 'line' | 'rect' | 'select'

export const inside = (g: Grid, x: number, y: number) => x >= 0 && y >= 0 && x < g.w && y < g.h

/** Bresenham inclusivo nas duas pontas. */
export function linePoints(x0: number, y0: number, x1: number, y1: number): Pt[] {
  const pts: Pt[] = []
  const dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1
  const dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1
  let err = dx + dy
  let x = x0, y = y0
  for (;;) {
    pts.push([x, y])
    if (x === x1 && y === y1) break
    const e2 = 2 * err
    if (e2 >= dy) {
      err += dy
      x += sx
    }
    if (e2 <= dx) {
      err += dx
      y += sy
    }
  }
  return pts
}

export function normRect(x0: number, y0: number, x1: number, y1: number): Rect {
  return { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0) + 1, h: Math.abs(y1 - y0) + 1 }
}

export function rectPoints(x0: number, y0: number, x1: number, y1: number, filled: boolean): Pt[] {
  const r = normRect(x0, y0, x1, y1)
  const pts: Pt[] = []
  for (let y = r.y; y < r.y + r.h; y++)
    for (let x = r.x; x < r.x + r.w; x++)
      if (filled || y === r.y || y === r.y + r.h - 1 || x === r.x || x === r.x + r.w - 1) pts.push([x, y])
  return pts
}

/** Adiciona os pontos espelhados no eixo X (centro do canvas), sem duplicar. */
export function withMirror(pts: Pt[], w: number, on: boolean): Pt[] {
  if (!on) return pts
  const seen = new Set<number>()
  const out: Pt[] = []
  for (const [x, y] of pts)
    for (const p of [[x, y], [w - 1 - x, y]] as Pt[]) {
      const k = p[1] * 100003 + p[0]
      if (!seen.has(k)) {
        seen.add(k)
        out.push(p)
      }
    }
  return out
}

export function plot(g: Grid, pts: Pt[], color: number): Grid {
  const out = cloneGrid(g)
  for (const [x, y] of pts) if (inside(g, x, y)) out.px[y * g.w + x] = color
  return out
}

/** Balde 4-conexo: substitui a região contígua da cor em (x,y) por `color`. */
export function floodFill(g: Grid, x: number, y: number, color: number): Grid {
  if (!inside(g, x, y)) return g
  const target = getPx(g, x, y)
  if (target === color) return g
  const out = cloneGrid(g)
  const stack: number[] = [y * g.w + x]
  while (stack.length) {
    const i = stack.pop()!
    if (out.px[i] !== target) continue
    out.px[i] = color
    const cx = i % g.w, cy = (i - cx) / g.w
    if (cx > 0) stack.push(i - 1)
    if (cx < g.w - 1) stack.push(i + 1)
    if (cy > 0) stack.push(i - g.w)
    if (cy < g.h - 1) stack.push(i + g.w)
  }
  return out
}

/** Desloca o desenho inteiro; o que sai do canvas se perde. */
export function shiftGrid(g: Grid, dx: number, dy: number): Grid {
  const out = emptyGrid(g.w, g.h)
  for (let y = 0; y < g.h; y++)
    for (let x = 0; x < g.w; x++) {
      const nx = x + dx, ny = y + dy
      if (inside(g, nx, ny)) out.px[ny * g.w + nx] = g.px[y * g.w + x]
    }
  return out
}

export function clipRect(r: Rect, g: Grid): Rect | null {
  const x0 = Math.max(0, r.x), y0 = Math.max(0, r.y)
  const x1 = Math.min(g.w, r.x + r.w), y1 = Math.min(g.h, r.y + r.h)
  return x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null
}

/**
 * Move os pixels da região por (dx,dy): a origem fica transparente e os pixels opacos
 * movidos sobrescrevem o destino (transparentes da seleção não apagam o que está embaixo).
 */
export function moveRegion(g: Grid, r: Rect, dx: number, dy: number): Grid {
  const out = cloneGrid(g)
  const buf: { x: number; y: number; v: number }[] = []
  for (let y = r.y; y < r.y + r.h; y++)
    for (let x = r.x; x < r.x + r.w; x++) {
      if (!inside(g, x, y)) continue
      const v = g.px[y * g.w + x]
      out.px[y * g.w + x] = TRANSPARENT
      if (v !== TRANSPARENT) buf.push({ x: x + dx, y: y + dy, v })
    }
  for (const p of buf) if (inside(g, p.x, p.y)) out.px[p.y * g.w + p.x] = p.v
  return out
}

export function clearRegion(g: Grid, r: Rect): Grid {
  const out = cloneGrid(g)
  for (let y = r.y; y < r.y + r.h; y++) for (let x = r.x; x < r.x + r.w; x++) if (inside(g, x, y)) out.px[y * g.w + x] = TRANSPARENT
  return out
}

export const inRect = (r: Rect, x: number, y: number) => x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h

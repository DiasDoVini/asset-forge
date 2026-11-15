// Motor de pintura do editor de sprite (Fase 2): pincéis, inks, simetria, pixel-perfect, formas (elipse, curva,
// polígono, contorno), balde contíguo/global com tolerância e gradiente com dithering.
//
// Modelo: um traço (ou forma) vira um CONJUNTO de pixels; o ink é aplicado uma vez por pixel contra a imagem de antes
// do traço (`base`). Assim, passar duas vezes pelo mesmo pixel num traço com alfa não escurece (como no Aseprite), e o
// pixel-perfect pode reescrever o caminho a cada movimento.
import { linePoints, type Pt } from '@/modalities/pixel/tools'
import type { RGBA } from './raster'

// ---------------------------------------------------------------- pincel

export type BrushShape = 'square' | 'circle' | 'hline' | 'vline'

export const BRUSH_SHAPES: { id: BrushShape; title: string }[] = [
  { id: 'square', title: 'Quadrado' }, { id: 'circle', title: 'Círculo' },
  { id: 'hline', title: 'Linha horizontal' }, { id: 'vline', title: 'Linha vertical' },
]

/** Deslocamentos do pincel em torno do ponto (tamanho par puxa para cima/esquerda, como no Aseprite). */
export function brushOffsets(shape: BrushShape, size: number): Pt[] {
  if (size <= 1) return [[0, 0]]
  const r0 = -Math.floor((size - 1) / 2)
  const out: Pt[] = []
  if (shape === 'hline') {
    for (let i = 0; i < size; i++) out.push([r0 + i, 0])
    return out
  }
  if (shape === 'vline') {
    for (let i = 0; i < size; i++) out.push([0, r0 + i])
    return out
  }
  const c = (size - 1) / 2
  const rr = (size / 2) ** 2
  for (let dy = 0; dy < size; dy++)
    for (let dx = 0; dx < size; dx++) {
      if (shape === 'circle' && (dx - c) ** 2 + (dy - c) ** 2 > rr - 0.25 * (size > 3 ? 1 : 0)) continue
      out.push([r0 + dx, r0 + dy])
    }
  return out
}

// ---------------------------------------------------------------- simetria

export type Symmetry = { mode: 'none' | 'h' | 'v' | 'both'; x: number; y: number } // x/y: eixo em coordenadas de borda (8 = entre o px 7 e o 8)

/** Espelha os pontos: 'h' espelha na horizontal (eixo vertical em x), 'v' na vertical (eixo horizontal em y). */
export function mirror(pts: Pt[], s: Symmetry): Pt[] {
  if (s.mode === 'none') return pts
  const out = [...pts]
  const mx = (p: Pt): Pt => [Math.round(2 * s.x - p[0] - 1), p[1]]
  const my = (p: Pt): Pt => [p[0], Math.round(2 * s.y - p[1] - 1)]
  for (const p of pts) {
    if (s.mode === 'h' || s.mode === 'both') out.push(mx(p))
    if (s.mode === 'v' || s.mode === 'both') out.push(my(p))
    if (s.mode === 'both') out.push(my(mx(p)))
  }
  return out
}

// ---------------------------------------------------------------- caminho

/** Liga os pontos do mouse em linhas contínuas (sem buracos). */
export function polyline(points: Pt[]): Pt[] {
  if (points.length === 0) return []
  const out: Pt[] = [points[0]]
  for (let i = 1; i < points.length; i++) {
    const seg = linePoints(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    for (let k = 1; k < seg.length; k++) out.push(seg[k])
  }
  return out
}

/** Pixel-perfect: remove os "cotovelos" em L de traços de 1 px (o pixel do meio quando os vizinhos se tocam na diagonal). */
export function pixelPerfect(path: Pt[]): Pt[] {
  const out: Pt[] = []
  for (const p of path) {
    const last = out[out.length - 1]
    if (last && last[0] === p[0] && last[1] === p[1]) continue
    out.push(p)
    while (out.length >= 3) {
      const [a, b, c] = out.slice(-3)
      const ortho = (a[0] === b[0] || a[1] === b[1]) && (b[0] === c[0] || b[1] === c[1])
      const diag = Math.abs(a[0] - c[0]) === 1 && Math.abs(a[1] - c[1]) === 1
      if (ortho && diag) out.splice(out.length - 2, 1)
      else break
    }
  }
  return out
}

/** Estabilizador ("corda"): o pincel só anda quando o mouse se afasta mais que `dist` px dele. */
export function stabilize(prev: [number, number], target: [number, number], dist: number): [number, number] {
  if (dist <= 0) return target
  const dx = target[0] - prev[0]
  const dy = target[1] - prev[1]
  const d = Math.hypot(dx, dy)
  if (d <= dist) return prev
  const k = (d - dist) / d
  return [prev[0] + dx * k, prev[1] + dy * k]
}

// ---------------------------------------------------------------- formas

export function rectPoints(a: Pt, b: Pt, filled: boolean): Pt[] {
  const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0])
  const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1])
  const pts: Pt[] = []
  for (let y = y0; y <= y1; y++)
    for (let x = x0; x <= x1; x++) if (filled || y === y0 || y === y1 || x === x0 || x === x1) pts.push([x, y])
  return pts
}

/** Elipse inscrita no retângulo a–b (algoritmo de ponto médio, simétrica nos dois eixos, sem pixels duplos). */
export function ellipsePoints(a: Pt, b: Pt, filled: boolean): Pt[] {
  const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0])
  const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1])
  const w = x1 - x0, h = y1 - y0
  if (w < 2 || h < 2) return rectPoints(a, b, true)
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  const rx = w / 2
  const ry = h / 2
  // por linha: o intervalo [xl, xr] que fica dentro da elipse
  const span = (y: number): [number, number] | null => {
    const dy = (y - cy) / (ry + 0.5)
    if (Math.abs(dy) > 1) return null
    const dx = (rx + 0.5) * Math.sqrt(1 - dy * dy)
    const xl = Math.ceil(cx - dx - 1e-9)
    const xr = Math.floor(cx + dx + 1e-9)
    return xl <= xr ? [Math.max(x0, xl), Math.min(x1, xr)] : null
  }
  const rows: ([number, number] | null)[] = []
  for (let y = y0; y <= y1; y++) rows.push(span(y))
  const pts: Pt[] = []
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i]
    if (!r) continue
    const y = y0 + i
    if (filled) {
      for (let x = r[0]; x <= r[1]; x++) pts.push([x, y])
      continue
    }
    // contorno: pixels da linha que não estão cobertos pelas linhas vizinhas (fecha as laterais sem degraus vazados)
    const up = rows[i - 1]
    const dn = rows[i + 1]
    for (let x = r[0]; x <= r[1]; x++) {
      const inUp = up && x >= up[0] && x <= up[1]
      const inDn = dn && x >= dn[0] && x <= dn[1]
      if (x === r[0] || x === r[1] || !inUp || !inDn) pts.push([x, y])
    }
  }
  return pts
}

/** Curva de Bézier cúbica a → b com controles c1, c2, amostrada densa e ligada em linhas. */
export function bezierPoints(a: Pt, c1: Pt, c2: Pt, b: Pt): Pt[] {
  const len = Math.hypot(c1[0] - a[0], c1[1] - a[1]) + Math.hypot(c2[0] - c1[0], c2[1] - c1[1]) + Math.hypot(b[0] - c2[0], b[1] - c2[1])
  const n = Math.max(2, Math.ceil(len * 2))
  const raw: Pt[] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    const u = 1 - t
    const x = u * u * u * a[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * b[0]
    const y = u * u * u * a[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * b[1]
    const p: Pt = [Math.round(x), Math.round(y)]
    const last = raw[raw.length - 1]
    if (!last || last[0] !== p[0] || last[1] !== p[1]) raw.push(p)
  }
  return pixelPerfect(polyline(raw))
}

/** Polígono preenchido (regra par-ímpar pelos centros dos pixels) + o contorno, para não perder pontas finas. */
export function polygonPoints(vertices: Pt[]): Pt[] {
  if (vertices.length === 0) return []
  if (vertices.length < 3) return polyline(vertices)
  const ys = vertices.map((v) => v[1])
  const out: Pt[] = []
  for (let y = Math.min(...ys); y <= Math.max(...ys); y++) {
    const cyy = y + 0.5
    const xs: number[] = []
    for (let i = 0; i < vertices.length; i++) {
      const [ax, ay] = vertices[i].map((v) => v + 0.5)
      const [bx, by] = vertices[(i + 1) % vertices.length].map((v) => v + 0.5)
      if ((ay <= cyy && by > cyy) || (by <= cyy && ay > cyy)) xs.push(ax + ((cyy - ay) * (bx - ax)) / (by - ay))
    }
    xs.sort((p, q) => p - q)
    for (let k = 0; k + 1 < xs.length; k += 2)
      for (let x = Math.ceil(xs[k] - 0.5); x <= Math.floor(xs[k + 1] - 0.5); x++) out.push([x, y])
  }
  return [...out, ...polyline([...vertices, vertices[0]])]
}

// ---------------------------------------------------------------- inks

export type Ink = 'simple' | 'alpha' | 'copy' | 'lock_alpha' | 'shading'

export const INKS: { id: Ink; title: string; hint: string }[] = [
  { id: 'simple', title: 'Simples', hint: 'cor opaca substitui; com alfa, mistura; alfa 0 apaga' },
  { id: 'alpha', title: 'Composição alfa', hint: 'sempre mistura pela transparência da cor' },
  { id: 'copy', title: 'Copiar alfa+cor', hint: 'substitui o pixel pela cor exata, inclusive o alfa' },
  { id: 'lock_alpha', title: 'Travar alfa', hint: 'só troca a cor onde já há pixel; mantém a transparência' },
  { id: 'shading', title: 'Sombreamento', hint: 'troca cada cor pela vizinha na rampa da paleta (esquerdo: próxima, direito: anterior)' },
]

export type InkOpts = {
  ink: Ink
  color: RGBA | null // null = apagar
  ramp?: RGBA[] // shading: cores em ordem (escuro → claro, ou como na paleta)
  rampStep?: 1 | -1
}

function blendOver(dst: Uint8ClampedArray, i: number, c: RGBA) {
  const sa = c[3] / 255
  const da = dst[i + 3] / 255
  const ra = sa + da * (1 - sa)
  if (ra <= 0) {
    dst[i] = dst[i + 1] = dst[i + 2] = dst[i + 3] = 0
    return
  }
  for (let k = 0; k < 3; k++) dst[i + k] = Math.round((c[k] * sa + dst[i + k] * da * (1 - sa)) / ra)
  dst[i + 3] = Math.round(ra * 255)
}

/** Aplica o ink nos pixels (índices y*w+x) lendo de `base` e escrevendo em `out` (que começa como cópia de base). */
export function applyInk(base: Uint8ClampedArray, out: Uint8ClampedArray, pixels: Iterable<number>, o: InkOpts) {
  const ramp = o.ramp ?? []
  for (const p of pixels) {
    const i = p * 4
    out[i] = base[i]
    out[i + 1] = base[i + 1]
    out[i + 2] = base[i + 2]
    out[i + 3] = base[i + 3]
    const c = o.color
    if (o.ink === 'shading') {
      if (!base[i + 3]) continue
      const k = ramp.findIndex((r) => r[0] === base[i] && r[1] === base[i + 1] && r[2] === base[i + 2])
      if (k < 0) continue
      const j = Math.max(0, Math.min(ramp.length - 1, k + (o.rampStep ?? 1)))
      out[i] = ramp[j][0]
      out[i + 1] = ramp[j][1]
      out[i + 2] = ramp[j][2]
      continue
    }
    if (c === null || (o.ink === 'simple' && c[3] === 0)) {
      out[i] = out[i + 1] = out[i + 2] = out[i + 3] = 0
      continue
    }
    switch (o.ink) {
      case 'copy':
        out.set(c, i)
        break
      case 'lock_alpha':
        if (base[i + 3]) {
          out[i] = c[0]
          out[i + 1] = c[1]
          out[i + 2] = c[2]
        }
        break
      case 'alpha':
        blendOver(out, i, c)
        break
      default: // simple
        if (c[3] === 255) out.set(c, i)
        else blendOver(out, i, c)
    }
  }
}

// ---------------------------------------------------------------- traço → pixels

export type StrokeOpts = {
  w: number; h: number; brush: BrushShape; size: number; symmetry: Symmetry
  /** modo tiled: o traço que sai por uma borda volta pela outra */
  wrap?: 'none' | 'x' | 'y' | 'both'
}

/** Pontos do caminho → índices de pixel (pincel + simetria + recorte ao canvas). */
export function coverage(path: Pt[], o: StrokeOpts, useBrush = true): Set<number> {
  const offs = useBrush ? brushOffsets(o.brush, o.size) : ([[0, 0]] as Pt[])
  const stamped: Pt[] = []
  for (const [x, y] of path) for (const [dx, dy] of offs) stamped.push([x + dx, y + dy])
  const set = new Set<number>()
  const wx = o.wrap === 'x' || o.wrap === 'both'
  const wy = o.wrap === 'y' || o.wrap === 'both'
  for (let [x, y] of mirror(stamped, o.symmetry)) {
    if (wx) x = ((x % o.w) + o.w) % o.w
    if (wy) y = ((y % o.h) + o.h) % o.h
    if (x >= 0 && y >= 0 && x < o.w && y < o.h) set.add(y * o.w + x)
  }
  return set
}

// ---------------------------------------------------------------- balde

export type FillOpts = { contiguous: boolean; tolerance: number; diagonal?: boolean }

const dist = (d: Uint8ClampedArray, i: number, r: number, g: number, b: number, a: number) => {
  // transparente conta como uma cor só (ignora o RGB escondido)
  if (d[i + 3] === 0 && a === 0) return 0
  return Math.max(Math.abs(d[i] - r), Math.abs(d[i + 1] - g), Math.abs(d[i + 2] - b), Math.abs(d[i + 3] - a))
}

/** Pixels a pintar pelo balde. `sample` é onde se compara a cor (a camada ou a composição de todas). */
export function fillRegion(sample: Uint8ClampedArray, w: number, h: number, x: number, y: number, o: FillOpts): Set<number> {
  const out = new Set<number>()
  if (x < 0 || y < 0 || x >= w || y >= h) return out
  const s = (y * w + x) * 4
  const [r, g, b, a] = [sample[s], sample[s + 1], sample[s + 2], sample[s + 3]]
  const match = (p: number) => dist(sample, p * 4, r, g, b, a) <= o.tolerance
  if (!o.contiguous) {
    for (let p = 0; p < w * h; p++) if (match(p)) out.add(p)
    return out
  }
  const seen = new Uint8Array(w * h)
  const stack = [y * w + x]
  seen[stack[0]] = 1
  while (stack.length) {
    const p = stack.pop()!
    if (!match(p)) continue
    out.add(p)
    const px = p % w
    const py = (p / w) | 0
    const nb: [number, number][] = [[px - 1, py], [px + 1, py], [px, py - 1], [px, py + 1]]
    if (o.diagonal) nb.push([px - 1, py - 1], [px + 1, py - 1], [px - 1, py + 1], [px + 1, py + 1])
    for (const [nx, ny] of nb) {
      if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue
      const q = ny * w + nx
      if (!seen[q]) {
        seen[q] = 1
        stack.push(q)
      }
    }
  }
  return out
}

// ---------------------------------------------------------------- gradiente

export type GradientOpts = { kind: 'linear' | 'radial'; dither: 'none' | 'bayer2' | 'bayer4' | 'bayer8'; from: RGBA; to: RGBA }

const BAYER: Record<string, number[][]> = {
  bayer2: [[0, 2], [3, 1]],
  bayer4: [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
  bayer8: (() => {
    const m: number[][] = []
    const b4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
    for (let y = 0; y < 8; y++) {
      m.push([])
      for (let x = 0; x < 8; x++) m[y].push(4 * b4[y % 4][x % 4] + [[0, 2], [3, 1]][(y / 4) | 0][(x / 4) | 0])
    }
    return m
  })(),
}

/** Gradiente de a até b nos pixels dados. Com dithering, cada pixel recebe só uma das duas cores (pixel art). */
export function gradientInto(out: Uint8ClampedArray, base: Uint8ClampedArray, w: number, pixels: Iterable<number>, a: Pt, b: Pt, o: GradientOpts) {
  const vx = b[0] - a[0]
  const vy = b[1] - a[1]
  const len2 = vx * vx + vy * vy || 1
  const len = Math.sqrt(len2)
  const m = o.dither !== 'none' ? BAYER[o.dither] : null
  const n = m ? m.length * m.length : 1
  for (const p of pixels) {
    const x = p % w
    const y = (p / w) | 0
    const dx = x - a[0]
    const dy = y - a[1]
    let t = o.kind === 'linear' ? (dx * vx + dy * vy) / len2 : Math.hypot(dx, dy) / len
    t = Math.max(0, Math.min(1, t))
    let c: RGBA
    if (m) {
      const threshold = (m[y % m.length][x % m.length] + 0.5) / n
      c = t > threshold ? o.to : o.from
    } else
      c = [0, 1, 2, 3].map((k) => Math.round(o.from[k] + (o.to[k] - o.from[k]) * t)) as RGBA
    const i = p * 4
    out[i] = base[i]
    out[i + 1] = base[i + 1]
    out[i + 2] = base[i + 2]
    out[i + 3] = base[i + 3]
    if (c[3] === 255) out.set(c, i)
    else blendOver(out, i, c)
  }
}

export function allPixels(w: number, h: number): number[] {
  return Array.from({ length: w * h }, (_, i) => i)
}

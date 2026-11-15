// Cor para o editor de sprite (Fase 4): conversões (HSV, OKLab, luminância), paleta mais próxima, quantização com
// dithering ordenado, extração/redução de paleta, rampas, ordenação e ajustes (HSV, brilho/contraste, inverter,
// dessaturar, posterizar, substituir cor, contorno, sombra, gradient map). Tudo puro: recebe RGBA e devolve RGBA novo.
import type { RGBA } from './raster'

export type ColorMode = 'rgb' | 'indexed' | 'gray'

// ---------------------------------------------------------------- conversões

export function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  const R = r / 255, G = g / 255, B = b / 255
  const max = Math.max(R, G, B)
  const min = Math.min(R, G, B)
  const d = max - min
  let h = 0
  if (d) {
    if (max === R) h = ((G - B) / d) % 6
    else if (max === G) h = (B - R) / d + 2
    else h = (R - G) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return [h, max ? d / max : 0, max]
}

export function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  h = ((h % 360) + 360) % 360
  const c = v * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = v - c
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)]
}

/** Luminância perceptual (Rec. 601), 0..255. */
export const luma = (r: number, g: number, b: number) => Math.round(0.299 * r + 0.587 * g + 0.114 * b)

const lin = (c: number) => {
  c /= 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}
const gam = (c: number) => Math.round(255 * Math.min(1, Math.max(0, c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)))

export function rgbToOklab(r: number, g: number, b: number): [number, number, number] {
  const R = lin(r), G = lin(g), B = lin(b)
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B)
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B)
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B)
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s]
}

export function oklabToRgb(L: number, a: number, b: number): [number, number, number] {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  return [gam(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s), gam(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    gam(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)]
}

export const hex = ([r, g, b, a]: RGBA) =>
  `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}${a === 255 ? '' : a.toString(16).padStart(2, '0')}`

export function parseHex(h: string): RGBA | null {
  const m = h.trim().replace(/^#|^0x/i, '')
  if (!/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(m)) return null
  return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16), m.length === 8 ? parseInt(m.slice(6, 8), 16) : 255]
}

// ---------------------------------------------------------------- paleta mais próxima

/** Índice da cor mais próxima (OKLab; cores transparentes só casam com transparente). Com cache por cor. */
export function makeNearest(palette: RGBA[]) {
  const labs = palette.map(([r, g, b]) => rgbToOklab(r, g, b))
  const transparent = palette.findIndex((c) => c[3] === 0)
  const cache = new Map<number, number>()
  return (r: number, g: number, b: number, a: number): number => {
    if (a === 0) return transparent
    const key = ((r << 16) | (g << 8) | b) >>> 0
    const hit = cache.get(key)
    if (hit !== undefined) return hit
    const [L, A, B] = rgbToOklab(r, g, b)
    let best = -1
    let bd = Infinity
    for (let i = 0; i < labs.length; i++) {
      if (palette[i][3] === 0) continue
      const d = (labs[i][0] - L) ** 2 + (labs[i][1] - A) ** 2 + (labs[i][2] - B) ** 2
      if (d < bd) {
        bd = d
        best = i
      }
    }
    cache.set(key, best)
    return best
  }
}

const BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]

/**
 * Leva os pixels para a paleta. Alfa vira binário (≥128 opaco), como no modo indexado do Aseprite.
 * `dither`: espalha o erro com a matriz de Bayer 4×4 (escolhe entre as duas cores mais próximas).
 */
export function quantize(data: Uint8ClampedArray, w: number, palette: RGBA[], dither = false, mask?: Uint8Array | null): Uint8ClampedArray {
  const out = data.slice()
  const opaque = palette.filter((c) => c[3] > 0)
  if (!opaque.length) return out
  const nearest = makeNearest(palette)
  const labs = palette.map(([r, g, b]) => rgbToOklab(r, g, b))
  for (let p = 0; p < data.length / 4; p++) {
    if (mask && !mask[p]) continue
    const i = p * 4
    if (data[i + 3] < 128) {
      out[i] = out[i + 1] = out[i + 2] = out[i + 3] = 0
      continue
    }
    let k = nearest(data[i], data[i + 1], data[i + 2], 255)
    if (dither) {
      // segunda mais próxima e a posição do pixel entre as duas
      const [L, A, B] = rgbToOklab(data[i], data[i + 1], data[i + 2])
      let k2 = -1
      let d2 = Infinity
      for (let j = 0; j < palette.length; j++) {
        if (j === k || palette[j][3] === 0) continue
        const d = (labs[j][0] - L) ** 2 + (labs[j][1] - A) ** 2 + (labs[j][2] - B) ** 2
        if (d < d2) {
          d2 = d
          k2 = j
        }
      }
      if (k2 >= 0) {
        const d1 = Math.sqrt((labs[k][0] - L) ** 2 + (labs[k][1] - A) ** 2 + (labs[k][2] - B) ** 2)
        const t = d1 / (d1 + Math.sqrt(d2) || 1)
        const x = p % w
        const y = (p / w) | 0
        if (t > (BAYER4[y % 4][x % 4] + 0.5) / 16) k = k2
      }
    }
    const c = palette[k]
    out[i] = c[0]
    out[i + 1] = c[1]
    out[i + 2] = c[2]
    out[i + 3] = 255
  }
  return out
}

export function toGray(data: Uint8ClampedArray, mask?: Uint8Array | null): Uint8ClampedArray {
  const out = data.slice()
  for (let p = 0; p < data.length / 4; p++) {
    if (mask && !mask[p]) continue
    const i = p * 4
    out[i] = out[i + 1] = out[i + 2] = luma(data[i], data[i + 1], data[i + 2])
  }
  return out
}

// ---------------------------------------------------------------- paletas

/** Cores distintas na ordem em que aparecem (até `max`). */
export function uniqueColors(datas: Uint8ClampedArray[], max = 256): RGBA[] {
  const seen = new Set<number>()
  const out: RGBA[] = []
  for (const d of datas)
    for (let i = 0; i < d.length; i += 4) {
      if (!d[i + 3]) continue
      const k = ((d[i] << 24) | (d[i + 1] << 16) | (d[i + 2] << 8) | d[i + 3]) >>> 0
      if (seen.has(k)) continue
      seen.add(k)
      out.push([d[i], d[i + 1], d[i + 2], d[i + 3]])
      if (out.length >= max) return out
    }
  return out
}

/** Reduz as cores do sprite a `n` (median cut em OKLab, ponderado pela frequência). */
export function reducePalette(datas: Uint8ClampedArray[], n: number): RGBA[] {
  const counts = new Map<number, number>()
  for (const d of datas)
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 128) continue
      const k = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2]
      counts.set(k, (counts.get(k) ?? 0) + 1)
    }
  type Item = { rgb: [number, number, number]; lab: [number, number, number]; n: number }
  const items: Item[] = [...counts].map(([k, c]) => {
    const rgb: [number, number, number] = [(k >> 16) & 255, (k >> 8) & 255, k & 255]
    return { rgb, lab: rgbToOklab(...rgb), n: c }
  })
  if (items.length <= n) return items.map((it) => [...it.rgb, 255] as RGBA)
  let boxes: Item[][] = [items]
  while (boxes.length < n) {
    // divide a caixa com maior (variação × população) no eixo mais longo
    let bi = -1
    let bestScore = -1
    let axis = 0
    boxes.forEach((b, i) => {
      if (b.length < 2) return
      for (let a = 0; a < 3; a++) {
        const vals = b.map((it) => it.lab[a])
        const range = Math.max(...vals) - Math.min(...vals)
        const pop = b.reduce((s, it) => s + it.n, 0)
        const score = range * Math.sqrt(pop)
        if (score > bestScore) {
          bestScore = score
          bi = i
          axis = a
        }
      }
    })
    if (bi < 0) break
    const box = [...boxes[bi]].sort((p, q) => p.lab[axis] - q.lab[axis])
    const total = box.reduce((s, it) => s + it.n, 0)
    let acc = 0
    let cut = 1
    for (; cut < box.length; cut++) {
      acc += box[cut - 1].n
      if (acc >= total / 2) break
    }
    boxes = [...boxes.slice(0, bi), box.slice(0, cut), box.slice(cut), ...boxes.slice(bi + 1)]
  }
  return boxes.map((b) => {
    const tot = b.reduce((s, it) => s + it.n, 0)
    const L = b.reduce((s, it) => s + it.lab[0] * it.n, 0) / tot
    const A = b.reduce((s, it) => s + it.lab[1] * it.n, 0) / tot
    const B = b.reduce((s, it) => s + it.lab[2] * it.n, 0) / tot
    return [...oklabToRgb(L, A, B), 255] as RGBA
  })
}

/** Rampa de `steps` cores entre a e b (interpolação em OKLab, inclui as pontas). */
export function ramp(a: RGBA, b: RGBA, steps: number): RGBA[] {
  const la = rgbToOklab(a[0], a[1], a[2])
  const lb = rgbToOklab(b[0], b[1], b[2])
  return Array.from({ length: Math.max(2, steps) }, (_, i) => {
    const t = i / (Math.max(2, steps) - 1)
    const rgb = oklabToRgb(la[0] + (lb[0] - la[0]) * t, la[1] + (lb[1] - la[1]) * t, la[2] + (lb[2] - la[2]) * t)
    return [...rgb, Math.round(a[3] + (b[3] - a[3]) * t)] as RGBA
  })
}

export type SortKey = 'hue' | 'luma' | 'saturation'
export function sortPalette(p: RGBA[], key: SortKey): RGBA[] {
  const k = (c: RGBA) => {
    const [h, s, v] = rgbToHsv(c[0], c[1], c[2])
    return key === 'hue' ? (s < 0.08 ? 1000 + v : h * 10 + v) : key === 'luma' ? luma(c[0], c[1], c[2]) : s
  }
  return [...p].sort((a, b) => (a[3] === 0 ? -1 : b[3] === 0 ? 1 : k(a) - k(b)))
}

// ---------------------------------------------------------------- ajustes

export type Adjust =
  | { kind: 'hsv'; hue: number; sat: number; val: number } // hue em graus; sat/val em -100..100
  | { kind: 'brightness'; brightness: number; contrast: number } // -100..100
  | { kind: 'invert' }
  | { kind: 'desaturate' }
  | { kind: 'posterize'; levels: number }
  | { kind: 'replace'; from: RGBA; to: RGBA; tolerance: number }
  | { kind: 'outline'; color: RGBA; place: 'outside' | 'inside'; diagonal: boolean }
  | { kind: 'shadow'; color: RGBA; dx: number; dy: number }
  | { kind: 'gradient_map'; colors: RGBA[] }
  | { kind: 'fit_palette'; palette: RGBA[]; dither: boolean }

export const ADJUST_TITLES: Record<Adjust['kind'], string> = {
  hsv: 'Matiz / saturação / brilho', brightness: 'Brilho e contraste', invert: 'Inverter cores', desaturate: 'Dessaturar',
  posterize: 'Posterizar', replace: 'Substituir cor', outline: 'Contorno', shadow: 'Sombra projetada', gradient_map: 'Gradient map',
  fit_palette: 'Ajustar à paleta',
}

const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)))

/** Aplica o ajuste a uma imagem w×h; com `mask`, só nos pixels selecionados (contorno/sombra só caem dentro dela). */
export function adjust(data: Uint8ClampedArray, w: number, h: number, a: Adjust, mask?: Uint8Array | null): Uint8ClampedArray {
  if (a.kind === 'fit_palette') return quantize(data, w, a.palette, a.dither, mask)
  const out = data.slice()
  const n = w * h
  const sel = (p: number) => !mask || mask[p]
  if (a.kind === 'outline' || a.kind === 'shadow') {
    const op = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && data[(y * w + x) * 4 + 3] > 0
    for (let p = 0; p < n; p++) {
      if (!sel(p)) continue
      const x = p % w
      const y = (p / w) | 0
      const i = p * 4
      if (a.kind === 'shadow') {
        if (data[i + 3] || !op(x - a.dx, y - a.dy)) continue
        out.set(a.color, i)
        continue
      }
      const nb = a.diagonal
        ? [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]
        : [[1, 0], [-1, 0], [0, 1], [0, -1]]
      if (a.place === 'outside' && !data[i + 3] && nb.some(([dx, dy]) => op(x + dx, y + dy))) out.set(a.color, i)
      if (a.place === 'inside' && data[i + 3] && nb.some(([dx, dy]) => !op(x + dx, y + dy))) out.set(a.color, i)
    }
    return out
  }
  const map = a.kind === 'gradient_map' && a.colors.length ? a.colors : null
  for (let p = 0; p < n; p++) {
    if (!sel(p)) continue
    const i = p * 4
    if (!data[i + 3]) continue
    let r = data[i], g = data[i + 1], b = data[i + 2]
    switch (a.kind) {
      case 'hsv': {
        const [hh, s, v] = rgbToHsv(r, g, b)
        const s2 = Math.max(0, Math.min(1, a.sat >= 0 ? s + (1 - s) * (a.sat / 100) : s * (1 + a.sat / 100)))
        const v2 = Math.max(0, Math.min(1, a.val >= 0 ? v + (1 - v) * (a.val / 100) : v * (1 + a.val / 100)))
        ;[r, g, b] = hsvToRgb(hh + a.hue, s2, v2)
        break
      }
      case 'brightness': {
        const k = (259 * (a.contrast * 2.55 + 255)) / (255 * (259 - a.contrast * 2.55))
        const f = (c: number) => clamp(k * (c - 128) + 128 + a.brightness * 2.55)
        ;[r, g, b] = [f(r), f(g), f(b)]
        break
      }
      case 'invert':
        ;[r, g, b] = [255 - r, 255 - g, 255 - b]
        break
      case 'desaturate':
        r = g = b = luma(r, g, b)
        break
      case 'posterize': {
        const lv = Math.max(2, a.levels)
        const f = (c: number) => clamp(Math.round((c / 255) * (lv - 1)) * (255 / (lv - 1)))
        ;[r, g, b] = [f(r), f(g), f(b)]
        break
      }
      case 'replace': {
        const d = Math.max(Math.abs(r - a.from[0]), Math.abs(g - a.from[1]), Math.abs(b - a.from[2]), Math.abs(data[i + 3] - a.from[3]))
        if (d <= a.tolerance) {
          out.set(a.to, i)
          continue
        }
        break
      }
      case 'gradient_map': {
        if (!map) break
        const t = luma(r, g, b) / 255
        const c = map[Math.min(map.length - 1, Math.round(t * (map.length - 1)))]
        ;[r, g, b] = [c[0], c[1], c[2]]
        break
      }
    }
    out[i] = r
    out[i + 1] = g
    out[i + 2] = b
  }
  return out
}

/** Troca uma cor exata por outra (editar uma entrada da paleta no modo indexado). */
export function remapColor(data: Uint8ClampedArray, from: RGBA, to: RGBA): Uint8ClampedArray {
  let changed = false
  const out = data.slice()
  for (let i = 0; i < data.length; i += 4)
    if (data[i + 3] && data[i] === from[0] && data[i + 1] === from[1] && data[i + 2] === from[2] && data[i + 3] === from[3]) {
      out.set(to, i)
      changed = true
    }
  return changed ? out : data
}

// Lógica pura do Pixel Studio: grade <-> PixelMap, contorno automático, sombra do lado L,
// texto do mapa e conversão para ImageData. Sem React, sem DOM (exceto ImageData).
import type { PixelMap, PixelPalette } from '@/core/api'

/** Cor empacotada 0xRRGGBB; TRANSPARENT = -1. */
export const TRANSPARENT = -1

export type Grid = { w: number; h: number; px: Int32Array }

export const MAP_FORMAT = 'asset-forge-pixelmap/1' as const
export const DEFAULT_SHADE_L = 0.78
export const DEFAULT_OUTLINE = '#0b0a12'

// ---------- cores ----------

export function normHex(c: string): string {
  let s = c.trim().toLowerCase()
  if (!s.startsWith('#')) s = '#' + s
  if (/^#[0-9a-f]{3}$/.test(s)) s = '#' + [...s.slice(1)].map((ch) => ch + ch).join('')
  return s
}

export function hexToInt(c: string): number {
  const s = normHex(c)
  if (!/^#[0-9a-f]{6}$/.test(s)) throw new Error(`cor inválida: ${c}`)
  return parseInt(s.slice(1), 16)
}

export function intToHex(v: number): string {
  return '#' + v.toString(16).padStart(6, '0')
}

export const rgbOf = (v: number): [number, number, number] => [(v >> 16) & 255, (v >> 8) & 255, v & 255]

/** Cores permitidas: core ∪ grupo escolhido (ordem: core primeiro, sem duplicatas). */
export function allowedColors(palette: PixelPalette, group: string | null): number[] {
  const list = [...palette.core, ...(group && palette.groups[group] ? palette.groups[group].cores : [])]
  const out: number[] = []
  for (const c of list) {
    try {
      const v = hexToInt(c)
      if (!out.includes(v)) out.push(v)
    } catch {
      /* ignora cor malformada no config */
    }
  }
  return out
}

/** Distância perceptual simples (RGB ponderado). */
export function colorDist(a: number, b: number): number {
  const [r1, g1, b1] = rgbOf(a)
  const [r2, g2, b2] = rgbOf(b)
  const rm = (r1 + r2) / 2
  const dr = r1 - r2, dg = g1 - g2, db = b1 - b2
  return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db
}

export function nearestColor(v: number, palette: number[]): number {
  let best = palette[0] ?? v
  let bd = Infinity
  for (const p of palette) {
    const d = colorDist(v, p)
    if (d < bd) {
      bd = d
      best = p
    }
  }
  return best
}

// ---------- legenda (cor -> caractere) ----------

const CHAR_POOL = 'kabcdefghijlmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#@$%&*+=-~^!?<>/|:;,_(){}[]'

export type Legend = { charOf: Map<number, string>; colorOf: Map<string, number> }

/**
 * Atribui um caractere estável a cada cor: primeiro os da legenda do mapa carregado,
 * depois a cor do contorno ganha 'k' (se livre), depois o restante da paleta (core e todos os grupos,
 * para que o caractere não mude ao trocar de grupo).
 */
export function buildLegend(palette: PixelPalette, mapLegend: Record<string, string> | undefined, outlineColor: string): Legend {
  const charOf = new Map<number, string>()
  const colorOf = new Map<string, number>()
  const put = (ch: string, v: number) => {
    if (charOf.has(v) || colorOf.has(ch) || ch === '.' || ch.length !== 1) return false
    charOf.set(v, ch)
    colorOf.set(ch, v)
    return true
  }
  for (const [ch, c] of Object.entries(mapLegend ?? {})) {
    try {
      put(ch, hexToInt(c))
    } catch {
      /* cor inválida na legenda: ignorada */
    }
  }
  try {
    put('k', hexToInt(outlineColor))
  } catch {
    /* sem contorno */
  }
  const all = [...palette.core, ...Object.values(palette.groups).flatMap((g) => g.cores)]
  let pool = 0
  let extra = 0xc0
  for (const c of all) {
    let v: number
    try {
      v = hexToInt(c)
    } catch {
      continue
    }
    if (charOf.has(v)) continue
    let done = false
    while (!done && pool < CHAR_POOL.length) done = put(CHAR_POOL[pool++], v)
    while (!done) done = put(String.fromCharCode(extra++), v)
  }
  return { charOf, colorOf }
}

/** Garante caractere para uma cor (ex.: cor fora da paleta vinda de importação). Muta a legenda. */
export function ensureChar(legend: Legend, v: number): string {
  const have = legend.charOf.get(v)
  if (have) return have
  for (const ch of CHAR_POOL) {
    if (!legend.colorOf.has(ch)) {
      legend.charOf.set(v, ch)
      legend.colorOf.set(ch, v)
      return ch
    }
  }
  let code = 0xc0
  while (legend.colorOf.has(String.fromCharCode(code))) code++
  const ch = String.fromCharCode(code)
  legend.charOf.set(v, ch)
  legend.colorOf.set(ch, v)
  return ch
}

// ---------- grade ----------

export function emptyGrid(w: number, h: number): Grid {
  return { w, h, px: new Int32Array(w * h).fill(TRANSPARENT) }
}

export const cloneGrid = (g: Grid): Grid => ({ w: g.w, h: g.h, px: g.px.slice() })

export const getPx = (g: Grid, x: number, y: number) => (x < 0 || y < 0 || x >= g.w || y >= g.h ? TRANSPARENT : g.px[y * g.w + x])

export function gridsEqual(a: Grid, b: Grid): boolean {
  if (a.w !== b.w || a.h !== b.h) return false
  for (let i = 0; i < a.px.length; i++) if (a.px[i] !== b.px[i]) return false
  return true
}

export function usedColors(g: Grid): number[] {
  const s = new Set<number>()
  for (const v of g.px) if (v !== TRANSPARENT) s.add(v)
  return [...s]
}

/** PixelMap -> grade. Lança Error com a linha/coluna em caso de inconsistência. */
export function mapToGrid(map: PixelMap): Grid {
  const { width: w, height: h } = map
  if (!Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0) throw new Error(`dimensões inválidas: ${w}×${h}`)
  if (map.rows.length !== h) throw new Error(`esperadas ${h} linhas, há ${map.rows.length}`)
  const lut = new Map<string, number>()
  for (const [ch, c] of Object.entries(map.legend)) lut.set(ch, hexToInt(c))
  const g = emptyGrid(w, h)
  map.rows.forEach((row, y) => {
    const chars = [...row]
    if (chars.length !== w) throw new Error(`linha ${y + 1}: esperados ${w} caracteres, há ${chars.length}`)
    chars.forEach((ch, x) => {
      if (ch === '.') return
      const v = lut.get(ch)
      if (v === undefined) throw new Error(`linha ${y + 1}, coluna ${x + 1}: caractere '${ch}' sem legenda`)
      g.px[y * w + x] = v
    })
  })
  return g
}

/** Grade -> PixelMap (legenda só com as cores usadas, na ordem do caractere). */
export function gridToMap(g: Grid, legend: Legend, meta: Pick<PixelMap, 'outline' | 'side' | 'shade_L'>): PixelMap {
  const used = new Map<string, string>()
  const rows: string[] = []
  for (let y = 0; y < g.h; y++) {
    let row = ''
    for (let x = 0; x < g.w; x++) {
      const v = g.px[y * g.w + x]
      if (v === TRANSPARENT) row += '.'
      else {
        const ch = ensureChar(legend, v)
        used.set(ch, intToHex(v))
        row += ch
      }
    }
    rows.push(row)
  }
  const legendObj: Record<string, string> = {}
  for (const ch of [...used.keys()].sort()) legendObj[ch] = used.get(ch)!
  return { format: MAP_FORMAT, width: g.w, height: g.h, legend: legendObj, rows, outline: { ...meta.outline }, side: meta.side, shade_L: meta.shade_L }
}

export function blankMap(w: number, h: number, outlineColor = DEFAULT_OUTLINE): PixelMap {
  return {
    format: MAP_FORMAT, width: w, height: h, legend: {}, rows: Array.from({ length: h }, () => '.'.repeat(w)),
    outline: { auto: true, color: normHex(outlineColor) }, side: null, shade_L: DEFAULT_SHADE_L,
  }
}

/** Aceita JSON vindo do servidor e completa campos opcionais. */
export function normalizeMap(raw: Partial<PixelMap> & { width: number; height: number; rows: string[] }): PixelMap {
  return {
    format: MAP_FORMAT,
    width: raw.width,
    height: raw.height,
    legend: raw.legend ?? {},
    rows: raw.rows,
    outline: { auto: raw.outline?.auto ?? true, color: normHex(raw.outline?.color ?? DEFAULT_OUTLINE) },
    side: raw.side ?? null,
    shade_L: typeof raw.shade_L === 'number' ? raw.shade_L : DEFAULT_SHADE_L,
  }
}

// ---------- renderização ----------

/** Contorno automático: todo pixel transparente 4-vizinho de um preenchido vira a cor do contorno. */
export function applyOutline(g: Grid, color: number): Grid {
  const out = cloneGrid(g)
  const { w, h, px } = g
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const i = y * w + x
      if (px[i] !== TRANSPARENT) continue
      if ((x > 0 && px[i - 1] !== TRANSPARENT) || (x < w - 1 && px[i + 1] !== TRANSPARENT) || (y > 0 && px[i - w] !== TRANSPARENT) || (y < h - 1 && px[i + w] !== TRANSPARENT))
        out.px[i] = color
    }
  return out
}

/** Variante _L: escurece tudo (fator shade) exceto a cor do contorno. */
export function shadeGrid(g: Grid, factor: number, outlineColor: number): Grid {
  const out = cloneGrid(g)
  for (let i = 0; i < out.px.length; i++) {
    const v = out.px[i]
    if (v === TRANSPARENT || v === outlineColor) continue
    const [r, gg, b] = rgbOf(v)
    out.px[i] = (Math.round(r * factor) << 16) | (Math.round(gg * factor) << 8) | Math.round(b * factor)
  }
  return out
}

/** Render final (como o servidor gera sprite.png): contorno automático se ligado, sombra se lado L. */
export function renderGrid(g: Grid, opts: { outline: number | null; shade: number | null; outlineColor: number }): Grid {
  let r = opts.outline !== null ? applyOutline(g, opts.outline) : g
  if (opts.shade !== null) r = shadeGrid(r, opts.shade, opts.outlineColor)
  return r
}

export function gridToRGBA(g: Grid): Uint8ClampedArray<ArrayBuffer> {
  const data = new Uint8ClampedArray(new ArrayBuffer(g.w * g.h * 4))
  for (let i = 0; i < g.px.length; i++) {
    const v = g.px[i]
    if (v === TRANSPARENT) continue
    data[i * 4] = (v >> 16) & 255
    data[i * 4 + 1] = (v >> 8) & 255
    data[i * 4 + 2] = v & 255
    data[i * 4 + 3] = 255
  }
  return data
}

export function gridToImageData(g: Grid): ImageData {
  return new ImageData(gridToRGBA(g), g.w, g.h)
}

// ---------- texto do mapa ----------

/** Texto editável: legenda ("c #rrggbb") e depois as linhas. Linhas iniciadas por "//" são comentários. */
export function gridToText(g: Grid, legend: Legend): string {
  const used = new Map<string, number>()
  const rows: string[] = []
  for (let y = 0; y < g.h; y++) {
    let row = ''
    for (let x = 0; x < g.w; x++) {
      const v = g.px[y * g.w + x]
      if (v === TRANSPARENT) row += '.'
      else {
        const ch = ensureChar(legend, v)
        used.set(ch, v)
        row += ch
      }
    }
    rows.push(row)
  }
  const leg = [...used.keys()].sort().map((ch) => `${ch} ${intToHex(used.get(ch)!)}`)
  return ['// legenda', ...leg, `// linhas (${g.w}×${g.h})`, ...rows].join('\n')
}

export type TextParse = { ok: true; grid: Grid } | { ok: false; error: string; row?: number; col?: number; line: number }

/**
 * Texto -> grade. Caracteres valem pela legenda do texto e, se ausentes nela, pela legenda da paleta.
 * Cores precisam estar em `allowed` (ou já existir na grade atual, `tolerated`).
 */
export function textToGrid(text: string, w: number, h: number, legend: Legend, allowed: Set<number>, tolerated: Set<number>): TextParse {
  const local = new Map<string, number>()
  const rows: { s: string; line: number }[] = []
  const lines = text.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim()
    if (!t || t.startsWith('//')) continue
    const m = /^(\S)\s+(#?[0-9a-fA-F]{6})$/.exec(t)
    if (m) {
      const v = hexToInt(m[2])
      if (!allowed.has(v) && !tolerated.has(v)) return { ok: false, error: `cor ${normHex(m[2])} fora da paleta`, line: i + 1 }
      if (m[1] === '.') return { ok: false, error: `'.' é reservado para transparente`, line: i + 1 }
      local.set(m[1], v)
      continue
    }
    if (/\s/.test(t)) return { ok: false, error: `linha não reconhecida (legenda é "c #rrggbb")`, line: i + 1 }
    rows.push({ s: t, line: i + 1 })
  }
  if (rows.length !== h) return { ok: false, error: `esperadas ${h} linhas de pixels, há ${rows.length}`, line: rows[rows.length - 1]?.line ?? 1 }
  const g = emptyGrid(w, h)
  for (let y = 0; y < h; y++) {
    const chars = [...rows[y].s]
    if (chars.length !== w) return { ok: false, error: `esperados ${w} caracteres, há ${chars.length}`, row: y + 1, line: rows[y].line }
    for (let x = 0; x < w; x++) {
      const ch = chars[x]
      if (ch === '.') continue
      const v = local.get(ch) ?? legend.colorOf.get(ch)
      if (v === undefined) return { ok: false, error: `caractere '${ch}' sem legenda`, row: y + 1, col: x + 1, line: rows[y].line }
      if (!allowed.has(v) && !tolerated.has(v)) return { ok: false, error: `'${ch}' = ${intToHex(v)} fora da paleta`, row: y + 1, col: x + 1, line: rows[y].line }
      g.px[y * w + x] = v
    }
  }
  return { ok: true, grid: g }
}

// ---------- spec (origem inferior esquerda -> superior esquerda) ----------

/** Retângulo da área segura em coordenadas de imagem (topo-esquerda). */
export function safeAreaTopLeft(safe: [number, number, number, number], h: number): [number, number, number, number] {
  const [x, y, sw, sh] = safe
  return [x, h - y - sh, sw, sh]
}

/** Pivô como ponto contínuo em coordenadas de imagem (topo-esquerda). */
export function pivotTopLeft(pivot: [number, number], h: number): [number, number] {
  return [pivot[0], h - pivot[1]]
}

// ---------- destaque de checagens ----------

export type Highlight = { pixels: [number, number][]; rects: [number, number, number, number][] }

const isPair = (v: unknown): v is [number, number] => Array.isArray(v) && v.length === 2 && typeof v[0] === 'number' && typeof v[1] === 'number'

/**
 * Extrai pixels/retângulos de `check.data` (coordenadas topo-esquerda da imagem):
 * - pixels/points/islands/pixel: [x,y] ou listas (aninhadas) de [x,y]
 * - bbox/rect: [x0,y0,x1,y1] (fim exclusivo, estilo PIL) ou {x,y,w,h}
 */
export function extractHighlight(data: unknown): Highlight {
  const out: Highlight = { pixels: [], rects: [] }
  const collectPairs = (v: unknown, depth: number) => {
    if (depth > 4) return
    if (isPair(v)) out.pixels.push([Math.floor(v[0]), Math.floor(v[1])])
    else if (Array.isArray(v)) v.forEach((x) => collectPairs(x, depth + 1))
    else if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      if (['x', 'y'].every((k) => typeof o[k] === 'number')) out.pixels.push([o.x as number, o.y as number])
    }
  }
  const rect = (v: unknown) => {
    if (Array.isArray(v) && v.length === 4 && v.every((n) => typeof n === 'number')) {
      const [x0, y0, x1, y1] = v as number[]
      out.rects.push([x0, y0, x1 - x0, y1 - y0])
    } else if (v && typeof v === 'object') {
      const o = v as Record<string, number>
      if (['x', 'y', 'w', 'h'].every((k) => typeof o[k] === 'number')) out.rects.push([o.x, o.y, o.w, o.h])
    }
  }
  const walk = (v: unknown, depth: number) => {
    if (!v || typeof v !== 'object' || depth > 3) return
    if (Array.isArray(v)) {
      v.forEach((x) => walk(x, depth + 1))
      return
    }
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (['pixels', 'points', 'islands', 'pixel', 'coords'].includes(k)) collectPairs(val, 0)
      else if (['bbox', 'rect', 'box'].includes(k)) rect(val)
      else if (val && typeof val === 'object') walk(val, depth + 1)
    }
  }
  walk(data, 0)
  return out
}

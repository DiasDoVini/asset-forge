// Texto em pixel art: fonte bitmap 3×5 embutida (sem dependências) e fontes pixel (Google Fonts, arquivo .ttf/.otf ou
// do sistema) rasterizadas sem antialiasing: o alfa vira 0 ou 255, então só entra a cor escolhida.
import type { RGBA } from './raster'
import type { Img } from './selection'

// 3×5, linhas de cima para baixo (bits 4,2,1 = colunas esquerda→direita)
const G3x5: Record<string, number[]> = {
  A: [2, 5, 7, 5, 5], B: [6, 5, 6, 5, 6], C: [3, 4, 4, 4, 3], D: [6, 5, 5, 5, 6], E: [7, 4, 6, 4, 7], F: [7, 4, 6, 4, 4],
  G: [3, 4, 5, 5, 3], H: [5, 5, 7, 5, 5], I: [7, 2, 2, 2, 7], J: [1, 1, 1, 5, 2], K: [5, 5, 6, 5, 5], L: [4, 4, 4, 4, 7],
  M: [5, 7, 7, 5, 5], N: [6, 5, 5, 5, 5], O: [2, 5, 5, 5, 2], P: [6, 5, 6, 4, 4], Q: [2, 5, 5, 6, 3], R: [6, 5, 6, 5, 5],
  S: [3, 4, 2, 1, 6], T: [7, 2, 2, 2, 2], U: [5, 5, 5, 5, 7], V: [5, 5, 5, 5, 2], W: [5, 5, 7, 7, 5], X: [5, 5, 2, 5, 5],
  Y: [5, 5, 2, 2, 2], Z: [7, 1, 2, 4, 7],
  '0': [7, 5, 5, 5, 7], '1': [2, 6, 2, 2, 7], '2': [6, 1, 2, 4, 7], '3': [6, 1, 2, 1, 6], '4': [5, 5, 7, 1, 1],
  '5': [7, 4, 6, 1, 6], '6': [3, 4, 7, 5, 7], '7': [7, 1, 2, 2, 2], '8': [7, 5, 7, 5, 7], '9': [7, 5, 7, 1, 6],
  ' ': [0, 0, 0, 0, 0], '.': [0, 0, 0, 0, 2], ',': [0, 0, 0, 2, 4], '!': [2, 2, 2, 0, 2], '?': [6, 1, 2, 0, 2],
  ':': [0, 2, 0, 2, 0], ';': [0, 2, 0, 2, 4], '-': [0, 0, 7, 0, 0], '+': [0, 2, 7, 2, 0], '=': [0, 7, 0, 7, 0],
  '/': [1, 1, 2, 4, 4], '(': [1, 2, 2, 2, 1], ')': [4, 2, 2, 2, 4], "'": [2, 2, 0, 0, 0], '"': [5, 5, 0, 0, 0],
  '*': [0, 5, 2, 5, 0], '#': [5, 7, 5, 7, 5], '%': [5, 1, 2, 4, 5], _: [0, 0, 0, 0, 7], '<': [1, 2, 4, 2, 1],
  '>': [4, 2, 1, 2, 4], '[': [6, 4, 4, 4, 6], ']': [3, 1, 1, 1, 3], '&': [2, 5, 2, 5, 3], '@': [7, 5, 7, 4, 3],
  $: [3, 6, 2, 3, 6],
}

export type FontChoice =
  | { kind: 'bitmap' } // 3×5 embutida
  | { kind: 'css'; family: string } // Google Fonts / sistema / arquivo já carregado

export const WEB_PIXEL_FONTS: { family: string; size: number; note: string }[] = [
  { family: 'Silkscreen', size: 8, note: 'caixa alta, 8 px' },
  { family: 'Pixelify Sans', size: 16, note: '16 px' },
  { family: 'Press Start 2P', size: 8, note: 'estilo NES, 8 px' },
  { family: 'VT323', size: 16, note: 'terminal, 16 px' },
  { family: 'Tiny5', size: 8, note: '5 px de altura' },
  { family: 'Jersey 10', size: 10, note: '10 px' },
]

const loaded = new Set<string>()

/** Carrega uma fonte do Google Fonts (só na primeira vez). */
export async function loadWebFont(family: string, size: number): Promise<void> {
  if (!loaded.has(family)) {
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(family).replace(/%20/g, '+')}&display=swap`
    document.head.appendChild(link)
    await new Promise<void>((res, rej) => {
      link.onload = () => res()
      link.onerror = () => rej(new Error(`não consegui baixar a fonte ${family}`))
    })
    loaded.add(family)
  }
  await document.fonts.load(`${size}px "${family}"`)
}

/** Registra uma fonte de arquivo (.ttf/.otf/.woff) e devolve o nome para usar. */
export async function loadFontFile(file: File): Promise<string> {
  const family = `AF ${file.name.replace(/\.[^.]+$/, '')}`
  const face = new FontFace(family, await file.arrayBuffer())
  await face.load()
  document.fonts.add(face)
  return family
}

const stripAccents = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '')

export type TextOpts = { font: FontChoice; size: number; color: RGBA; spacing: number; lineGap: number; scale: number }

/** Rasteriza o texto (várias linhas) numa imagem recortada ao conteúdo. */
export function renderText(text: string, o: TextOpts): Img | null {
  const lines = text.split('\n')
  if (o.font.kind === 'bitmap') {
    const s = Math.max(1, Math.round(o.scale))
    const cw = 3 * s
    const ch = 5 * s
    const adv = cw + Math.max(0, o.spacing) * s + s
    const lineH = ch + Math.max(0, o.lineGap) * s + s
    const w = Math.max(1, ...lines.map((l) => l.length * adv - s))
    const h = Math.max(1, lines.length * lineH - s)
    const data = new Uint8ClampedArray(w * h * 4)
    lines.forEach((line, li) => {
      ;[...stripAccents(line).toUpperCase()].forEach((chr, ci) => {
        const g = G3x5[chr] ?? G3x5['?']
        for (let r = 0; r < 5; r++)
          for (let c = 0; c < 3; c++) {
            if (!(g[r] & (4 >> c))) continue
            for (let dy = 0; dy < s; dy++)
              for (let dx = 0; dx < s; dx++) {
                const x = ci * adv + c * s + dx
                const y = li * lineH + r * s + dy
                if (x < w && y < h) data.set(o.color, (y * w + x) * 4)
              }
          }
      })
    })
    return trim({ w, h, data })
  }
  const c = document.createElement('canvas')
  const g = c.getContext('2d', { willReadFrequently: true })!
  const font = `${o.size}px "${o.font.family}"`
  g.font = font
  const lineH = Math.ceil(o.size * 1.25) + o.lineGap
  const widths = lines.map((l) => Math.ceil(g.measureText(l).width + Math.max(0, l.length - 1) * o.spacing))
  c.width = Math.max(1, ...widths) + 4
  c.height = Math.max(1, lines.length * lineH) + 4
  g.font = font
  g.textBaseline = 'top'
  g.fillStyle = '#fff'
  lines.forEach((line, i) => {
    let x = 2
    if (o.spacing === 0) g.fillText(line, x, 2 + i * lineH)
    else
      for (const chr of line) {
        g.fillText(chr, x, 2 + i * lineH)
        x += g.measureText(chr).width + o.spacing
      }
  })
  const src = g.getImageData(0, 0, c.width, c.height).data
  const data = new Uint8ClampedArray(src.length)
  for (let i = 0; i < src.length; i += 4) if (src[i + 3] >= 128) data.set(o.color, i)
  const img = trim({ w: c.width, h: c.height, data })
  if (!img || o.scale <= 1) return img
  const s = Math.round(o.scale)
  const out = new Uint8ClampedArray(img.w * s * img.h * s * 4)
  for (let y = 0; y < img.h * s; y++)
    for (let x = 0; x < img.w * s; x++) out.set(img.data.subarray(((((y / s) | 0) * img.w) + ((x / s) | 0)) * 4, ((((y / s) | 0) * img.w) + ((x / s) | 0)) * 4 + 4), (y * img.w * s + x) * 4)
  return { w: img.w * s, h: img.h * s, data: out }
}

function trim(img: Img): Img | null {
  let x0 = img.w, y0 = img.h, x1 = -1, y1 = -1
  for (let y = 0; y < img.h; y++)
    for (let x = 0; x < img.w; x++)
      if (img.data[(y * img.w + x) * 4 + 3]) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
  if (x1 < 0) return null
  const w = x1 - x0 + 1
  const h = y1 - y0 + 1
  const data = new Uint8ClampedArray(w * h * 4)
  for (let y = 0; y < h; y++) data.set(img.data.subarray(((y0 + y) * img.w + x0) * 4, ((y0 + y) * img.w + x1 + 1) * 4), y * w * 4)
  return { w, h, data }
}

export const BITMAP_GLYPHS = Object.keys(G3x5)

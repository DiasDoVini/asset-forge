// Importar/exportar paletas: GIMP (.gpl), JASC/Microsoft RIFF (.pal), Lospec (.hex), Paint.NET (.txt),
// Adobe Swatch Exchange (.ase), Aseprite (.aseprite/.ase, paleta do arquivo) e imagem (PNG etc.: cores na ordem).
import type { RGBA } from './raster'

export class PaletteError extends Error {}

const text = (b: ArrayBuffer) => new TextDecoder().decode(b).replace(/^﻿/, '')

export function parseGpl(s: string): RGBA[] {
  if (!/^GIMP Palette/i.test(s.trim())) throw new PaletteError('não é uma paleta GIMP (.gpl)')
  const out: RGBA[] = []
  for (const line of s.split(/\r?\n/).slice(1)) {
    const t = line.trim()
    if (!t || t.startsWith('#') || /^(Name|Columns):/i.test(t)) continue
    const m = t.match(/^(\d+)\s+(\d+)\s+(\d+)/)
    if (m) out.push([+m[1], +m[2], +m[3], 255])
  }
  return out
}

export function parseJascPal(s: string): RGBA[] {
  const lines = s.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (lines[0] !== 'JASC-PAL') throw new PaletteError('não é uma paleta JASC (.pal)')
  const n = parseInt(lines[2], 10)
  return lines.slice(3, 3 + n).map((l) => {
    const [r, g, b, a] = l.split(/\s+/).map(Number)
    return [r, g, b, Number.isFinite(a) ? a : 255] as RGBA
  })
}

/** Microsoft RIFF PAL (binário). */
export function parseRiffPal(b: ArrayBuffer): RGBA[] {
  const dv = new DataView(b)
  const tag = (o: number) => String.fromCharCode(dv.getUint8(o), dv.getUint8(o + 1), dv.getUint8(o + 2), dv.getUint8(o + 3))
  if (tag(0) !== 'RIFF' || tag(8) !== 'PAL ') throw new PaletteError('não é um RIFF PAL')
  let o = 12
  while (o + 8 <= b.byteLength) {
    const id = tag(o)
    const size = dv.getUint32(o + 4, true)
    if (id === 'data') {
      const n = dv.getUint16(o + 10, true)
      return Array.from({ length: n }, (_, i) => [dv.getUint8(o + 12 + i * 4), dv.getUint8(o + 13 + i * 4), dv.getUint8(o + 14 + i * 4), 255] as RGBA)
    }
    o += 8 + size + (size & 1)
  }
  throw new PaletteError('RIFF PAL sem bloco de dados')
}

export function parseHexList(s: string): RGBA[] {
  const out: RGBA[] = []
  for (const line of s.split(/\r?\n/)) {
    const t = line.trim()
    if (!t || t.startsWith(';')) continue
    const m = t.match(/^#?([0-9a-f]{6}|[0-9a-f]{8})$/i)
    if (!m) throw new PaletteError(`linha inválida: ${t.slice(0, 20)}`)
    const h = m[1]
    // Paint.NET (.txt) usa AARRGGBB; Lospec (.hex) usa RRGGBB
    if (h.length === 8) out.push([parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), parseInt(h.slice(6, 8), 16), parseInt(h.slice(0, 2), 16)])
    else out.push([parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 255])
  }
  return out
}

/** Adobe Swatch Exchange (ASEF). Lê cores RGB, CMYK (aproximado), cinza e LAB (aproximado). */
export function parseAdobeAse(b: ArrayBuffer): RGBA[] {
  const dv = new DataView(b)
  if (dv.getUint32(0) !== 0x41534546) throw new PaletteError('não é um Adobe Swatch Exchange')
  const n = dv.getUint32(8)
  let o = 12
  const out: RGBA[] = []
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)))
  for (let k = 0; k < n && o + 6 <= b.byteLength; k++) {
    const type = dv.getUint16(o)
    const len = dv.getUint32(o + 2)
    const start = o + 6
    if (type === 0x0001) {
      const nameLen = dv.getUint16(start)
      let p = start + 2 + nameLen * 2
      const model = String.fromCharCode(dv.getUint8(p), dv.getUint8(p + 1), dv.getUint8(p + 2), dv.getUint8(p + 3)).trim()
      p += 4
      const v = (i: number) => dv.getFloat32(p + i * 4)
      if (model === 'RGB') out.push([f(v(0)), f(v(1)), f(v(2)), 255])
      else if (model === 'Gray') out.push([f(v(0)), f(v(0)), f(v(0)), 255])
      else if (model === 'CMYK') {
        const [c, m, y, kk] = [v(0), v(1), v(2), v(3)]
        out.push([f((1 - c) * (1 - kk)), f((1 - m) * (1 - kk)), f((1 - y) * (1 - kk)), 255])
      } else if (model === 'LAB') {
        // aproximação simples (D50 → sRGB sem adaptação cromática)
        const L = v(0) * 100, A = v(1), B = v(2)
        const fy = (L + 16) / 116, fx = fy + A / 500, fz = fy - B / 200
        const inv = (t: number) => (t ** 3 > 0.008856 ? t ** 3 : (t - 16 / 116) / 7.787)
        const X = 0.9642 * inv(fx), Y = inv(fy), Z = 0.8249 * inv(fz)
        const lin = [3.1339 * X - 1.6169 * Y - 0.4906 * Z, -0.9788 * X + 1.9161 * Y + 0.0335 * Z, 0.0719 * X - 0.229 * Y + 1.4052 * Z]
        const g = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)
        out.push([f(g(lin[0])), f(g(lin[1])), f(g(lin[2])), 255])
      }
    }
    o = start + len
  }
  return out
}

/** Paleta embutida num .aseprite (chunk 0x2019; se não houver, cores distintas dos cels — feito pelo chamador). */
export function parseAsepritePalette(b: ArrayBuffer): RGBA[] {
  const dv = new DataView(b)
  if (b.byteLength < 128 || dv.getUint16(4, true) !== 0xa5e0) throw new PaletteError('não é um arquivo Aseprite')
  const frames = dv.getUint16(6, true)
  let p = 128
  const out: RGBA[] = []
  for (let f = 0; f < frames && p < b.byteLength; f++) {
    const fsize = dv.getUint32(p, true)
    const n = dv.getUint32(p + 12, true) || dv.getUint16(p + 6, true)
    let c = p + 16
    for (let k = 0; k < n; k++) {
      const size = dv.getUint32(c, true)
      const type = dv.getUint16(c + 4, true)
      if (type === 0x2019) {
        const total = dv.getUint32(c + 6, true)
        const first = dv.getUint32(c + 10, true)
        const last = dv.getUint32(c + 14, true)
        let q = c + 26
        while (out.length < total) out.push([0, 0, 0, 255])
        for (let i = first; i <= last; i++) {
          const flags = dv.getUint16(q, true)
          out[i] = [dv.getUint8(q + 2), dv.getUint8(q + 3), dv.getUint8(q + 4), dv.getUint8(q + 5)]
          q += 6
          if (flags & 1) q += 2 + dv.getUint16(q, true)
        }
      }
      c += size
    }
    p += fsize
  }
  return out
}

/** Cores distintas de uma imagem, na ordem de leitura (paletas do Lospec em PNG 1×N, ou um sprite qualquer). */
export async function parseImagePalette(file: Blob, max = 256): Promise<RGBA[]> {
  const bmp = await createImageBitmap(file)
  const c = document.createElement('canvas')
  c.width = bmp.width
  c.height = bmp.height
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.drawImage(bmp, 0, 0)
  const d = g.getImageData(0, 0, c.width, c.height).data
  const seen = new Set<number>()
  const out: RGBA[] = []
  for (let i = 0; i < d.length && out.length < max; i += 4) {
    if (!d[i + 3]) continue
    const k = ((d[i] << 24) | (d[i + 1] << 16) | (d[i + 2] << 8) | d[i + 3]) >>> 0
    if (seen.has(k)) continue
    seen.add(k)
    out.push([d[i], d[i + 1], d[i + 2], d[i + 3]])
  }
  return out
}

/** Lê qualquer formato suportado pelo nome/conteúdo do arquivo. */
export async function readPalette(file: File): Promise<RGBA[]> {
  const name = file.name.toLowerCase()
  const buf = await file.arrayBuffer()
  const head = new Uint8Array(buf.slice(0, 8))
  const magic = String.fromCharCode(...head.slice(0, 4))
  let pal: RGBA[]
  if (magic === 'ASEF') pal = parseAdobeAse(buf)
  else if (buf.byteLength >= 6 && new DataView(buf).getUint16(4, true) === 0xa5e0) pal = parseAsepritePalette(buf)
  else if (magic === 'RIFF') pal = parseRiffPal(buf)
  else if (name.endsWith('.gpl')) pal = parseGpl(text(buf))
  else if (name.endsWith('.pal')) pal = parseJascPal(text(buf))
  else if (name.endsWith('.hex') || name.endsWith('.txt')) pal = parseHexList(text(buf))
  else if (file.type.startsWith('image/') || /\.(png|gif|bmp|webp|jpe?g)$/.test(name)) pal = await parseImagePalette(file)
  else {
    const t = text(buf)
    if (/^GIMP Palette/.test(t)) pal = parseGpl(t)
    else if (t.startsWith('JASC-PAL')) pal = parseJascPal(t)
    else pal = parseHexList(t)
  }
  if (!pal.length) throw new PaletteError('nenhuma cor encontrada no arquivo')
  return pal.slice(0, 256)
}

// ---------------------------------------------------------------- exportar

export type PaletteFormat = 'gpl' | 'pal' | 'hex' | 'txt' | 'ase' | 'png'

const h2 = (v: number) => v.toString(16).padStart(2, '0')

export function writeGpl(p: RGBA[], name = 'Asset Forge'): string {
  return `GIMP Palette\nName: ${name}\nColumns: 8\n#\n` + p.map(([r, g, b]) => `${String(r).padStart(3)} ${String(g).padStart(3)} ${String(b).padStart(3)}\t${h2(r)}${h2(g)}${h2(b)}`).join('\n') + '\n'
}
export const writeJascPal = (p: RGBA[]) => `JASC-PAL\r\n0100\r\n${p.length}\r\n` + p.map(([r, g, b]) => `${r} ${g} ${b}`).join('\r\n') + '\r\n'
export const writeHex = (p: RGBA[]) => p.map(([r, g, b]) => `${h2(r)}${h2(g)}${h2(b)}`).join('\n') + '\n'
export const writePdnTxt = (p: RGBA[]) => '; paint.net Palette File\n; Asset Forge\n' + p.map(([r, g, b, a]) => `${h2(a)}${h2(r)}${h2(g)}${h2(b)}`.toUpperCase()).join('\n') + '\n'

export function writeAdobeAse(p: RGBA[]): Uint8Array {
  const blocks = p.map(([r, g, b], i) => {
    const name = `${i}`
    const body = new DataView(new ArrayBuffer(2 + (name.length + 1) * 2 + 4 + 12 + 2))
    let o = 0
    body.setUint16(o, name.length + 1)
    o += 2
    for (const ch of name) {
      body.setUint16(o, ch.charCodeAt(0))
      o += 2
    }
    body.setUint16(o, 0)
    o += 2
    for (const ch of 'RGB ') body.setUint8(o++, ch.charCodeAt(0))
    for (const v of [r, g, b]) {
      body.setFloat32(o, v / 255)
      o += 4
    }
    body.setUint16(o, 2) // normal
    return new Uint8Array(body.buffer)
  })
  const total = 12 + blocks.reduce((s, b) => s + 6 + b.length, 0)
  const out = new Uint8Array(total)
  const dv = new DataView(out.buffer)
  dv.setUint32(0, 0x41534546)
  dv.setUint16(4, 1)
  dv.setUint16(6, 0)
  dv.setUint32(8, blocks.length)
  let o = 12
  for (const b of blocks) {
    dv.setUint16(o, 0x0001)
    dv.setUint32(o + 2, b.length)
    out.set(b, o + 6)
    o += 6 + b.length
  }
  return out
}

/** PNG com 1 px por cor (formato de troca do Lospec); `cell` > 1 gera quadradinhos para visualizar. */
export async function writePalettePng(p: RGBA[], cell = 1): Promise<Blob> {
  const c = document.createElement('canvas')
  c.width = p.length * cell
  c.height = cell
  const g = c.getContext('2d')!
  p.forEach(([r, gg, b, a], i) => {
    g.fillStyle = `rgba(${r},${gg},${b},${a / 255})`
    g.fillRect(i * cell, 0, cell, cell)
  })
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('falha ao gerar PNG'))), 'image/png'))
}

export async function exportPalette(p: RGBA[], fmt: PaletteFormat, name = 'paleta'): Promise<{ blob: Blob; filename: string }> {
  const txt = (s: string, ext: string) => ({ blob: new Blob([s], { type: 'text/plain' }), filename: `${name}.${ext}` })
  switch (fmt) {
    case 'gpl': return txt(writeGpl(p, name), 'gpl')
    case 'pal': return txt(writeJascPal(p), 'pal')
    case 'hex': return txt(writeHex(p), 'hex')
    case 'txt': return txt(writePdnTxt(p), 'txt')
    case 'ase': return { blob: new Blob([writeAdobeAse(p) as BlobPart], { type: 'application/octet-stream' }), filename: `${name}.ase` }
    case 'png': return { blob: await writePalettePng(p), filename: `${name}.png` }
  }
}

// ---------------------------------------------------------------- paletas prontas

const H = (s: string) => s.split(' ').map((h) => [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 255] as RGBA)

export const PRESETS: { name: string; colors: RGBA[] }[] = [
  { name: 'PICO-8', colors: H('000000 1d2b53 7e2553 008751 ab5236 5f574f c2c3c7 fff1e8 ff004d ffa300 ffec27 00e436 29adff 83769c ff77a8 ffccaa') },
  { name: 'DawnBringer 16', colors: H('140c1c 442434 30346d 4e4a4e 854c30 346524 d04648 757161 597dce d27d2c 8595a1 6daa2c d2aa99 6dc2ca dad45e deeed6') },
  { name: 'DawnBringer 32', colors: H('000000 222034 45283c 663931 8f563b df7126 d9a066 eec39a fbf236 99e550 6abe30 37946e 4b692f 524b24 323c39 3f3f74 306082 5b6ee1 639bff 5fcde4 cbdbfc ffffff 9badb7 847e87 696a6a 595652 76428a ac3232 d95763 d77bba 8f974a 8a6f30') },
  { name: 'Endesga 32', colors: H('be4a2f d77643 ead4aa e4a672 b86f50 733e39 3e2731 a22633 e43b44 f77622 feae34 fee761 63c74d 3e8948 265c42 193c3e 124e89 0099db 2ce8f5 ffffff c0cbdc 8b9bb4 5a6988 3a4466 262b44 181425 ff0044 68386c b55088 f6757a e8b796 c28569') },
  { name: 'Game Boy', colors: H('0f380f 306230 8bac0f 9bbc0f') },
  { name: 'Sweetie 16', colors: H('1a1c2c 5d275d b13e53 ef7d57 ffcd75 a7f070 38b764 257179 29366f 3b5dc9 41a6f6 73eff7 f4f4f4 94b0c2 566c86 333c57') },
  { name: '1-bit', colors: H('000000 ffffff') },
]

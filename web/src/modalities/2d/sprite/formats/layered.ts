// Abrir arquivos de outros editores em camadas: Pixelorama (.pxo), Photoshop (.psd, 8 bits RGB) e Krita (.kra, com
// animação por keyframes). Viram o documento do editor; ao importar na galeria, viram .aseprite e o servidor gera a
// versão (as camadas ficam no .aseprite guardado como fonte).
import { blankDoc, celKey, newCelImage, type Blend, type Layer, type SpriteDoc, type Tag } from '../doc'
import { listZip, readZipEntry, readZipText, type ZipEntry } from './zip'

export const LAYERED_EXTS = ['.pxo', '.psd', '.kra']

export class FormatError extends Error {}

const ext = (name: string) => (name.includes('.') ? '.' + name.split('.').pop()!.toLowerCase() : '')

export const isLayered = (name: string) => LAYERED_EXTS.includes(ext(name))

/** Para enviar ao servidor: .pxo/.psd/.kra viram .aseprite com as camadas. Se não der (PSD 16 bits, KRA CMYK…), PSD e KRA
 *  seguem como vieram (o servidor importa a imagem achatada); .pxo não tem leitor no servidor, então o erro sobe. */
export async function layeredUpload(file: File): Promise<{ file: File; note?: string }> {
  if (!isLayered(file.name)) return { file }
  try {
    const { writeAseprite } = await import('../aseprite')
    const doc = await readLayered(file)
    const base = file.name.replace(/\.[^.]+$/, '')
    return { file: new File([(await writeAseprite(doc)) as BlobPart], `${base}.aseprite`, { type: 'application/octet-stream' }) }
  } catch (e) {
    if (ext(file.name) === '.pxo') throw e
    return { file, note: `${file.name}: camadas não lidas (${e instanceof Error ? e.message : e}); importado achatado` }
  }
}

export async function readLayered(file: File): Promise<SpriteDoc> {
  const buf = await file.arrayBuffer()
  switch (ext(file.name)) {
    case '.pxo': return readPxo(buf)
    case '.psd': return readPsd(buf)
    case '.kra': return readKra(buf)
    default: throw new FormatError(`formato não suportado: ${file.name}`)
  }
}

const layer = (id: number, name: string, extra: Partial<Layer> = {}): Layer => ({
  id, name, kind: 'image', parent: null, visible: true, locked: false, opacity: 255, blend: 'normal', ...extra,
})

/** Recorta/posiciona um bloco RGBA (w×h em x,y) no canvas W×H. */
function place(W: number, H: number, rgba: Uint8ClampedArray | Uint8Array, w: number, h: number, x: number, y: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(W * H * 4)
  for (let yy = 0; yy < h; yy++) {
    const ty = y + yy
    if (ty < 0 || ty >= H) continue
    const x0 = Math.max(0, x)
    const x1 = Math.min(W, x + w)
    if (x1 <= x0) continue
    out.set(rgba.subarray((yy * w + (x0 - x)) * 4, (yy * w + (x1 - x)) * 4), (ty * W + x0) * 4)
  }
  return out
}

const hasPixels = (d: Uint8ClampedArray) => {
  for (let i = 3; i < d.length; i += 4) if (d[i]) return true
  return false
}

// ---------------------------------------------------------------- Pixelorama (.pxo)

// BaseLayer.BlendModes do Pixelorama (NORMAL = 0)
const PXO_BLEND: Blend[] = ['normal', 'darken', 'multiply', 'color_burn', 'color_burn', 'lighten', 'screen', 'color_dodge', 'addition', 'overlay',
  'overlay', 'hard_light', 'difference', 'exclusion', 'subtract', 'divide']

type PxoLayer = { name?: string; visible?: boolean; locked?: boolean; opacity?: number; blend_mode?: number; clipping_mask?: boolean; parent?: number; type?: number }
type PxoJson = {
  size_x: number; size_y: number; fps?: number
  layers: PxoLayer[]
  frames: { cels: { opacity?: number }[]; duration?: number }[]
  tags?: { name: string; color?: string; from: number; to: number }[]
}

function godotColor(s: string | undefined): string {
  const m = s?.match(/\(([^)]+)\)/)
  if (!m) return '#f59e0b'
  const [r, g, b] = m[1].split(',').map((v) => Math.round(Math.max(0, Math.min(1, parseFloat(v))) * 255))
  return `#${[r, g, b].map((v) => (v || 0).toString(16).padStart(2, '0')).join('')}`
}

export async function readPxo(buf: ArrayBuffer): Promise<SpriteDoc> {
  let entries: Map<string, ZipEntry>
  try {
    entries = listZip(buf)
  } catch {
    throw new FormatError('.pxo antigo (antes do Pixelorama 1.0) não é suportado; salve de novo numa versão recente')
  }
  const txt = await readZipText(buf, entries, 'data.json')
  if (!txt) throw new FormatError('.pxo sem data.json')
  const j = JSON.parse(txt) as PxoJson
  const W = j.size_x
  const H = j.size_y
  const fps = j.fps || 6
  const doc = blankDoc(W, H, Math.max(1, j.frames.length))
  doc.layers = []
  const ids = j.layers.map((_, i) => i + 1)
  j.layers.forEach((l, i) => {
    const kind = l.type === 1 ? 'group' : 'image'
    if (l.type === 2 || l.type === 4) return // 3D e áudio: não há equivalente aqui
    doc.layers.push(layer(ids[i], l.name || `Camada ${i + 1}`, {
      kind, visible: l.visible ?? true, locked: !!l.locked, opacity: Math.round((l.opacity ?? 1) * 255),
      blend: PXO_BLEND[l.blend_mode ?? 0] ?? 'normal', clip: !!l.clipping_mask,
      parent: l.parent !== undefined && l.parent >= 0 ? ids[l.parent] : null,
    }))
  })
  doc.nextId = ids.length + 1
  doc.frames = j.frames.map((f) => ({ duration: Math.max(1, Math.round((1000 / fps) * (f.duration ?? 1))) }))
  for (let f = 0; f < j.frames.length; f++)
    for (let c = 0; c < j.layers.length; c++) {
      const L = doc.layers.find((x) => x.id === ids[c])
      if (!L || L.kind === 'group') continue
      const e = entries.get(`image_data/frames/${f + 1}/layer_${c + 1}`)
      if (!e) continue
      const raw = await readZipEntry(buf, e)
      if (raw.length < W * H * 4) continue
      const data = new Uint8ClampedArray(raw.buffer, raw.byteOffset, W * H * 4).slice()
      const op = j.frames[f].cels[c]?.opacity ?? 1
      if (op < 1) for (let i = 3; i < data.length; i += 4) data[i] = Math.round(data[i] * op)
      if (hasPixels(data)) doc.cels[celKey(ids[c], f)] = newCelImage(W, H, data)
    }
  doc.tags = (j.tags ?? []).map((t): Tag => ({
    name: t.name, color: godotColor(t.color), direction: 'forward', repeat: 0,
    from: Math.max(0, t.from - 1), to: Math.min(doc.frames.length - 1, Math.max(0, t.to - 1)),
  }))
  return doc
}

// ---------------------------------------------------------------- Photoshop (.psd)

const PSD_BLEND: Record<string, Blend> = {
  norm: 'normal', 'mul ': 'multiply', scrn: 'screen', over: 'overlay', dark: 'darken', lite: 'lighten', diff: 'difference', smud: 'exclusion',
  lddg: 'addition', fsub: 'subtract', fdiv: 'divide', 'div ': 'color_dodge', idiv: 'color_burn', hLit: 'hard_light', pass: 'normal',
}

export function readPsd(buf: ArrayBuffer): SpriteDoc {
  const dv = new DataView(buf)
  const u8 = new Uint8Array(buf)
  const sig = (o: number) => String.fromCharCode(u8[o], u8[o + 1], u8[o + 2], u8[o + 3])
  if (sig(0) !== '8BPS') throw new FormatError('não é um arquivo Photoshop')
  if (dv.getUint16(4) !== 1) throw new FormatError('PSB (documento grande) não é suportado')
  const H = dv.getUint32(14)
  const W = dv.getUint32(18)
  const depth = dv.getUint16(22)
  const mode = dv.getUint16(24)
  if (depth !== 8 || mode !== 3) throw new FormatError('só PSD RGB de 8 bits tem camadas suportadas (o servidor ainda importa a imagem achatada)')
  let p = 26
  p += 4 + dv.getUint32(p) // color mode data
  p += 4 + dv.getUint32(p) // image resources
  const lmLen = dv.getUint32(p)
  p += 4
  if (!lmLen) throw new FormatError('PSD sem camadas')
  const liLen = dv.getUint32(p)
  p += 4
  if (!liLen) throw new FormatError('PSD sem camadas')
  const count = Math.abs(dv.getInt16(p))
  p += 2
  type Rec = { top: number; left: number; bottom: number; right: number; channels: { id: number; len: number }[]; blend: string; opacity: number; clipping: number; flags: number; name: string; section: number }
  const recs: Rec[] = []
  for (let i = 0; i < count; i++) {
    const top = dv.getInt32(p), left = dv.getInt32(p + 4), bottom = dv.getInt32(p + 8), right = dv.getInt32(p + 12)
    const nch = dv.getUint16(p + 16)
    p += 18
    const channels: Rec['channels'] = []
    for (let c = 0; c < nch; c++) {
      channels.push({ id: dv.getInt16(p), len: dv.getUint32(p + 2) })
      p += 6
    }
    const blend = sig(p + 4)
    const opacity = u8[p + 8]
    const clipping = u8[p + 9]
    const flags = u8[p + 10]
    const extraLen = dv.getUint32(p + 12)
    p += 16
    const extraEnd = p + extraLen
    p += 4 + dv.getUint32(p) // máscara
    p += 4 + dv.getUint32(p) // blending ranges
    const nlen = u8[p]
    let name = new TextDecoder('latin1').decode(u8.subarray(p + 1, p + 1 + nlen))
    p += Math.ceil((nlen + 1) / 4) * 4
    let section = 0
    while (p + 12 <= extraEnd) {
      const s = sig(p)
      if (s !== '8BIM' && s !== '8B64') break
      const key = sig(p + 4)
      const len = dv.getUint32(p + 8)
      const d = p + 12
      if (key === 'luni') {
        const n = dv.getUint32(d)
        let str = ''
        for (let k = 0; k < n; k++) str += String.fromCharCode(dv.getUint16(d + 4 + k * 2))
        name = str.replace(/\0+$/, '')
      } else if (key === 'lsct' || key === 'lsdk') section = dv.getUint32(d)
      p = d + len + (len % 2)
    }
    p = extraEnd
    recs.push({ top, left, bottom, right, channels, blend, opacity, clipping, flags, name, section })
  }
  // dados das camadas: canal a canal, na mesma ordem dos registros
  const pixels: Uint8ClampedArray[] = []
  for (const r of recs) {
    const w = r.right - r.left
    const h = r.bottom - r.top
    const rgba = new Uint8ClampedArray(Math.max(0, w * h) * 4)
    let hasAlpha = false
    for (const ch of r.channels) {
      const start = p
      p += ch.len
      if (w <= 0 || h <= 0 || ch.len < 2) continue
      const comp = dv.getUint16(start)
      const plane = new Uint8Array(w * h)
      if (comp === 0) plane.set(u8.subarray(start + 2, start + 2 + w * h))
      else if (comp === 1) {
        let q = start + 2 + h * 2
        for (let y = 0; y < h; y++) {
          const rowEnd = q + dv.getUint16(start + 2 + y * 2)
          let x = 0
          while (q < rowEnd && x < w) {
            const n = dv.getInt8(q++)
            if (n >= 0) {
              plane.set(u8.subarray(q, q + n + 1), y * w + x)
              q += n + 1
              x += n + 1
            } else if (n !== -128) {
              plane.fill(u8[q++], y * w + x, y * w + x + 1 - n)
              x += 1 - n
            }
          }
          q = rowEnd
        }
      } else continue // ZIP: raro em PSD 8 bits
      const k = ch.id === -1 ? 3 : ch.id >= 0 && ch.id <= 2 ? ch.id : -1
      if (k < 0) continue
      if (k === 3) hasAlpha = true
      for (let i = 0; i < w * h; i++) rgba[i * 4 + k] = plane[i]
    }
    if (!hasAlpha) for (let i = 3; i < rgba.length; i += 4) rgba[i] = 255
    pixels.push(rgba)
  }
  // registros vão de baixo para cima; grupos: divisor (3) embaixo do conteúdo, pasta (1/2) em cima
  const doc = blankDoc(W, H)
  doc.layers = []
  let id = 1
  const stack: number[] = []
  recs.forEach((r, i) => {
    const parent = stack.length ? stack[stack.length - 1] : null
    if (r.section === 3) {
      const g = id++
      doc.layers.push(layer(g, 'Grupo', { kind: 'group', parent }))
      stack.push(g)
      return
    }
    if (r.section === 1 || r.section === 2) {
      const g = stack.pop()
      const L = doc.layers.find((x) => x.id === g)
      if (L) Object.assign(L, { name: r.name, visible: !(r.flags & 2), opacity: r.opacity, blend: PSD_BLEND[r.blend] ?? 'normal', collapsed: r.section === 2 })
      return
    }
    const lid = id++
    doc.layers.push(layer(lid, r.name || `Camada ${lid}`, { parent, visible: !(r.flags & 2), opacity: r.opacity, blend: PSD_BLEND[r.blend] ?? 'normal', clip: r.clipping === 1 }))
    const w = r.right - r.left
    const h = r.bottom - r.top
    if (w > 0 && h > 0) {
      const data = place(W, H, pixels[i], w, h, r.left, r.top)
      if (hasPixels(data)) doc.cels[celKey(lid, 0)] = newCelImage(W, H, data)
    }
  })
  doc.nextId = id
  if (!doc.layers.some((l) => l.kind !== 'group')) doc.layers.push(layer(doc.nextId++, 'Camada 1'))
  return doc
}

// ---------------------------------------------------------------- Krita (.kra)

/** XML mínimo (tags, atributos, aninhamento) — suficiente para o maindoc.xml do Krita, e roda fora do navegador. */
type XEl = { tag: string; attrs: Record<string, string>; children: XEl[] }
const XML_ENT: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }
const unent = (v: string) => v.replace(/&(#x?[0-9a-f]+|\w+);/gi, (m, e: string) =>
  e[0] === '#' ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : XML_ENT[e] ?? m)
export function parseXml(src: string): XEl {
  const root: XEl = { tag: '#root', attrs: {}, children: [] }
  const stack = [root]
  const re = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[[\s\S]*?\]\]>|<!DOCTYPE[^>]*>|<(\/?)([\w:.-]+)((?:\s+[\w:.-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>/g
  for (let m; (m = re.exec(src)); ) {
    if (!m[2]) continue
    if (m[1]) {
      if (stack.length > 1) stack.pop()
      continue
    }
    const attrs: Record<string, string> = {}
    for (const a of m[3].matchAll(/([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) attrs[a[1]] = unent(a[2] ?? a[3] ?? '')
    const el: XEl = { tag: m[2], attrs, children: [] }
    stack[stack.length - 1].children.push(el)
    if (!m[4]) stack.push(el)
  }
  return root
}
const xfind = (el: XEl, tag: string): XEl | undefined => {
  for (const c of el.children) {
    if (c.tag === tag) return c
    const r = xfind(c, tag)
    if (r) return r
  }
}
const xall = (el: XEl, tag: string, out: XEl[] = []): XEl[] => {
  for (const c of el.children) {
    if (c.tag === tag) out.push(c)
    xall(c, tag, out)
  }
  return out
}

const KRA_BLEND: Record<string, Blend> = {
  normal: 'normal', multiply: 'multiply', screen: 'screen', overlay: 'overlay', darken: 'darken', lighten: 'lighten', diff: 'difference',
  exclusion: 'exclusion', add: 'addition', subtract: 'subtract', divide: 'divide', dodge: 'color_dodge', burn: 'color_burn', hard_light: 'hard_light',
}

/** Descompressor LZF (usado nos tiles do Krita). */
export function lzf(input: Uint8Array, outLen: number): Uint8Array {
  const out = new Uint8Array(outLen)
  let ip = 0
  let op = 0
  while (ip < input.length && op < outLen) {
    let ctrl = input[ip++]
    if (ctrl < 32) {
      ctrl++
      out.set(input.subarray(ip, ip + ctrl), op)
      ip += ctrl
      op += ctrl
    } else {
      let len = ctrl >> 5
      let ref = op - ((ctrl & 0x1f) << 8) - 1
      if (len === 7) len += input[ip++]
      ref -= input[ip++]
      len += 2
      while (len-- > 0 && op < outLen) out[op++] = out[ref++]
    }
  }
  return out
}

/** Camada de pixels do Krita (formato de tiles "VERSION 2") → RGBA no canvas, com o deslocamento da camada. */
function decodeKraLayer(raw: Uint8Array, W: number, H: number, ox: number, oy: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(W * H * 4)
  const td = new TextDecoder()
  let p = 0
  const line = () => {
    const e = raw.indexOf(10, p)
    const s = td.decode(raw.subarray(p, e < 0 ? raw.length : e))
    p = e < 0 ? raw.length : e + 1
    return s
  }
  const head: Record<string, number> = {}
  for (let k = 0; k < 5; k++) {
    const [key, val] = line().split(' ')
    head[key] = Number(val)
  }
  const tw = head.TILEWIDTH || 64
  const th = head.TILEHEIGHT || 64
  const ps = head.PIXELSIZE || 4
  if (ps !== 4) throw new FormatError('só camadas RGBA de 8 bits do Krita são suportadas')
  const n = head.DATA || 0
  for (let t = 0; t < n && p < raw.length; t++) {
    const [xs, ys, , sz] = line().split(',')
    const size = Number(sz)
    const block = raw.subarray(p, p + size)
    p += size
    const plane = block[0] === 1 ? lzf(block.subarray(1), tw * th * ps) : block.subarray(1)
    const tx = Number(xs) + ox
    const ty = Number(ys) + oy
    const area = tw * th
    for (let y = 0; y < th; y++) {
      const cy = ty + y
      if (cy < 0 || cy >= H) continue
      for (let x = 0; x < tw; x++) {
        const cx = tx + x
        if (cx < 0 || cx >= W) continue
        const i = y * tw + x
        const o = (cy * W + cx) * 4
        // planar BGRA
        out[o] = plane[2 * area + i]
        out[o + 1] = plane[area + i]
        out[o + 2] = plane[i]
        out[o + 3] = plane[3 * area + i]
      }
    }
  }
  return out
}

export async function readKra(buf: ArrayBuffer): Promise<SpriteDoc> {
  const entries = listZip(buf)
  const xml = await readZipText(buf, entries, 'maindoc.xml')
  if (!xml) throw new FormatError('.kra sem maindoc.xml')
  const image = xfind(parseXml(xml), 'IMAGE')
  if (!image) throw new FormatError('.kra sem IMAGE')
  const W = Number(image.attrs.width)
  const H = Number(image.attrs.height)
  const imgName = image.attrs.name ?? ''
  const cs = image.attrs.colorspacename ?? 'RGBA'
  if (cs !== 'RGBA') throw new FormatError(`espaço de cor ${cs} não suportado (use RGBA 8 bits)`)
  const anim = xfind(image, 'animation')
  const fps = Number((anim && xfind(anim, 'framerate'))?.attrs.value) || 12
  type K = { name: string; file: string; x: number; y: number; opacity: number; visible: boolean; blend: Blend; group: boolean; parent: number | null; id: number; keys?: { time: number; file: string }[] }
  const nodes: K[] = []
  let nid = 1
  const walk = (el: XEl, parent: number | null) => {
    // no XML a primeira camada é a de cima; o editor quer de baixo para cima
    const kids = el.children.filter((c) => c.tag === 'layer').reverse()
    for (const c of kids) {
      const a = c.attrs
      const type = a.nodetype
      if (type !== 'paintlayer' && type !== 'grouplayer') continue
      const k: K = {
        id: nid++, parent, name: a.name ?? 'Camada', file: a.filename ?? '',
        x: Number(a.x ?? 0), y: Number(a.y ?? 0), opacity: Number(a.opacity ?? 255),
        visible: a.visible !== '0', blend: KRA_BLEND[a.compositeop ?? 'normal'] ?? 'normal', group: type === 'grouplayer',
      }
      nodes.push(k)
      if (k.group) {
        const sub = c.children.find((x) => x.tag === 'layers')
        if (sub) walk(sub, k.id)
      } else if (a.keyframes) k.keys = []
    }
  }
  const top = image.children.find((c) => c.tag === 'layers')
  if (top) walk(top, null)
  // keyframes de animação
  let frames = 1
  for (const k of nodes) {
    if (!k.keys) continue
    const kx = await readZipText(buf, entries, `${imgName}/layers/${k.file}.keyframes.xml`)
    if (!kx) {
      k.keys = undefined
      continue
    }
    k.keys = xall(parseXml(kx), 'keyframe').map((e) => ({ time: Number(e.attrs.time ?? 0), file: e.attrs.frame ?? k.file }))
    for (const t of k.keys) frames = Math.max(frames, t.time + 1)
  }
  const doc = blankDoc(W, H, frames)
  doc.frames = Array.from({ length: frames }, () => ({ duration: Math.round(1000 / fps) }))
  doc.layers = nodes.map((k) => layer(k.id, k.name, { kind: k.group ? 'group' : 'image', parent: k.parent, visible: k.visible, opacity: k.opacity, blend: k.blend }))
  doc.nextId = nid
  for (const k of nodes) {
    if (k.group) continue
    const keys = k.keys?.length ? [...k.keys].sort((a, b) => a.time - b.time) : [{ time: 0, file: k.file }]
    for (let i = 0; i < keys.length; i++) {
      const e = entries.get(`${imgName}/layers/${keys[i].file}`)
      if (!e) continue
      const data = decodeKraLayer(await readZipEntry(buf, e), W, H, k.x, k.y)
      if (!hasPixels(data)) continue
      const img = newCelImage(W, H, data)
      const until = i + 1 < keys.length ? keys[i + 1].time : frames
      for (let f = keys[i].time; f < until; f++) doc.cels[celKey(k.id, f)] = img // segura até o próximo keyframe (cels ligados)
    }
  }
  if (!doc.layers.some((l) => l.kind !== 'group')) doc.layers.push(layer(doc.nextId++, 'Camada 1'))
  return doc
}

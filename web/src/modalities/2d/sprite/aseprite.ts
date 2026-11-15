// Leitura e escrita de .aseprite no navegador (espelha forge_2d/aseprite.py).
// Spec: https://github.com/aseprite/aseprite/blob/main/docs/ase-file-specs.md
// O editor salva o documento inteiro (camadas, grupos, opacidade, modos, cels ligados, durações, tags) como .aseprite;
// o servidor importa esse arquivo como nova versão do asset e guarda o original como fonte.
import { BLEND_INDEX, blankDoc, celKey, newCelImage, renderOrder, type Blend, type CelImage, type Layer, type SpriteDoc, type Tag, type TagDir, type Tilemap, type Tileset } from './doc'
import { renderTilemap } from './tilemap'

const DIRS: TagDir[] = ['forward', 'reverse', 'pingpong', 'pingpong_reverse']
const BLEND_BY_INDEX = Object.fromEntries(Object.entries(BLEND_INDEX).map(([k, v]) => [v, k as Blend])) as Record<number, Blend>

async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

async function deflate(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream('deflate'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

const hex2 = (n: number) => n.toString(16).padStart(2, '0')

export class AsepriteError extends Error {}

/** Prefixo do texto de "user data" das camadas com dados do Asset Forge (o Aseprite preserva e ignora). */
export const AF_PREFIX = 'asset-forge:'

/** Lê um .aseprite e devolve o documento do editor. Camadas de tilemap e de referência são ignoradas. */
export async function readAseprite(buf: ArrayBuffer): Promise<{ doc: SpriteDoc; skipped: string[] }> {
  const dv = new DataView(buf)
  const u8 = new Uint8Array(buf)
  if (buf.byteLength < 128 || dv.getUint16(4, true) !== 0xa5e0) throw new AsepriteError('não é um arquivo Aseprite')
  const nFrames = dv.getUint16(6, true)
  const W = dv.getUint16(8, true)
  const H = dv.getUint16(10, true)
  const depth = dv.getUint16(12, true)
  const flags = dv.getUint32(14, true)
  const transparent = u8[28]
  if (![8, 16, 32].includes(depth)) throw new AsepriteError(`profundidade de cor inválida: ${depth}`)
  const opacityValid = (flags & 1) !== 0
  const doc = blankDoc(W, H, nFrames)
  doc.colorMode = depth === 8 ? 'indexed' : depth === 16 ? 'gray' : 'rgb'
  const gw = dv.getUint16(40, true)
  const gh = dv.getUint16(42, true)
  if (gw > 0 && gh > 0) doc.grid = { x: dv.getInt16(36, true), y: dv.getInt16(38, true), w: gw, h: gh }
  doc.layers = []
  const skipped: string[] = []
  const palette: [number, number, number, number][] = []
  let newPalette = false
  const levelGroups: number[] = [] // id do último grupo visto em cada nível
  const aseLayers: { id: number; kind: number; reference: boolean }[] = []
  type Pending = { layer: number; frame: number; link: number }
  const links: Pending[] = []
  const pendingMaps: { layer: number; frame: number; tm: Tilemap; x: number; y: number }[] = []
  let prevChunk = 0
  let lastLayer: Layer | null = null
  /** Pixels do arquivo (na profundidade de cor dele) → RGBA. */
  const toRGBA = (px: Uint8Array, n: number, op = 255): Uint8ClampedArray => {
    const out = new Uint8ClampedArray(n * 4)
    const bpp = depth / 8
    for (let i = 0; i < n; i++) {
      const s = i * bpp
      let r: number, g: number, b: number, a: number
      if (depth === 32) [r, g, b, a] = [px[s], px[s + 1], px[s + 2], px[s + 3]]
      else if (depth === 16) [r, g, b, a] = [px[s], px[s], px[s], px[s + 1]]
      else {
        const idx = px[s]
        ;[r, g, b, a] = palette[idx] ?? [0, 0, 0, 255]
        if (idx === transparent) a = 0
      }
      out[i * 4] = r
      out[i * 4 + 1] = g
      out[i * 4 + 2] = b
      out[i * 4 + 3] = (a * op) / 255
    }
    return out
  }
  const td = new TextDecoder()
  let p = 128
  const str = () => {
    const n = dv.getUint16(p, true)
    const s = td.decode(u8.subarray(p + 2, p + 2 + n))
    p += 2 + n
    return s
  }

  for (let fi = 0; fi < nFrames; fi++) {
    const fstart = p
    const fsize = dv.getUint32(p, true)
    if (dv.getUint16(p + 4, true) !== 0xf1fa) throw new AsepriteError(`frame ${fi}: assinatura inválida`)
    const oldChunks = dv.getUint16(p + 6, true)
    const duration = dv.getUint16(p + 8, true)
    const newChunks = dv.getUint32(p + 12, true)
    doc.frames[fi] = { duration: duration || 100 }
    p += 16
    const n = newChunks || oldChunks
    for (let c = 0; c < n; c++) {
      const cstart = p
      const csize = dv.getUint32(p, true)
      const ctype = dv.getUint16(p + 4, true)
      const cend = cstart + csize
      p += 6
      if (ctype === 0x2004) {
        const lflags = dv.getUint16(p, true)
        const ltype = dv.getUint16(p + 2, true)
        const level = dv.getUint16(p + 4, true)
        const blend = dv.getUint16(p + 10, true)
        const opacity = u8[p + 12]
        p += 16
        const name = str()
        const tsIndex = ltype === 2 ? dv.getUint32(p, true) : undefined
        const id = doc.nextId++
        aseLayers.push({ id, kind: ltype, reference: (lflags & 64) !== 0 })
        const layer: Layer = {
          id, name, kind: ltype === 1 ? 'group' : ltype === 2 ? 'tilemap' : 'image', parent: level > 0 ? (levelGroups[level - 1] ?? null) : null,
          visible: (lflags & 1) !== 0 && (lflags & 64) === 0, locked: (lflags & 2) === 0, opacity: opacityValid ? opacity : 255,
          blend: BLEND_BY_INDEX[blend] ?? 'normal', collapsed: (lflags & 32) !== 0, ...(tsIndex !== undefined ? { tileset: tsIndex } : {}),
        }
        doc.layers.push(layer)
        if (ltype === 1) levelGroups[level] = id
        lastLayer = layer
      } else if (ctype === 0x2005) {
        const li = dv.getUint16(p, true)
        const x = dv.getInt16(p + 2, true)
        const y = dv.getInt16(p + 4, true)
        const op = u8[p + 6]
        const type = dv.getUint16(p + 7, true)
        p += 16
        const target = aseLayers[li]
        if (!target || target.kind === 1) {
          p = cend
          continue
        }
        if (type === 3) {
          const tw = dv.getUint16(p, true)
          const th = dv.getUint16(p + 2, true)
          const bits = dv.getUint16(p + 4, true)
          const idMask = dv.getUint32(p + 6, true)
          if (bits !== 32) skipped.push(`tilemap com ${bits} bits por tile (frame ${fi})`)
          else {
            const raw = await inflate(u8.subarray(p + 32, cend))
            const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
            const tiles = new Uint32Array(tw * th)
            for (let i = 0; i < tiles.length && i * 4 + 4 <= raw.length; i++) tiles[i] = view.getUint32(i * 4, true) & idMask
            // a grade do arquivo começa na posição do cel; aqui ela sempre começa em 0,0 (deslocamentos múltiplos do tile)
            pendingMaps.push({ layer: target.id, frame: fi, tm: { cols: tw, rows: th, tiles }, x, y })
          }
        } else if (type === 1) {
          links.push({ layer: target.id, frame: fi, link: dv.getUint16(p, true) })
        } else if (type === 0 || type === 2) {
          const cw = dv.getUint16(p, true)
          const ch = dv.getUint16(p + 2, true)
          let px: Uint8Array = u8.subarray(p + 4, cend)
          if (type === 2) px = await inflate(px)
          const bpp = depth / 8
          if (px.length < cw * ch * bpp) throw new AsepriteError(`frame ${fi}: cel truncado`)
          const rgba = toRGBA(px, cw * ch, op)
          const data = new Uint8ClampedArray(W * H * 4)
          for (let yy = 0; yy < ch; yy++) {
            const ty = y + yy
            if (ty < 0 || ty >= H) continue
            const x0 = Math.max(0, x)
            const x1 = Math.min(W, x + cw)
            if (x1 <= x0) continue
            data.set(rgba.subarray((yy * cw + (x0 - x)) * 4, (yy * cw + (x1 - x)) * 4), (ty * W + x0) * 4)
          }
          doc.cels[celKey(target.id, fi)] = newCelImage(W, H, data)
        }
      } else if (ctype === 0x2023) {
        const tsId = dv.getUint32(p, true)
        const tflags = dv.getUint32(p + 4, true)
        const count = dv.getUint32(p + 8, true)
        const tw = dv.getUint16(p + 12, true)
        const th = dv.getUint16(p + 14, true)
        p += 32
        const name = str()
        if (tflags & 1) p += 8
        if (tflags & 2) {
          const len = dv.getUint32(p, true)
          const img = await inflate(u8.subarray(p + 4, p + 4 + len))
          const rgba = toRGBA(img, tw * th * count)
          const tiles: Uint8ClampedArray[] = []
          for (let i = 0; i < count; i++) tiles.push(rgba.slice(i * tw * th * 4, (i + 1) * tw * th * 4))
          if (!tiles.length) tiles.push(new Uint8ClampedArray(tw * th * 4))
          doc.tilesets.push({ id: tsId, name, tw, th, tiles })
        } else skipped.push(`tileset externo "${name}"`)
      } else if (ctype === 0x2022) {
        const nkeys = dv.getUint32(p, true)
        const sflags = dv.getUint32(p + 4, true)
        p += 12
        const name = str()
        for (let k = 0; k < nkeys; k++) {
          const frameNo = dv.getUint32(p, true)
          const sx = dv.getInt32(p + 4, true)
          const sy = dv.getInt32(p + 8, true)
          const sw = dv.getUint32(p + 12, true)
          const sh = dv.getUint32(p + 16, true)
          p += 20
          let center: { x: number; y: number; w: number; h: number } | undefined
          let pivot: { x: number; y: number } | undefined
          if (sflags & 1) {
            center = { x: dv.getInt32(p, true), y: dv.getInt32(p + 4, true), w: dv.getUint32(p + 8, true), h: dv.getUint32(p + 12, true) }
            p += 16
          }
          if (sflags & 2) {
            pivot = { x: dv.getInt32(p, true), y: dv.getInt32(p + 4, true) }
            p += 8
          }
          // uma chave por fatia aqui: fica a do primeiro quadro
          if (k === 0 || frameNo === 0) {
            const sl = { name, color: '#0000ff', x: sx, y: sy, w: sw, h: sh, ...(center ? { center } : {}), ...(pivot ? { pivot } : {}) }
            if (k === 0) doc.slices.push(sl)
            else doc.slices[doc.slices.length - 1] = sl
          }
        }
      } else if (ctype === 0x2020 && prevChunk === 0x2004 && lastLayer) {
        // dados do Asset Forge guardados no texto da camada (efeitos não destrutivos e máscara de recorte)
        const uflags = dv.getUint32(p, true)
        p += 4
        if (uflags & 1) {
          const txt = str()
          if (txt.startsWith(AF_PREFIX)) {
            try {
              const meta = JSON.parse(txt.slice(AF_PREFIX.length)) as { effects?: Layer['effects']; clip?: boolean }
              if (meta.effects?.length) lastLayer.effects = meta.effects
              if (meta.clip) lastLayer.clip = true
            } catch {
              skipped.push(`dados da camada "${lastLayer.name}" ilegíveis`)
            }
          }
        }
      } else if (ctype === 0x2020 && prevChunk === 0x2022 && doc.slices.length) {
        const uflags = dv.getUint32(p, true)
        p += 4
        if (uflags & 1) str()
        if (uflags & 2) doc.slices[doc.slices.length - 1].color = `#${hex2(u8[p])}${hex2(u8[p + 1])}${hex2(u8[p + 2])}`
      } else if (ctype === 0x2018) {
        const nt = dv.getUint16(p, true)
        p += 10
        for (let t = 0; t < nt; t++) {
          const from = dv.getUint16(p, true)
          const to = dv.getUint16(p + 2, true)
          const dir = u8[p + 4]
          const repeat = dv.getUint16(p + 5, true)
          const color = `#${hex2(u8[p + 13])}${hex2(u8[p + 14])}${hex2(u8[p + 15])}`
          p += 17
          doc.tags.push({ name: str(), from, to, direction: DIRS[dir] ?? 'forward', repeat, color })
        }
      } else if (ctype === 0x2019) {
        const total = dv.getUint32(p, true)
        const first = dv.getUint32(p + 4, true)
        const last = dv.getUint32(p + 8, true)
        p += 20
        while (palette.length < total) palette.push([0, 0, 0, 255])
        for (let i = first; i <= last; i++) {
          const ef = dv.getUint16(p, true)
          palette[i] = [u8[p + 2], u8[p + 3], u8[p + 4], u8[p + 5]]
          p += 6
          if (ef & 1) str()
        }
        newPalette = true
      } else if ((ctype === 0x0004 || ctype === 0x0011) && !newPalette) {
        const packets = dv.getUint16(p, true)
        p += 2
        let pos = 0
        for (let k = 0; k < packets; k++) {
          pos += u8[p]
          const cnt = u8[p + 1] || 256
          p += 2
          for (let i = 0; i < cnt; i++) {
            let [r, g, b] = [u8[p], u8[p + 1], u8[p + 2]]
            if (ctype === 0x0011) [r, g, b] = [(r * 255) / 63, (g * 255) / 63, (b * 255) / 63].map(Math.round) as [number, number, number]
            palette[pos + i] = [r, g, b, 255]
            p += 3
          }
          pos += cnt
        }
      }
      prevChunk = ctype
      p = cend
    }
    p = fstart + fsize
  }
  // tilemaps: renderiza agora que todos os tilesets foram lidos
  for (const { layer, frame, tm: src, x, y } of pendingMaps) {
    const l = doc.layers.find((q) => q.id === layer)
    const ts = doc.tilesets.find((t) => t.id === l?.tileset)
    if (!ts) {
      skipped.push(`tilemap sem tileset (frame ${frame})`)
      continue
    }
    // aqui a grade começa sempre em 0,0: o cel do arquivo é deslocado em tiles inteiros
    const ox = Math.round(x / ts.tw)
    const oy = Math.round(y / ts.th)
    if (ox * ts.tw !== x || oy * ts.th !== y) skipped.push(`deslocamento do tilemap fora da grade (frame ${frame})`)
    const cols = Math.ceil(W / ts.tw)
    const rows = Math.ceil(H / ts.th)
    const tm: Tilemap = { cols, rows, tiles: new Uint32Array(cols * rows) }
    for (let r = 0; r < src.rows; r++)
      for (let c = 0; c < src.cols; c++) {
        const tc = c + ox
        const tr = r + oy
        if (tc >= 0 && tr >= 0 && tc < cols && tr < rows) tm.tiles[tr * cols + tc] = src.tiles[r * src.cols + c]
      }
    doc.cels[celKey(layer, frame)] = { ...newCelImage(W, H, renderTilemap(ts, tm, W, H)), tilemap: tm }
  }
  // camadas de tilemap cujo tileset não veio no arquivo viram imagem
  doc.layers = doc.layers.map((l) => (l.kind === 'tilemap' && !doc.tilesets.some((t) => t.id === l.tileset) ? { ...l, kind: 'image' as const, tileset: undefined } : l))
  for (const l of links) {
    const src = doc.cels[celKey(l.layer, l.link)]
    if (src) doc.cels[celKey(l.layer, l.frame)] = src
  }
  doc.palette = palette.map((c, i) => (depth === 8 && i === transparent ? [c[0], c[1], c[2], 0] : [...c]) as [number, number, number, number])
  if (!doc.layers.some((l) => l.kind !== 'group')) doc.layers.push({ id: doc.nextId++, name: 'Camada 1', kind: 'image', parent: null, visible: true, locked: false, opacity: 255, blend: 'normal' })
  return { doc, skipped }
}

// ---------------------------------------------------------------- escrita

class W {
  parts: Uint8Array[] = []
  size = 0
  push(b: Uint8Array) {
    this.parts.push(b)
    this.size += b.length
  }
  u8(v: number) { this.push(Uint8Array.of(v & 255)) }
  u16(v: number) { const b = new Uint8Array(2); new DataView(b.buffer).setUint16(0, v, true); this.push(b) }
  i16(v: number) { const b = new Uint8Array(2); new DataView(b.buffer).setInt16(0, v, true); this.push(b) }
  i32(v: number) { const b = new Uint8Array(4); new DataView(b.buffer).setInt32(0, v, true); this.push(b) }
  u32(v: number) { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, v, true); this.push(b) }
  zeros(n: number) { this.push(new Uint8Array(n)) }
  str(s: string) { const b = new TextEncoder().encode(s); this.u16(b.length); this.push(b) }
  bytes(): Uint8Array {
    const out = new Uint8Array(this.size)
    let o = 0
    for (const p of this.parts) {
      out.set(p, o)
      o += p.length
    }
    return out
  }
}

function chunk(type: number, body: Uint8Array): Uint8Array {
  const w = new W()
  w.u32(body.length + 6)
  w.u16(type)
  w.push(body)
  return w.bytes()
}

function bbox(data: Uint8ClampedArray, Wd: number, Hd: number): [number, number, number, number] | null {
  let x0 = Wd, y0 = Hd, x1 = -1, y1 = -1
  for (let y = 0; y < Hd; y++)
    for (let x = 0; x < Wd; x++)
      if (data[(y * Wd + x) * 4 + 3]) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
  return x1 < 0 ? null : [x0, y0, x1 - x0 + 1, y1 - y0 + 1]
}

/** Chunk de tileset com os tiles dentro do arquivo (flags 2 = tiles no arquivo, 4 = tile 0 é vazio). */
async function tilesetChunk(ts: Tileset, encode: (rgba: Uint8Array, n: number) => Uint8Array): Promise<Uint8Array> {
  const b = new W()
  b.u32(ts.id)
  b.u32(2 | 4)
  b.u32(ts.tiles.length)
  b.u16(ts.tw)
  b.u16(ts.th)
  b.i16(1)
  b.zeros(14)
  b.str(ts.name)
  const all = new Uint8Array(ts.tw * ts.th * 4 * ts.tiles.length)
  ts.tiles.forEach((t, i) => all.set(t, i * ts.tw * ts.th * 4))
  const z = await deflate(encode(all, ts.tw * ts.th * ts.tiles.length))
  b.u32(z.length)
  b.push(z)
  return chunk(0x2023, b.bytes())
}

/** Ordem do arquivo: grupo antes dos filhos, de baixo para cima (como o Aseprite grava). */
function fileOrder(doc: SpriteDoc): { layer: Layer; level: number }[] {
  const out: { layer: Layer; level: number }[] = []
  const walk = (parent: number | null, level: number) => {
    for (const l of doc.layers.filter((x) => x.parent === parent)) {
      out.push({ layer: l, level })
      if (l.kind === 'group') walk(l.id, level + 1)
    }
  }
  walk(null, 0)
  return out
}

/** Grava o documento como .aseprite RGBA. */
export async function writeAseprite(doc: SpriteDoc): Promise<Uint8Array> {
  const order = fileOrder(doc)
  const depth = doc.colorMode === 'indexed' ? 8 : doc.colorMode === 'gray' ? 16 : 32
  // paleta gravada: a do documento; no indexado, garante uma entrada transparente (índice "máscara")
  const pal = doc.palette.slice(0, 256).map((c) => [...c] as [number, number, number, number])
  let transparent = pal.findIndex((c) => c[3] === 0)
  if (depth === 8) {
    if (transparent < 0 && pal.length < 256) {
      pal.push([0, 0, 0, 0])
      transparent = pal.length - 1
    }
    if (transparent < 0) transparent = 0
    if (!pal.length) throw new AsepriteError('modo indexado sem paleta')
  }
  const indexOfColor = new Map<number, number>()
  pal.forEach((c, i) => {
    if (c[3] > 0) {
      const k = ((c[0] << 16) | (c[1] << 8) | c[2]) >>> 0
      if (!indexOfColor.has(k)) indexOfColor.set(k, i)
    }
  })
  const nearestIdx = (r: number, g: number, b: number) => {
    const k = ((r << 16) | (g << 8) | b) >>> 0
    const hit = indexOfColor.get(k)
    if (hit !== undefined) return hit
    let best = 0
    let bd = Infinity
    pal.forEach((c, i) => {
      if (c[3] === 0) return
      const d = (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2
      if (d < bd) {
        bd = d
        best = i
      }
    })
    indexOfColor.set(k, best)
    return best
  }
  const encode = (rgba: Uint8Array, n: number): Uint8Array => {
    if (depth === 32) return rgba
    if (depth === 16) {
      const o = new Uint8Array(n * 2)
      for (let i = 0; i < n; i++) {
        o[i * 2] = Math.round(0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2])
        o[i * 2 + 1] = rgba[i * 4 + 3]
      }
      return o
    }
    const o = new Uint8Array(n)
    for (let i = 0; i < n; i++) o[i] = rgba[i * 4 + 3] < 128 ? transparent : nearestIdx(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2])
    return o
  }
  const indexOf = new Map(order.map((o, i) => [o.layer.id, i]))
  const images = new Set(renderOrder(doc).map((l) => l.id))
  const frames: Uint8Array[] = []
  const firstUse = new Map<CelImage, number>() // cel -> primeiro quadro (cels ligados)
  for (let fi = 0; fi < doc.frames.length; fi++) {
    const chunks: Uint8Array[] = []
    if (fi === 0) {
      const cp = new W()
      cp.u16(1)
      cp.u16(0)
      cp.u32(0)
      cp.zeros(8)
      chunks.push(chunk(0x2007, cp.bytes()))
      for (const ts of doc.tilesets) chunks.push(await tilesetChunk(ts, encode))
      for (const { layer, level } of order) {
        const b = new W()
        b.u16((layer.visible ? 1 : 0) | (layer.locked ? 0 : 2) | (layer.collapsed ? 32 : 0))
        b.u16(layer.kind === 'group' ? 1 : layer.kind === 'tilemap' ? 2 : 0)
        b.u16(level)
        b.u16(0)
        b.u16(0)
        b.u16(BLEND_INDEX[layer.blend] ?? 0)
        b.u8(layer.opacity)
        b.zeros(3)
        b.str(layer.name)
        if (layer.kind === 'tilemap') b.u32(layer.tileset ?? 0)
        chunks.push(chunk(0x2004, b.bytes()))
        if (layer.effects?.length || layer.clip) {
          const ud = new W()
          ud.u32(1)
          ud.str(AF_PREFIX + JSON.stringify({ effects: layer.effects ?? [], clip: !!layer.clip }))
          chunks.push(chunk(0x2020, ud.bytes()))
        }
      }
      if (doc.tags.length) {
        const b = new W()
        b.u16(doc.tags.length)
        b.zeros(8)
        for (const t of doc.tags) {
          b.u16(t.from)
          b.u16(t.to)
          b.u8(Math.max(0, DIRS.indexOf(t.direction)))
          b.u16(t.repeat)
          b.zeros(6)
          const c = t.color.replace('#', '')
          b.u8(parseInt(c.slice(0, 2), 16) || 0)
          b.u8(parseInt(c.slice(2, 4), 16) || 0)
          b.u8(parseInt(c.slice(4, 6), 16) || 0)
          b.u8(0)
          b.str(t.name)
        }
        chunks.push(chunk(0x2018, b.bytes()))
      }
      for (const sl of doc.slices) {
        const b = new W()
        b.u32(1)
        b.u32((sl.center ? 1 : 0) | (sl.pivot ? 2 : 0))
        b.u32(0)
        b.str(sl.name)
        b.u32(0)
        b.i32(sl.x)
        b.i32(sl.y)
        b.u32(sl.w)
        b.u32(sl.h)
        if (sl.center) {
          b.i32(sl.center.x)
          b.i32(sl.center.y)
          b.u32(sl.center.w)
          b.u32(sl.center.h)
        }
        if (sl.pivot) {
          b.i32(sl.pivot.x)
          b.i32(sl.pivot.y)
        }
        chunks.push(chunk(0x2022, b.bytes()))
        const ud = new W()
        ud.u32(2)
        const c = sl.color.replace('#', '')
        ud.u8(parseInt(c.slice(0, 2), 16) || 0)
        ud.u8(parseInt(c.slice(2, 4), 16) || 0)
        ud.u8(parseInt(c.slice(4, 6), 16) || 0)
        ud.u8(255)
        chunks.push(chunk(0x2020, ud.bytes()))
      }
      if (pal.length) {
        const b = new W()
        b.u32(pal.length)
        b.u32(0)
        b.u32(pal.length - 1)
        b.zeros(8)
        for (const c of pal) {
          b.u16(0)
          b.u8(c[0])
          b.u8(c[1])
          b.u8(c[2])
          b.u8(c[3])
        }
        chunks.push(chunk(0x2019, b.bytes()))
      }
    }
    for (const { layer } of order) {
      if (!images.has(layer.id)) continue
      const cel = doc.cels[celKey(layer.id, fi)]
      if (!cel) continue
      const li = indexOf.get(layer.id)!
      const b = new W()
      b.u16(li)
      const prev = firstUse.get(cel)
      if (prev !== undefined) {
        b.i16(0)
        b.i16(0)
        b.u8(255)
        b.u16(1)
        b.i16(0)
        b.zeros(5)
        b.u16(prev)
        chunks.push(chunk(0x2005, b.bytes()))
        continue
      }
      if (layer.kind === 'tilemap') {
        if (!cel.tilemap) continue
        firstUse.set(cel, fi)
        const tm = cel.tilemap
        b.i16(0)
        b.i16(0)
        b.u8(255)
        b.u16(3)
        b.i16(0)
        b.zeros(5)
        b.u16(tm.cols)
        b.u16(tm.rows)
        b.u16(32)
        b.u32(0x1fffffff)
        b.u32(0x20000000)
        b.u32(0x40000000)
        b.u32(0x80000000)
        b.zeros(10)
        const raw = new Uint8Array(tm.tiles.length * 4)
        const view = new DataView(raw.buffer)
        tm.tiles.forEach((t, i) => view.setUint32(i * 4, t, true))
        b.push(await deflate(raw))
        chunks.push(chunk(0x2005, b.bytes()))
        continue
      }
      const box = bbox(cel.data, doc.w, doc.h)
      if (!box) continue
      firstUse.set(cel, fi)
      const [x, y, w, h] = box
      const px = new Uint8Array(w * h * 4)
      for (let yy = 0; yy < h; yy++) px.set(cel.data.subarray(((y + yy) * doc.w + x) * 4, ((y + yy) * doc.w + x + w) * 4), yy * w * 4)
      b.i16(x)
      b.i16(y)
      b.u8(255)
      b.u16(2)
      b.i16(0)
      b.zeros(5)
      b.u16(w)
      b.u16(h)
      b.push(await deflate(encode(px, w * h)))
      chunks.push(chunk(0x2005, b.bytes()))
    }
    const body = new W()
    for (const c of chunks) body.push(c)
    const payload = body.bytes()
    const fh = new W()
    fh.u32(16 + payload.length)
    fh.u16(0xf1fa)
    fh.u16(Math.min(chunks.length, 0xffff))
    fh.u16(doc.frames[fi].duration)
    fh.zeros(2)
    fh.u32(chunks.length)
    fh.push(payload)
    frames.push(fh.bytes())
  }
  const total = 128 + frames.reduce((s, f) => s + f.length, 0)
  const hd = new W()
  hd.u32(total)
  hd.u16(0xa5e0)
  hd.u16(doc.frames.length)
  hd.u16(doc.w)
  hd.u16(doc.h)
  hd.u16(depth)
  hd.u32(1) // opacidade das camadas é válida
  hd.u16(doc.frames[0]?.duration ?? 100)
  hd.zeros(8)
  hd.u8(depth === 8 ? transparent : 0)
  hd.zeros(3)
  hd.u16(depth === 8 ? pal.length : 0)
  hd.u8(1)
  hd.u8(1)
  hd.i16(doc.grid.x)
  hd.i16(doc.grid.y)
  hd.u16(doc.grid.w)
  hd.u16(doc.grid.h)
  hd.zeros(84)
  const out = new W()
  out.push(hd.bytes())
  for (const f of frames) out.push(f)
  return out.bytes()
}

export type { Tag }

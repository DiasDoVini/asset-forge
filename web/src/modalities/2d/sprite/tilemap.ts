// Camadas de tilemap e tilesets (como no Aseprite 1.3): o cel guarda uma grade de índices; a imagem é renderizada a
// partir do tileset. Pintar pixels numa camada de tilemap edita os tiles (modo "manual": o tile muda em todo lugar;
// modo "auto": cria/reaproveita tiles, sem afetar as outras cópias). Tudo puro.
import { celKey, newCelImage, type CelImage, type Layer, type SpriteDoc, type Tilemap, type Tileset } from './doc'

export type PixelMode = 'auto' | 'manual'

export function newTileset(id: number, tw: number, th: number, name = `Tileset ${id}`): Tileset {
  return { id, name, tw, th, tiles: [new Uint8ClampedArray(tw * th * 4)] }
}

export const gridSize = (W: number, H: number, ts: Tileset) => ({ cols: Math.ceil(W / ts.tw), rows: Math.ceil(H / ts.th) })

export function emptyTilemap(W: number, H: number, ts: Tileset): Tilemap {
  const { cols, rows } = gridSize(W, H, ts)
  return { cols, rows, tiles: new Uint32Array(cols * rows) }
}

/** Imagem RGBA (W×H) dos tiles. Índices fora do tileset são tratados como vazio. */
export function renderTilemap(ts: Tileset, tm: Tilemap, W: number, H: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(W * H * 4)
  for (let r = 0; r < tm.rows; r++)
    for (let c = 0; c < tm.cols; c++) {
      const t = tm.tiles[r * tm.cols + c]
      const img = t > 0 ? ts.tiles[t] : undefined
      if (!img) continue
      for (let y = 0; y < ts.th; y++) {
        const ty = r * ts.th + y
        if (ty >= H) break
        const x0 = c * ts.tw
        const n = Math.min(ts.tw, W - x0)
        if (n <= 0) continue
        out.set(img.subarray(y * ts.tw * 4, (y * ts.tw + n) * 4), (ty * W + x0) * 4)
      }
    }
  return out
}

/** Recorta o tile (c, r) de uma imagem W×H (o que passa da borda fica transparente). */
export function cutTile(data: Uint8ClampedArray, W: number, H: number, tw: number, th: number, c: number, r: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(tw * th * 4)
  for (let y = 0; y < th; y++) {
    const sy = r * th + y
    if (sy >= H) break
    const x0 = c * tw
    const n = Math.min(tw, W - x0)
    if (n > 0) out.set(data.subarray((sy * W + x0) * 4, (sy * W + x0 + n) * 4), y * tw * 4)
  }
  return out
}

const empty = (t: Uint8ClampedArray) => {
  for (let i = 3; i < t.length; i += 4) if (t[i]) return false
  return true
}
const same = (a: Uint8ClampedArray, b: Uint8ClampedArray) => {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 4) {
    if (a[i + 3] === 0 && b[i + 3] === 0) continue
    if (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2] || a[i + 3] !== b[i + 3]) return false
  }
  return true
}

/** Hash simples de um tile (para achar tiles iguais rápido). */
function tileKey(t: Uint8ClampedArray): string {
  let h1 = 2166136261, h2 = 0
  for (let i = 0; i < t.length; i += 4) {
    const a = t[i + 3]
    const v = a ? ((t[i] << 24) | (t[i + 1] << 16) | (t[i + 2] << 8) | a) >>> 0 : 0
    h1 = Math.imul(h1 ^ v, 16777619)
    h2 = (h2 + v * (i + 1)) % 4294967291
  }
  return `${h1 >>> 0}:${h2}`
}

/** Índice de um tile igual no tileset (ou -1). */
function findTile(ts: Tileset, t: Uint8ClampedArray, index?: Map<string, number[]>): number {
  if (empty(t)) return 0
  const cands = index ? index.get(tileKey(t)) ?? [] : ts.tiles.map((_, i) => i)
  for (const i of cands) if (i > 0 && same(ts.tiles[i], t)) return i
  return -1
}

function buildIndex(ts: Tileset): Map<string, number[]> {
  const m = new Map<string, number[]>()
  ts.tiles.forEach((t, i) => {
    if (i === 0) return
    const k = tileKey(t)
    m.set(k, [...(m.get(k) ?? []), i])
  })
  return m
}

export const tilesetOf = (doc: SpriteDoc, layer: Layer) => doc.tilesets.find((t) => t.id === layer.tileset)

/** Troca um tileset e re-renderiza todos os cels de tilemap que o usam. */
export function replaceTileset(doc: SpriteDoc, ts: Tileset): SpriteDoc {
  const tilesets = doc.tilesets.map((t) => (t.id === ts.id ? ts : t))
  const layers = new Set(doc.layers.filter((l) => l.kind === 'tilemap' && l.tileset === ts.id).map((l) => l.id))
  const done = new Map<CelImage, CelImage>()
  const cels = { ...doc.cels }
  for (const [k, c] of Object.entries(doc.cels)) {
    if (!layers.has(Number(k.split(':')[0])) || !c.tilemap) continue
    if (!done.has(c)) done.set(c, { ...newCelImage(doc.w, doc.h, renderTilemap(ts, c.tilemap, doc.w, doc.h)), tilemap: c.tilemap })
    cels[k] = done.get(c)!
  }
  return { ...doc, tilesets, cels }
}

/** Grava a grade de tiles de um cel (e a imagem renderizada). */
export function setTilemap(doc: SpriteDoc, layerId: number, frame: number, tm: Tilemap): SpriteDoc {
  const layer = doc.layers.find((l) => l.id === layerId)
  const ts = layer && tilesetOf(doc, layer)
  if (!ts) return doc
  const old = doc.cels[celKey(layerId, frame)]
  const img: CelImage = { ...newCelImage(doc.w, doc.h, renderTilemap(ts, tm, doc.w, doc.h)), tilemap: tm }
  const cels = { ...doc.cels }
  if (old) {
    for (const [k, c] of Object.entries(doc.cels)) if (c === old) cels[k] = img // cels ligados
  } else cels[celKey(layerId, frame)] = img
  return { ...doc, cels }
}

export function getTilemap(doc: SpriteDoc, layer: Layer, frame: number): Tilemap | null {
  const ts = tilesetOf(doc, layer)
  if (!ts) return null
  return doc.cels[celKey(layer.id, frame)]?.tilemap ?? emptyTilemap(doc.w, doc.h, ts)
}

/**
 * Leva uma pintura em pixels (a imagem nova do cel inteiro) para a camada de tilemap.
 * manual: o tile editado muda em todas as cópias; auto: cada célula alterada recebe um tile igual existente ou um novo.
 */
export function applyPixels(doc: SpriteDoc, layerId: number, frame: number, data: Uint8ClampedArray, mode: PixelMode): SpriteDoc {
  const layer = doc.layers.find((l) => l.id === layerId)
  const ts0 = layer && tilesetOf(doc, layer)
  if (!layer || !ts0) return doc
  const tm0 = getTilemap(doc, layer, frame)!
  const before = doc.cels[celKey(layerId, frame)]?.data ?? new Uint8ClampedArray(doc.w * doc.h * 4)
  const tiles = [...ts0.tiles]
  const tm: Tilemap = { ...tm0, tiles: tm0.tiles.slice() }
  const index = buildIndex(ts0)
  let tsChanged = false
  for (let r = 0; r < tm.rows; r++)
    for (let c = 0; c < tm.cols; c++) {
      const now = cutTile(data, doc.w, doc.h, ts0.tw, ts0.th, c, r)
      const was = cutTile(before, doc.w, doc.h, ts0.tw, ts0.th, c, r)
      if (same(now, was)) continue
      const cur = tm.tiles[r * tm.cols + c]
      if (mode === 'manual' && cur > 0) {
        tiles[cur] = now
        tsChanged = true
        continue
      }
      let k = findTile({ ...ts0, tiles }, now, index)
      if (k < 0) {
        tiles.push(now)
        k = tiles.length - 1
        const key = tileKey(now)
        index.set(key, [...(index.get(key) ?? []), k])
        tsChanged = true
      }
      tm.tiles[r * tm.cols + c] = k
    }
  let d = doc
  if (tsChanged) d = replaceTileset(d, { ...ts0, tiles })
  return setTilemap(d, layerId, frame, tm)
}

/** Balde de tiles: troca a região contígua (4 vizinhos) de mesmo índice. */
export function fillTiles(tm: Tilemap, c: number, r: number, tile: number): Tilemap {
  if (c < 0 || r < 0 || c >= tm.cols || r >= tm.rows) return tm
  const target = tm.tiles[r * tm.cols + c]
  if (target === tile) return tm
  const out = tm.tiles.slice()
  const stack = [r * tm.cols + c]
  while (stack.length) {
    const i = stack.pop()!
    if (out[i] !== target) continue
    out[i] = tile
    const x = i % tm.cols
    const y = (i / tm.cols) | 0
    if (x > 0) stack.push(i - 1)
    if (x < tm.cols - 1) stack.push(i + 1)
    if (y > 0) stack.push(i - tm.cols)
    if (y < tm.rows - 1) stack.push(i + tm.cols)
  }
  return { ...tm, tiles: out }
}

/** Nova camada de tilemap (com tileset novo ou um existente), logo acima de `above`. */
export function addTilemapLayer(doc: SpriteDoc, above: number | null, tw: number, th: number, tilesetId?: number): { doc: SpriteDoc; id: number } {
  let tsId = tilesetId
  let tilesets = doc.tilesets
  if (tsId === undefined || !tilesets.some((t) => t.id === tsId)) {
    tsId = Math.max(0, ...tilesets.map((t) => t.id)) + 1
    tilesets = [...tilesets, newTileset(tsId, tw, th)]
  }
  const id = doc.nextId
  const ref = doc.layers.find((l) => l.id === above)
  const count = doc.layers.filter((l) => l.kind === 'tilemap').length + 1
  const layer: Layer = { id, name: `Tilemap ${count}`, kind: 'tilemap', tileset: tsId, parent: ref?.parent ?? null, visible: true, locked: false, opacity: 255, blend: 'normal' }
  const layers = [...doc.layers]
  layers.splice(ref ? layers.indexOf(ref) + 1 : layers.length, 0, layer)
  return { doc: { ...doc, layers, tilesets, nextId: id + 1 }, id }
}

/** Converte uma camada de imagem em tilemap: recorta todos os quadros em tiles, reaproveitando os iguais. */
export function convertToTilemap(doc: SpriteDoc, layerId: number, tw: number, th: number): SpriteDoc {
  const layer = doc.layers.find((l) => l.id === layerId)
  if (!layer || layer.kind !== 'image') return doc
  const tsId = Math.max(0, ...doc.tilesets.map((t) => t.id)) + 1
  const ts = newTileset(tsId, tw, th, `Tileset ${tsId}`)
  const index = new Map<string, number[]>()
  const { cols, rows } = gridSize(doc.w, doc.h, ts)
  const maps = new Map<CelImage, Tilemap>()
  for (const [k, c] of Object.entries(doc.cels)) {
    if (Number(k.split(':')[0]) !== layerId || maps.has(c)) continue
    const tm: Tilemap = { cols, rows, tiles: new Uint32Array(cols * rows) }
    for (let r = 0; r < rows; r++)
      for (let cc = 0; cc < cols; cc++) {
        const t = cutTile(c.data, doc.w, doc.h, tw, th, cc, r)
        let i = findTile(ts, t, index)
        if (i < 0) {
          ts.tiles.push(t)
          i = ts.tiles.length - 1
          const key = tileKey(t)
          index.set(key, [...(index.get(key) ?? []), i])
        }
        tm.tiles[r * cols + cc] = i
      }
    maps.set(c, tm)
  }
  const done = new Map<CelImage, CelImage>()
  const cels = { ...doc.cels }
  for (const [k, c] of Object.entries(doc.cels)) {
    const tm = maps.get(c)
    if (Number(k.split(':')[0]) !== layerId || !tm) continue
    if (!done.has(c)) done.set(c, { ...newCelImage(doc.w, doc.h, renderTilemap(ts, tm, doc.w, doc.h)), tilemap: tm })
    cels[k] = done.get(c)!
  }
  const layers = doc.layers.map((l) => (l.id === layerId ? { ...l, kind: 'tilemap' as const, tileset: tsId } : l))
  return { ...doc, layers, cels, tilesets: [...doc.tilesets, ts] }
}

/** Camada de tilemap → imagem comum (os pixels ficam; a grade é descartada). */
export function convertToImage(doc: SpriteDoc, layerId: number): SpriteDoc {
  const cels = { ...doc.cels }
  const done = new Map<CelImage, CelImage>()
  for (const [k, c] of Object.entries(doc.cels)) {
    if (Number(k.split(':')[0]) !== layerId) continue
    if (!done.has(c)) done.set(c, newCelImage(doc.w, doc.h, c.data))
    cels[k] = done.get(c)!
  }
  const layers = doc.layers.map((l) => (l.id === layerId ? { ...l, kind: 'image' as const, tileset: undefined } : l))
  return { ...doc, layers, cels }
}

/** Remove do tileset os tiles que nenhum cel usa (renumerando as grades). */
export function pruneTileset(doc: SpriteDoc, tsId: number): SpriteDoc {
  const ts = doc.tilesets.find((t) => t.id === tsId)
  if (!ts) return doc
  const layers = new Set(doc.layers.filter((l) => l.kind === 'tilemap' && l.tileset === tsId).map((l) => l.id))
  const used = new Set<number>([0])
  for (const [k, c] of Object.entries(doc.cels)) if (layers.has(Number(k.split(':')[0])) && c.tilemap) c.tilemap.tiles.forEach((t) => used.add(t))
  const keep = ts.tiles.map((_, i) => i).filter((i) => used.has(i))
  const remap = new Map(keep.map((old, i) => [old, i]))
  const done = new Map<CelImage, CelImage>()
  const cels = { ...doc.cels }
  for (const [k, c] of Object.entries(doc.cels)) {
    if (!layers.has(Number(k.split(':')[0])) || !c.tilemap) continue
    if (!done.has(c)) done.set(c, { ...c, tilemap: { ...c.tilemap, tiles: c.tilemap.tiles.map((t) => remap.get(t) ?? 0) } })
    cels[k] = done.get(c)!
  }
  return { ...doc, cels, tilesets: doc.tilesets.map((t) => (t.id === tsId ? { ...t, tiles: keep.map((i) => ts.tiles[i]) } : t)) }
}

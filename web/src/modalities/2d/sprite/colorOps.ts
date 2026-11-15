// Operações de cor no documento: trocar o modo (RGB/indexado/cinza), aplicar ajustes por escopo e editar a paleta
// remapeando os pixels (no modo indexado, mudar uma entrada muda todos os pixels daquela cor).
import { adjust, luma, makeNearest, quantize, reducePalette, remapColor, toGray, type Adjust, type ColorMode } from './color'
import { newCelImage, type CelImage, type SpriteDoc } from './doc'
import { replaceTileset } from './tilemap'
import type { RGBA } from './raster'

export type Scope = 'cel' | 'layer' | 'sprite'

type MapFn = (d: Uint8ClampedArray, w: number, h: number) => Uint8ClampedArray

/**
 * Aplica `fn` aos cels do escopo (cada imagem uma vez, ligações preservadas). Em camadas de tilemap, o ajuste vai nos
 * tiles do tileset (e vale para todas as cópias), que é o que o Aseprite faz.
 */
export function mapScope(doc: SpriteDoc, scope: Scope, layer: number, frame: number, fn: MapFn): SpriteDoc {
  const done = new Map<CelImage, CelImage>()
  const cels = { ...doc.cels }
  const tilemapLayers = new Map(doc.layers.filter((l) => l.kind === 'tilemap').map((l) => [l.id, l.tileset!]))
  const tilesetsHit = new Set<number>()
  for (const [k, c] of Object.entries(doc.cels)) {
    const [l, f] = k.split(':').map(Number)
    if (scope !== 'sprite' && l !== layer) continue
    if (scope === 'cel' && f !== frame) continue
    if (tilemapLayers.has(l)) {
      tilesetsHit.add(tilemapLayers.get(l)!)
      continue
    }
    if (!done.has(c)) {
      const next = fn(c.data, doc.w, doc.h)
      done.set(c, next === c.data ? c : newCelImage(doc.w, doc.h, next))
    }
    cels[k] = done.get(c)!
  }
  // um cel ligado fora do escopo continua apontando para a imagem antiga (como no Aseprite, ligação se desfaz)
  let d: SpriteDoc = { ...doc, cels }
  for (const id of tilesetsHit) {
    const ts = d.tilesets.find((t) => t.id === id)
    if (ts) d = replaceTileset(d, { ...ts, tiles: ts.tiles.map((t, i) => (i === 0 ? t : fn(t, ts.tw, ts.th))) })
  }
  return d
}

export function applyAdjust(doc: SpriteDoc, a: Adjust, scope: Scope, layer: number, frame: number, mask: Uint8Array | null): SpriteDoc {
  // a máscara de seleção só se aplica a imagens do tamanho do sprite (nos tiles, o ajuste vale para o tile todo)
  const d = mapScope(doc, scope, layer, frame, (data, w, h) => adjust(data, w, h, a, w === doc.w && h === doc.h ? mask : null))
  return enforceMode(d, scope, layer, frame)
}

/** Garante as regras do modo de cor nos cels do escopo (indexado: só cores da paleta; cinza: só tons de cinza). */
export function enforceMode(doc: SpriteDoc, scope: Scope = 'sprite', layer = 0, frame = 0): SpriteDoc {
  if (doc.colorMode === 'indexed' && doc.palette.some((c) => c[3] > 0))
    return mapScope(doc, scope, layer, frame, (d, w) => quantize(d, w, doc.palette))
  if (doc.colorMode === 'gray') return mapScope(doc, scope, layer, frame, (d) => toGray(d))
  return doc
}

/** Uma imagem de cel segue o modo do documento (usado ao gravar uma pintura). */
export function enforceData(doc: SpriteDoc, data: Uint8ClampedArray): Uint8ClampedArray {
  if (doc.colorMode === 'indexed' && doc.palette.some((c) => c[3] > 0)) return quantize(data, doc.w, doc.palette)
  if (doc.colorMode === 'gray') return toGray(data)
  return data
}

export function setColorMode(doc: SpriteDoc, mode: ColorMode, opts: { dither?: boolean; maxColors?: number } = {}): SpriteDoc {
  if (mode === doc.colorMode) return doc
  const all = [...new Set(Object.values(doc.cels))].map((c) => c.data)
  if (mode === 'indexed') {
    let palette: RGBA[] = doc.palette.some((c) => c[3] > 0) ? doc.palette : reducePalette(all, opts.maxColors ?? 32)
    if (!palette.length) palette = [[0, 0, 0, 255]]
    const d: SpriteDoc = { ...doc, colorMode: 'indexed', palette }
    return mapScope(d, 'sprite', 0, 0, (data, w) => quantize(data, w, d.palette, !!opts.dither))
  }
  if (mode === 'gray') {
    const palette = doc.palette.map(([r, g, b, a]) => {
      const v = luma(r, g, b)
      return [v, v, v, a] as RGBA
    })
    return mapScope({ ...doc, colorMode: 'gray', palette }, 'sprite', 0, 0, (data) => toGray(data))
  }
  return { ...doc, colorMode: 'rgb' }
}

/** Troca a entrada `index` da paleta; no modo indexado, os pixels daquela cor acompanham. */
export function setPaletteColor(doc: SpriteDoc, index: number, color: RGBA): SpriteDoc {
  const old = doc.palette[index]
  if (!old) return doc
  const palette = doc.palette.map((c, i) => (i === index ? color : c))
  let d: SpriteDoc = { ...doc, palette }
  if (doc.colorMode === 'indexed' && old[3] > 0) d = mapScope(d, 'sprite', 0, 0, (data) => remapColor(data, old, color))
  return d
}

/** Remove entradas; no modo indexado, os pixels delas vão para a cor mais próxima que sobrou. */
export function removePaletteColors(doc: SpriteDoc, indices: number[]): SpriteDoc {
  const gone = new Set(indices)
  const palette = doc.palette.filter((_, i) => !gone.has(i))
  const d: SpriteDoc = { ...doc, palette }
  if (doc.colorMode !== 'indexed' || !palette.some((c) => c[3] > 0)) return d
  return mapScope(d, 'sprite', 0, 0, (data, w) => quantize(data, w, palette))
}

/** Substitui a paleta inteira (importar/preset). No indexado, reencaixa os pixels na nova paleta. */
export function replacePalette(doc: SpriteDoc, palette: RGBA[], remap = true): SpriteDoc {
  const d: SpriteDoc = { ...doc, palette: palette.slice(0, 256) }
  if (!remap || doc.colorMode !== 'indexed' || !palette.some((c) => c[3] > 0)) return d
  return mapScope(d, 'sprite', 0, 0, (data, w) => quantize(data, w, d.palette))
}

/** Índice da cor na paleta (exato), ou -1. */
export function paletteIndexOf(doc: SpriteDoc, c: RGBA): number {
  return doc.palette.findIndex((p) => p[0] === c[0] && p[1] === c[1] && p[2] === c[2] && p[3] === c[3])
}

export function nearestInPalette(doc: SpriteDoc, c: RGBA): RGBA {
  const i = makeNearest(doc.palette)(c[0], c[1], c[2], c[3])
  return i >= 0 ? doc.palette[i] : c
}


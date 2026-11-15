// Documento de sprite do editor (camadas × quadros, cels, tags), no espírito do Aseprite/Pixelorama.
//
// Imutável: toda operação devolve um documento novo e reaproveita os buffers que não mudaram (copy-on-write),
// então o histórico de desfazer guarda documentos inteiros sem custo de cópia.
// Cel = imagem RGBA do tamanho do canvas (sem deslocamento). Cels ligados compartilham o mesmo `CelImage`
// (mesmo objeto): pintar em um altera todos os quadros da ligação.

export type Blend =
  | 'normal' | 'multiply' | 'screen' | 'overlay' | 'darken' | 'lighten' | 'color_dodge' | 'color_burn'
  | 'hard_light' | 'difference' | 'exclusion' | 'addition' | 'subtract' | 'divide'

export const BLENDS: { id: Blend; title: string }[] = [
  { id: 'normal', title: 'Normal' }, { id: 'multiply', title: 'Multiplicar' }, { id: 'screen', title: 'Tela' },
  { id: 'overlay', title: 'Sobrepor' }, { id: 'darken', title: 'Escurecer' }, { id: 'lighten', title: 'Clarear' },
  { id: 'color_dodge', title: 'Subexposição' }, { id: 'color_burn', title: 'Superexposição' },
  { id: 'hard_light', title: 'Luz forte' }, { id: 'difference', title: 'Diferença' }, { id: 'exclusion', title: 'Exclusão' },
  { id: 'addition', title: 'Adição' }, { id: 'subtract', title: 'Subtração' }, { id: 'divide', title: 'Divisão' },
]

/** Índices do formato .aseprite (BLEND_NAMES em forge_2d/aseprite.py). */
export const BLEND_INDEX: Record<Blend, number> = {
  normal: 0, multiply: 1, screen: 2, overlay: 3, darken: 4, lighten: 5, color_dodge: 6, color_burn: 7, hard_light: 8,
  difference: 10, exclusion: 11, addition: 16, subtract: 17, divide: 18,
}

import { adjust, type Adjust } from './color'

/** Efeito de camada não destrutivo (aplicado na hora de compor; o cel fica intacto). */
export type LayerEffect = { enabled: boolean; adjust: Adjust; thickness?: number }

/** Grade de tiles de um cel de camada de tilemap (índices no tileset; 0 = vazio). */
export type Tilemap = { cols: number; rows: number; tiles: Uint32Array }

/** Imagem de um cel. Em camadas de tilemap, `data` é a renderização dos tiles e `tilemap` é a fonte. */
export type CelImage = { id: number; data: Uint8ClampedArray; tilemap?: Tilemap } // RGBA w*h*4

export type Tileset = { id: number; name: string; tw: number; th: number; tiles: Uint8ClampedArray[] } // tiles[0] = vazio

/** Fatia (Aseprite): retângulo nomeado, com centro de 9-slice e pivô opcionais. */
export type Slice = {
  name: string
  color: string
  x: number; y: number; w: number; h: number
  center?: { x: number; y: number; w: number; h: number } // relativo à fatia
  pivot?: { x: number; y: number } // relativo à fatia
}

export type Grid = { x: number; y: number; w: number; h: number }

export type Layer = {
  id: number
  name: string
  kind: 'image' | 'group' | 'tilemap'
  tileset?: number // camadas de tilemap: id do tileset
  parent: number | null // id do grupo pai
  visible: boolean
  locked: boolean
  opacity: number // 0..255
  blend: Blend
  collapsed?: boolean
  /** máscara de recorte: só aparece onde a camada de baixo (a base, sem recorte) tem pixels */
  clip?: boolean
  effects?: LayerEffect[]
}

export type Frame = { duration: number } // ms

export type TagDir = 'forward' | 'reverse' | 'pingpong' | 'pingpong_reverse'
export type Tag = { name: string; from: number; to: number; direction: TagDir; repeat: number; color: string }

export type SpriteDoc = {
  w: number
  h: number
  /** paleta do sprite (no modo indexado, as únicas cores permitidas) */
  palette: [number, number, number, number][]
  colorMode: 'rgb' | 'indexed' | 'gray'
  tilesets: Tileset[]
  slices: Slice[]
  grid: Grid
  layers: Layer[] // ordem de baixo para cima; grupos antes dos filhos não é exigido
  frames: Frame[]
  cels: Record<string, CelImage> // chave `${layerId}:${frame}`
  tags: Tag[]
  nextId: number
}

let celSeq = 1
export const newCelImage = (w: number, h: number, data?: Uint8ClampedArray): CelImage => ({
  id: celSeq++,
  data: data ?? new Uint8ClampedArray(w * h * 4),
})

export const celKey = (layer: number, frame: number) => `${layer}:${frame}`

/** Cópia independente de um cel (pixels e, em tilemaps, a grade de tiles). */
export const cloneCel = (c: CelImage, w: number, h: number): CelImage => ({
  ...newCelImage(w, h, c.data.slice()),
  ...(c.tilemap ? { tilemap: { ...c.tilemap, tiles: c.tilemap.tiles.slice() } } : {}),
})

export function blankDoc(w: number, h: number, frames = 1): SpriteDoc {
  return {
    w, h, palette: [], colorMode: 'rgb', tilesets: [], slices: [], grid: { x: 0, y: 0, w: 16, h: 16 },
    layers: [{ id: 1, name: 'Camada 1', kind: 'image', parent: null, visible: true, locked: false, opacity: 255, blend: 'normal' }],
    frames: Array.from({ length: frames }, () => ({ duration: 100 })),
    cels: {},
    tags: [],
    nextId: 2,
  }
}

export function getCel(doc: SpriteDoc, layer: number, frame: number): CelImage | undefined {
  return doc.cels[celKey(layer, frame)]
}

export function isEmptyCel(c: CelImage | undefined): boolean {
  if (!c) return true
  const d = c.data
  for (let i = 3; i < d.length; i += 4) if (d[i]) return false
  return true
}

/** Grupos acima de uma camada (pai, avô…). */
export function ancestors(doc: SpriteDoc, layer: Layer): Layer[] {
  const out: Layer[] = []
  let p = layer.parent
  while (p !== null) {
    const g = doc.layers.find((l) => l.id === p)
    if (!g) break
    out.push(g)
    p = g.parent
  }
  return out
}

export function depthOf(doc: SpriteDoc, layer: Layer): number {
  return ancestors(doc, layer).length
}

export function effectiveVisible(doc: SpriteDoc, layer: Layer): boolean {
  return layer.visible && ancestors(doc, layer).every((g) => g.visible)
}

export function effectiveLocked(doc: SpriteDoc, layer: Layer): boolean {
  return layer.locked || ancestors(doc, layer).some((g) => g.locked)
}

/** Ordem de exibição no painel (de cima para baixo), respeitando a hierarquia dos grupos. */
export function displayOrder(doc: SpriteDoc): Layer[] {
  const children = (parent: number | null) => doc.layers.filter((l) => l.parent === parent).reverse()
  const out: Layer[] = []
  const walk = (parent: number | null) => {
    for (const l of children(parent)) {
      out.push(l)
      if (l.kind === 'group' && !l.collapsed) walk(l.id)
    }
  }
  walk(null)
  return out
}

/** Ordem de composição (de baixo para cima), só camadas de imagem, com a hierarquia linearizada. */
export function renderOrder(doc: SpriteDoc): Layer[] {
  const out: Layer[] = []
  const walk = (parent: number | null) => {
    for (const l of doc.layers.filter((x) => x.parent === parent)) {
      if (l.kind === 'group') walk(l.id)
      else out.push(l)
    }
  }
  walk(null)
  return out
}

// ---------------------------------------------------------------- composição

function blendChannel(mode: Blend, s: number, d: number): number {
  switch (mode) {
    case 'multiply': return s * d
    case 'screen': return s + d - s * d
    case 'overlay': return d <= 0.5 ? 2 * s * d : 1 - 2 * (1 - s) * (1 - d)
    case 'hard_light': return s <= 0.5 ? 2 * s * d : 1 - 2 * (1 - s) * (1 - d)
    case 'darken': return Math.min(s, d)
    case 'lighten': return Math.max(s, d)
    case 'difference': return Math.abs(d - s)
    case 'exclusion': return s + d - 2 * s * d
    case 'addition': return Math.min(1, s + d)
    case 'subtract': return Math.max(0, d - s)
    case 'color_dodge': return s >= 1 ? 1 : Math.min(1, d / (1 - s))
    case 'color_burn': return s <= 0 ? 0 : 1 - Math.min(1, (1 - d) / s)
    case 'divide': return s <= 0 ? 1 : Math.min(1, d / s)
    default: return s
  }
}

/** Compõe src (RGBA 0..255) sobre dst (floats 0..1, premultiplicação feita aqui) com opacidade e modo. */
export function compositeInto(dst: Float32Array, src: Uint8ClampedArray, opacity: number, mode: Blend) {
  const k = opacity / 255
  for (let i = 0; i < src.length; i += 4) {
    const sa = (src[i + 3] / 255) * k
    if (sa <= 0) continue
    const da = dst[i + 3]
    const ra = sa + da * (1 - sa)
    for (let c = 0; c < 3; c++) {
      const s = src[i + c] / 255
      const d = dst[i + c]
      const b = mode === 'normal' ? s : blendChannel(mode, s, d)
      const mixed = (1 - da) * s + da * b
      dst[i + c] = ra > 0 ? (sa * mixed + (1 - sa) * da * d) / ra : 0
    }
    dst[i + 3] = ra
  }
}

export type RenderOpts = {
  /** só estas camadas (ex.: prévia de uma camada); padrão: todas as visíveis */
  only?: Set<number>
  /** substitui a imagem de um cel durante um traço (prévia sem gravar no documento) */
  override?: { layer: number; data: Uint8ClampedArray }
  includeHidden?: boolean
}

// cache dos efeitos por imagem de cel (as imagens são imutáveis, então a chave é o próprio buffer)
const fxCache = new WeakMap<Uint8ClampedArray, Map<string, Uint8ClampedArray>>()

/** Cel com os efeitos da camada aplicados (sem efeitos, devolve o mesmo buffer). */
export function applyEffects(doc: SpriteDoc, layer: Layer, data: Uint8ClampedArray): Uint8ClampedArray {
  const fx = (layer.effects ?? []).filter((e) => e.enabled)
  if (!fx.length) return data
  const key = JSON.stringify(fx)
  let m = fxCache.get(data)
  const hit = m?.get(key)
  if (hit) return hit
  let d = data
  for (const e of fx) {
    const n = e.adjust.kind === 'outline' ? Math.max(1, Math.min(8, e.thickness ?? 1)) : 1
    for (let i = 0; i < n; i++) d = adjust(d, doc.w, doc.h, e.adjust)
  }
  if (!m) fxCache.set(data, (m = new Map()))
  if (m.size > 8) m.clear()
  m.set(key, d)
  return d
}

/** Corta `data` pelo alfa da base (máscara de recorte). */
function clipTo(data: Uint8ClampedArray, base: Uint8ClampedArray): Uint8ClampedArray {
  const out = data.slice()
  for (let i = 3; i < out.length; i += 4) out[i] = (out[i] * base[i]) / 255
  return out
}

/** Imagem RGBA do quadro composto. */
export function renderFrame(doc: SpriteDoc, frame: number, opts: RenderOpts = {}): Uint8ClampedArray {
  const acc = new Float32Array(doc.w * doc.h * 4)
  // base da máscara de recorte: a última camada sem recorte (com efeitos), se estiver visível
  let clipBase: Uint8ClampedArray | null = null
  for (const l of renderOrder(doc)) {
    const raw = opts.override?.layer === l.id ? opts.override.data : getCel(doc, l.id, frame)?.data
    const visible = (opts.includeHidden || effectiveVisible(doc, l)) && (!opts.only || opts.only.has(l.id))
    let data = raw ? applyEffects(doc, l, raw) : undefined
    if (!l.clip) clipBase = visible && data ? data : null
    else if (data) data = clipBase ? clipTo(data, clipBase) : undefined
    if (!visible || !data) continue
    const opacity = ancestors(doc, l).reduce((o, g) => (o * g.opacity) / 255, l.opacity)
    compositeInto(acc, data, opacity, l.blend)
  }
  const out = new Uint8ClampedArray(acc.length)
  for (let i = 0; i < acc.length; i += 4) {
    out[i] = acc[i] * 255 + 0.5
    out[i + 1] = acc[i + 1] * 255 + 0.5
    out[i + 2] = acc[i + 2] * 255 + 0.5
    out[i + 3] = acc[i + 3] * 255 + 0.5
  }
  return out
}

// ---------------------------------------------------------------- operações (puras)

const withLayers = (doc: SpriteDoc, layers: Layer[]): SpriteDoc => ({ ...doc, layers })

export function setCel(doc: SpriteDoc, layer: number, frame: number, img: CelImage | null): SpriteDoc {
  const cels = { ...doc.cels }
  const k = celKey(layer, frame)
  if (img) cels[k] = img
  else delete cels[k]
  return { ...doc, cels }
}

/** Grava pixels num cel. Num cel ligado, todos os quadros da ligação recebem a mesma imagem nova. */
export function writeCel(doc: SpriteDoc, layer: number, frame: number, data: Uint8ClampedArray): SpriteDoc {
  const old = getCel(doc, layer, frame)
  const img = newCelImage(doc.w, doc.h, data)
  const cels = { ...doc.cels }
  if (old) {
    for (const [k, c] of Object.entries(doc.cels)) if (c === old) cels[k] = img
  } else cels[celKey(layer, frame)] = img
  return { ...doc, cels }
}

export function updateLayer(doc: SpriteDoc, id: number, patch: Partial<Layer>): SpriteDoc {
  return withLayers(doc, doc.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)))
}

/** Nova camada logo acima de `above` (no mesmo grupo), ou no topo. */
export function addLayer(doc: SpriteDoc, above: number | null, kind: Layer['kind'] = 'image', name?: string): { doc: SpriteDoc; id: number } {
  const id = doc.nextId
  const ref = doc.layers.find((l) => l.id === above)
  const count = doc.layers.filter((l) => l.kind === kind).length + 1
  const layer: Layer = {
    id, name: name ?? (kind === 'group' ? `Grupo ${count}` : `Camada ${count}`), kind, parent: ref?.parent ?? null,
    visible: true, locked: false, opacity: 255, blend: 'normal',
  }
  const layers = [...doc.layers]
  const at = ref ? layers.indexOf(ref) + 1 : layers.length
  layers.splice(at, 0, layer)
  return { doc: { ...doc, layers, nextId: id + 1 }, id }
}

function descendants(doc: SpriteDoc, id: number): number[] {
  const out: number[] = []
  for (const l of doc.layers) if (l.parent === id) out.push(l.id, ...descendants(doc, l.id))
  return out
}

export function removeLayer(doc: SpriteDoc, id: number): SpriteDoc {
  const gone = new Set([id, ...descendants(doc, id)])
  const layers = doc.layers.filter((l) => !gone.has(l.id))
  if (!layers.some((l) => l.kind !== 'group')) return doc // sempre sobra uma camada desenhável
  const cels = Object.fromEntries(Object.entries(doc.cels).filter(([k]) => !gone.has(Number(k.split(':')[0]))))
  return { ...doc, layers, cels }
}

export function duplicateLayer(doc: SpriteDoc, id: number): { doc: SpriteDoc; id: number } {
  const src = doc.layers.find((l) => l.id === id)
  if (!src || src.kind === 'group') return { doc, id }
  const r = addLayer(doc, id, src.kind, `${src.name} cópia`)
  let d = updateLayer(r.doc, r.id, { opacity: src.opacity, blend: src.blend, visible: src.visible, tileset: src.tileset, clip: src.clip, effects: src.effects?.map((e) => ({ ...e })) })
  const copies = new Map<CelImage, CelImage>() // preserva as ligações entre quadros
  const cels = { ...d.cels }
  doc.frames.forEach((_, f) => {
    const c = getCel(doc, id, f)
    if (!c) return
    if (!copies.has(c)) copies.set(c, cloneCel(c, doc.w, doc.h))
    cels[celKey(r.id, f)] = copies.get(c)!
  })
  d = { ...d, cels }
  return { doc: d, id: r.id }
}

/** Sobe/desce uma posição entre as irmãs (mesmo grupo). */
export function moveLayer(doc: SpriteDoc, id: number, dir: 1 | -1): SpriteDoc {
  const l = doc.layers.find((x) => x.id === id)
  if (!l) return doc
  const siblings = doc.layers.filter((x) => x.parent === l.parent)
  const i = siblings.indexOf(l)
  const j = i + dir
  if (j < 0 || j >= siblings.length) return doc
  const other = siblings[j]
  const layers = [...doc.layers]
  const a = layers.indexOf(l)
  const b = layers.indexOf(other)
  layers[a] = other
  layers[b] = l
  return withLayers(doc, layers)
}

/** Põe a camada dentro de um grupo (ou na raiz com null). */
export function setParent(doc: SpriteDoc, id: number, parent: number | null): SpriteDoc {
  if (parent === id || (parent !== null && descendants(doc, id).includes(parent))) return doc
  return updateLayer(doc, id, { parent })
}

/** Junta a camada com a de baixo (mesmo grupo), compondo quadro a quadro. */
export function mergeDown(doc: SpriteDoc, id: number): SpriteDoc {
  const l = doc.layers.find((x) => x.id === id)
  if (!l || l.kind !== 'image') return doc
  const siblings = doc.layers.filter((x) => x.parent === l.parent)
  const below = siblings[siblings.indexOf(l) - 1]
  if (!below || below.kind !== 'image') return doc
  let d = doc
  doc.frames.forEach((_, f) => {
    const top = getCel(doc, l.id, f)
    if (!top) return
    const base = getCel(doc, below.id, f)
    const acc = new Float32Array(doc.w * doc.h * 4)
    // os efeitos das duas camadas são "assados" no resultado (a camada mesclada fica sem efeitos)
    if (base) compositeInto(acc, applyEffects(doc, below, base.data), below.opacity, 'normal')
    compositeInto(acc, applyEffects(doc, l, top.data), l.opacity, l.blend)
    const out = new Uint8ClampedArray(acc.length)
    for (let i = 0; i < acc.length; i++) out[i] = acc[i] * 255 + 0.5
    d = setCel(d, below.id, f, newCelImage(doc.w, doc.h, out))
  })
  d = updateLayer(d, below.id, { opacity: 255, effects: undefined })
  return removeLayer(d, l.id)
}

function shiftKeys(doc: SpriteDoc, fn: (frame: number) => number | null): Record<string, CelImage> {
  const cels: Record<string, CelImage> = {}
  for (const [k, c] of Object.entries(doc.cels)) {
    const [l, f] = k.split(':').map(Number)
    const nf = fn(f)
    if (nf !== null) cels[celKey(l, nf)] = c
  }
  return cels
}

function shiftTags(tags: Tag[], fn: (frame: number) => number | null, total: number): Tag[] {
  const out: Tag[] = []
  for (const t of tags) {
    let from = fn(t.from)
    let to = fn(t.to)
    if (from === null && to === null) continue
    from = from ?? to!
    to = to ?? from
    out.push({ ...t, from: Math.max(0, Math.min(from, total - 1)), to: Math.max(0, Math.min(to, total - 1)) })
  }
  return out.filter((t) => t.from <= t.to)
}

/** Insere um quadro depois de `after`; `copy` duplica os cels do quadro `after` (`linked` mantém ligados). */
export function insertFrame(doc: SpriteDoc, after: number, copy: 'empty' | 'duplicate' | 'linked' = 'empty'): SpriteDoc {
  const at = after + 1
  const frames = [...doc.frames]
  frames.splice(at, 0, { duration: doc.frames[after]?.duration ?? 100 })
  const cels = shiftKeys(doc, (f) => (f >= at ? f + 1 : f))
  if (copy !== 'empty')
    for (const l of doc.layers) {
      const c = getCel(doc, l.id, after)
      if (c) cels[celKey(l.id, at)] = copy === 'linked' ? c : cloneCel(c, doc.w, doc.h)
    }
  const tags = doc.tags.map((t) => ({ ...t, from: t.from >= at ? t.from + 1 : t.from, to: t.to >= after ? t.to + 1 : t.to }))
  return { ...doc, frames, cels, tags }
}

export function removeFrame(doc: SpriteDoc, frame: number): SpriteDoc {
  if (doc.frames.length <= 1) return doc
  const frames = doc.frames.filter((_, i) => i !== frame)
  const map = (f: number) => (f === frame ? null : f > frame ? f - 1 : f)
  const tags = shiftTags(doc.tags, (f) => (f === frame ? (f > 0 ? f - 1 : 0) : f > frame ? f - 1 : f), frames.length)
  return { ...doc, frames, cels: shiftKeys(doc, map), tags }
}

/** Move o quadro `from` para a posição `to` (arrastar na linha do tempo). */
export function moveFrame(doc: SpriteDoc, from: number, to: number): SpriteDoc {
  if (from === to || to < 0 || to >= doc.frames.length) return doc
  const order = doc.frames.map((_, i) => i)
  order.splice(to, 0, order.splice(from, 1)[0])
  const newIndex = new Map(order.map((old, i) => [old, i]))
  const frames = order.map((i) => doc.frames[i])
  return { ...doc, frames, cels: shiftKeys(doc, (f) => newIndex.get(f) ?? f) }
}

export function setDuration(doc: SpriteDoc, frames: number[], ms: number): SpriteDoc {
  const set = new Set(frames)
  return { ...doc, frames: doc.frames.map((f, i) => (set.has(i) ? { duration: Math.max(1, Math.min(65535, Math.round(ms))) } : f)) }
}

/** Liga os cels da camada nos quadros dados ao primeiro deles. */
export function linkCels(doc: SpriteDoc, layer: number, frames: number[]): SpriteDoc {
  const [first, ...rest] = [...frames].sort((a, b) => a - b)
  const src = getCel(doc, layer, first)
  if (!src) return doc
  const cels = { ...doc.cels }
  for (const f of rest) cels[celKey(layer, f)] = src
  return { ...doc, cels }
}

export function unlinkCel(doc: SpriteDoc, layer: number, frame: number): SpriteDoc {
  const c = getCel(doc, layer, frame)
  if (!c) return doc
  return setCel(doc, layer, frame, cloneCel(c, doc.w, doc.h))
}

export function isLinked(doc: SpriteDoc, layer: number, frame: number): boolean {
  const c = getCel(doc, layer, frame)
  if (!c) return false
  return doc.frames.some((_, f) => f !== frame && getCel(doc, layer, f) === c)
}

const TAG_COLORS = ['#f59e0b', '#22c55e', '#3b82f6', '#ec4899', '#a855f7', '#14b8a6', '#ef4444']
export function addTag(doc: SpriteDoc, from: number, to: number, name?: string): SpriteDoc {
  const tag: Tag = {
    name: name ?? `tag${doc.tags.length + 1}`, from: Math.min(from, to), to: Math.max(from, to), direction: 'forward', repeat: 0,
    color: TAG_COLORS[doc.tags.length % TAG_COLORS.length],
  }
  return { ...doc, tags: [...doc.tags, tag] }
}

export function updateTag(doc: SpriteDoc, index: number, patch: Partial<Tag>): SpriteDoc {
  return { ...doc, tags: doc.tags.map((t, i) => (i === index ? { ...t, ...patch } : t)) }
}

export function removeTag(doc: SpriteDoc, index: number): SpriteDoc {
  return { ...doc, tags: doc.tags.filter((_, i) => i !== index) }
}

/** Sequência de reprodução de uma tag (ou do sprite todo), já com a direção aplicada. */
export function playSequence(doc: SpriteDoc, tag: Tag | null): number[] {
  const from = tag?.from ?? 0
  const to = tag?.to ?? doc.frames.length - 1
  const fwd = Array.from({ length: to - from + 1 }, (_, i) => from + i)
  switch (tag?.direction) {
    case 'reverse': return [...fwd].reverse()
    case 'pingpong': return fwd.length > 1 ? [...fwd, ...fwd.slice(1, -1).reverse()] : fwd
    case 'pingpong_reverse': {
      const r = [...fwd].reverse()
      return r.length > 1 ? [...r, ...r.slice(1, -1).reverse()] : r
    }
    default: return fwd
  }
}

/** Documento de 1 camada a partir de quadros RGBA (imagem ou animação existente). */
export function docFromFrames(w: number, h: number, frames: Uint8ClampedArray[], durations: number[], tags: Tag[] = []): SpriteDoc {
  const doc = blankDoc(w, h, Math.max(1, frames.length))
  doc.frames = frames.map((_, i) => ({ duration: durations[i] ?? 100 }))
  frames.forEach((data, i) => {
    doc.cels[celKey(1, i)] = newCelImage(w, h, data)
  })
  doc.tags = tags
  return doc
}

// ---------------------------------------------------------------- sprite inteiro (Fase 3)

/** Aplica `fn` em cada imagem de cel uma vez só (cels ligados continuam ligados) e muda o tamanho do documento. */
export function mapCels(doc: SpriteDoc, w: number, h: number, fn: (data: Uint8ClampedArray) => Uint8ClampedArray): SpriteDoc {
  const done = new Map<CelImage, CelImage>()
  const cels: Record<string, CelImage> = {}
  for (const [k, c] of Object.entries(doc.cels)) {
    if (!done.has(c)) done.set(c, newCelImage(w, h, fn(c.data)))
    cels[k] = done.get(c)!
  }
  const layers = doc.layers.map((l) => (l.kind === 'tilemap' ? { ...l, kind: 'image' as const, tileset: undefined } : l))
  const slices = w === doc.w && h === doc.h ? doc.slices : []
  return { ...doc, w, h, cels, layers, slices }
}

/** Novo tamanho de canvas; o conteúdo é deslocado por (dx, dy) (âncora) e o que sobra é cortado. */
export function resizeCanvas(doc: SpriteDoc, w: number, h: number, dx: number, dy: number): SpriteDoc {
  return mapCels(doc, w, h, (src) => {
    const out = new Uint8ClampedArray(w * h * 4)
    for (let y = 0; y < doc.h; y++) {
      const ty = y + dy
      if (ty < 0 || ty >= h) continue
      for (let x = 0; x < doc.w; x++) {
        const tx = x + dx
        if (tx < 0 || tx >= w) continue
        out.set(src.subarray((y * doc.w + x) * 4, (y * doc.w + x) * 4 + 4), (ty * w + tx) * 4)
      }
    }
    return out
  })
}

/** Deslocamento para uma âncora 3×3 (ax, ay em 0..2: esquerda/centro/direita, topo/meio/base). */
export function anchorOffset(doc: SpriteDoc, w: number, h: number, ax: number, ay: number): [number, number] {
  return [Math.round(((w - doc.w) * ax) / 2), Math.round(((h - doc.h) * ay) / 2)]
}

/** Menor retângulo com conteúdo em algum cel (para "aparar"). */
export function contentBounds(doc: SpriteDoc): { x: number; y: number; w: number; h: number } | null {
  let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1
  for (const c of new Set(Object.values(doc.cels)))
    for (let i = 3; i < c.data.length; i += 4)
      if (c.data[i]) {
        const p = (i - 3) / 4
        const x = p % doc.w
        const y = (p / doc.w) | 0
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 }
}

export function cropDoc(doc: SpriteDoc, r: { x: number; y: number; w: number; h: number }): SpriteDoc {
  return resizeCanvas(doc, r.w, r.h, -r.x, -r.y)
}

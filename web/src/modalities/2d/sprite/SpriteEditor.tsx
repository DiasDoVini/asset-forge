// Editor de sprite com camadas e quadros (aba "Sprite" dos assets 2D): canvas com zoom/pan, ferramentas de desenho
// (lápis pixel-perfect, pincéis, inks, simetria, linha, curva, retângulo, elipse, polígono, contorno, balde, gradiente),
// painel de camadas, linha do tempo com tags, onion skin e reprodução. Salva como .aseprite (nova versão do asset).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import {
  ArrowLeftRight, Blend, Brush, Circle, CircleDashed, Download, Eraser, Grid3x3, Hexagon, Keyboard, Lasso, LassoSelect, Loader2, Minus, Move,
  PaintBucket, Palette, Pipette, Plus, Redo2, Save, Slash, Slice as SliceIcon, Spline, Square, SquareDashed, Type, Undo2,
  WandSparkles, Waypoints,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, downloadBlob } from '@/core/api'
import type { PanelProps } from '@/core/registry'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import type { Pt } from '@/modalities/pixel/tools'
import { addLayer, effectiveLocked, effectiveVisible, getCel, insertFrame, playSequence, renderFrame, writeCel, type Slice, type SpriteDoc, type Tilemap } from './doc'
import { SlicePanel, TilesetPanel } from './SidePanels'
import { EffectsPanel } from './EffectsPanel'
import { TextDialog } from './TextDialog'
import { applyPixels, fillTiles, getTilemap, renderTilemap, setTilemap, tilesetOf, type PixelMode } from './tilemap'
import { PreviewWindow, ReferencePanel, Rulers, ViewMenu } from './ViewPanels'
import { useViewState } from './viewState'
import { docBlob, loadDoc, saveAsVersion } from './io'
import { LayersPanel } from './LayersPanel'
import { applyInk, brushOffsets, fillRegion, stabilize } from './paint'
import {
  bounds, cleared, clipToMask, colorMask, combine, ellipseMask, extract, invert, isEmptyMask, maskFromImage, outline, polygonMask,
  rectMask, type Img, type Mask, type SelMode,
} from './selection'
import { SpriteMenu } from './SpriteMenu'
import { ColorMenu } from './ColorMenu'
import { enforceData } from './colorOps'
import { PalettePanel } from './PalettePanel'
import { flip as flipImg, stampOver, transformImage } from './transform'
import { hexToRgba, rgbaToHex, shifted, tinted, type RGBA } from './raster'
import { Timeline, type Onion } from './Timeline'
import { ToolOptionsBar, TransformBar, type FloatState } from './ToolOptionsBar'
import { SELECT_TOOLS, strokeResult, TOOL_DEFS, useToolOptions, type StrokeState, type Tool } from './tools'
import { useDocHistory } from './useDocHistory'
import { comboOf, keyHint, keymap, useBindings } from './shortcuts'
import { ShortcutsDialog } from './ShortcutsDialog'

const ICONS: Record<Tool, ReactNode> = {
  pencil: <Brush />, eraser: <Eraser />, fill: <PaintBucket />, gradient: <Blend />, picker: <Pipette />, line: <Slash />,
  curve: <Spline />, rect: <Square />, ellipse: <Circle />, polygon: <Hexagon />, contour: <Lasso />, move: <Move />,
  sel_rect: <SquareDashed />, sel_ellipse: <CircleDashed />, lasso: <LassoSelect />, poly_lasso: <Waypoints />, wand: <WandSparkles />,
  sel_color: <Palette />, slice: <SliceIcon />, text: <Type />,
}

// DawnBringer 16 — paleta padrão quando o projeto não tem uma
const DB16 = ['#140c1c', '#442434', '#30346d', '#4e4a4e', '#854c30', '#346524', '#d04648', '#757161', '#597dce', '#d27d2c', '#8595a1', '#6daa2c', '#d2aa99', '#6dc2ca', '#dad45e', '#deeed6']

type View = { z: number; ox: number; oy: number }
type Curve = { s: StrokeState; pts: [Pt, Pt, Pt, Pt]; stage: 1 | 2 } // 1: ajustando o controle 1; 2: o controle 2
type Poly = { s: StrokeState; verts: Pt[] }
/** Seleção flutuante (colada ou levantada da seleção) sendo movida/escalada/girada. cx, cy: centro em px do documento. */
type Floating = {
  src: Img; cx: number; cy: number; sx: number; sy: number; angle: number
  base: Uint8ClampedArray // o cel por baixo (sem os pixels levantados)
  layer: number; frame: number; lifted: boolean
}
type FloatDrag = { mode: 'move' | 'scale' | 'rotate'; hx: number; hy: number; start: [number, number]; f0: Floating }
type Clip = { img: Img; x: number; y: number }

/** Modo de seleção pelos modificadores (Shift soma, Alt subtrai, os dois intersectam), senão o da barra. */
function selModeOf(e: { shiftKey: boolean; altKey: boolean }, fallback: SelMode): SelMode {
  if (e.shiftKey && e.altKey) return 'intersect'
  if (e.shiftKey) return 'add'
  if (e.altKey) return 'subtract'
  return fallback
}

const FREEHAND: Tool[] = ['pencil', 'eraser', 'contour']
const SHAPES: Tool[] = ['line', 'rect', 'ellipse', 'gradient']

export function SpriteEditor({ asset, project }: PanelProps) {
  const hist = useDocHistory()
  const doc = hist.doc
  const qc = useQueryClient()
  const nav = useNavigate()
  const { opts, set: setOpt } = useToolOptions()
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [layer, setLayer] = useState(1)
  const [frame, setFrame] = useState(0)
  const [range, setRange] = useState<[number, number]>([0, 0])
  const [tool, setTool] = useState<Tool>('pencil')
  const [fg, setFg] = useState<RGBA>([20, 12, 28, 255])
  const [bg, setBg] = useState<RGBA>([222, 238, 214, 255])
  const [showGrid, setShowGrid] = useState(true)
  const [onion, setOnion] = useState<Onion>({ on: false, before: 1, after: 1, opacity: 0.35 })
  const [playing, setPlaying] = useState(false)
  const [loopTag, setLoopTag] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [view, setView] = useState<View>({ z: 8, ox: 0, oy: 0 })
  const [hover, setHover] = useState<Pt | null>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  // o traço guarda a camada e o quadro onde começou (dá para desenhar com a animação tocando)
  const stroke = useRef<{ s: StrokeState; work: Uint8ClampedArray; stab: [number, number]; layer: number; frame: number } | null>(null)
  const [paintFollow, setPaintFollow] = useState(false) // tocando: o traço continua em cada quadro que passa
  const curve = useRef<Curve | null>(null)
  const poly = useRef<Poly | null>(null)
  const pan = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const space = useRef(false)
  const lastPoint = useRef<Pt | null>(null)
  const [sel, setSel] = useState<Mask | null>(null)
  const selDraft = useRef<{ mode: SelMode; start: Pt; pts: Pt[]; mask: Mask | null } | null>(null)
  const polyLasso = useRef<{ mode: SelMode; verts: Pt[] } | null>(null)
  const floating = useRef<Floating | null>(null)
  const floatDrag = useRef<FloatDrag | null>(null)
  const clip = useRef<Clip | null>(null)
  const sysClipOk = useRef(false)
  const pasteWanted = useRef<number | null>(null)
  const [tick, setTick] = useState(0) // redesenho durante o traço
  const [previewDoc, setPreviewDoc] = useState<SpriteDoc | null>(null) // prévia dos ajustes de cor
  const { view: vs, setView: setVs } = useViewState(asset.slug)
  const [selSlice, setSelSlice] = useState<number | null>(null)
  const sliceDrag = useRef<{ mode: 'new' | 'move'; start: Pt; index: number; draft: Slice | null } | null>(null)
  const [tile, setTile] = useState(1)
  const [tileMode, setTileMode] = useState(false)
  const [pixelMode, setPixelMode] = useState<PixelMode>('auto')
  const tileStroke = useRef<{ tm: Tilemap; erase: boolean } | null>(null)
  const [textAt, setTextAt] = useState<Pt | null>(null)
  const refDrag = useRef<{ x: number; y: number; rx: number; ry: number } | null>(null)
  const [refEl, setRefEl] = useState<HTMLImageElement | null>(null)
  const projectAssets = useQuery({ queryKey: ['assets', project.slug, 'ref-images'], queryFn: () => api.assets(project.slug, { modality: '2d' }), enabled: vs.refPanel })
  const projectImages = (projectAssets.data ?? []).filter((a) => a.primary_url && /\.(png|jpe?g|webp|gif)$/i.test(a.primary_file ?? '')).map((a) => ({ name: a.name || a.slug, url: a.primary_url! }))
  const projectPalette = useMemo(() => ((project.style as { palette?: string[] } | undefined)?.palette ?? []).map((c) => hexToRgba(c)), [project.style])
  // rampa do ink de sombreamento: a paleta do sprite, na ordem
  const ramp = useMemo(() => (doc?.palette ?? []).filter((c) => c[3] > 0), [doc?.palette])
  const saveProjectPalette = async (p: RGBA[]) => {
    await api.patchProject(project.slug, { style: { ...project.style, palette: p.filter((c) => c[3] > 0).map((c) => rgbaToHex(c)) } })
    qc.invalidateQueries({ queryKey: ['project', project.slug] })
  }
  const redraw = () => setTick((t) => t + 1)
  // imagem de referência carregada para desenhar no canvas
  useEffect(() => {
    const src = vs.reference?.src
    if (!src) return setRefEl(null)
    const im = new Image()
    im.crossOrigin = 'anonymous'
    im.onload = () => setRefEl(im)
    im.onerror = () => setRefEl(null)
    im.src = src
  }, [vs.reference?.src])

  // ---- abrir -----------------------------------------------------------------
  useEffect(() => {
    let alive = true
    setLoadErr(null)
    loadDoc(asset).then(
      (r) => {
        if (!alive) return
        // sprite sem paleta: começa com a do projeto (ou DB16)
        if (!r.doc.palette.length) r.doc.palette = (projectPalette.length ? projectPalette : DB16.map((c) => hexToRgba(c))).slice(0, 256)
        hist.reset(r.doc)
        const first = [...r.doc.layers].reverse().find((l) => l.kind === 'image')
        setLayer(first?.id ?? 1)
        setFrame(0)
        setRange([0, 0])
        setOpt('symmetry', { ...opts.symmetry, x: r.doc.w / 2, y: r.doc.h / 2 })
        if (r.skipped.length) toast.warning(`Ignorado na abertura: ${r.skipped.join(', ')}`)
        requestAnimationFrame(() => {
          const box = wrap.current?.getBoundingClientRect()
          if (!box) return
          const z = Math.max(1, Math.floor(Math.min((box.width - 40) / r.doc.w, (box.height - 40) / r.doc.h)))
          setView({ z, ox: Math.round((box.width - r.doc.w * z) / 2), oy: Math.round((box.height - r.doc.h * z) / 2) })
        })
      },
      (e) => alive && setLoadErr(String(e)),
    )
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset.ref])

  useEffect(() => {
    if (!hist.dirty) return
    const h = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [hist.dirty])

  // trocar de ferramenta/quadro/camada cancela curva e polígono pela metade
  useEffect(() => {
    curve.current = null
    poly.current = null
    polyLasso.current = null
    if (floating.current && (tool !== 'move' || floating.current.frame !== frame || floating.current.layer !== layer)) commitFloating()
    redraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, frame, layer])
  // documento novo (outro tamanho, outra versão): a seleção não vale mais
  useEffect(() => {
    if (doc && sel && sel.length !== doc.w * doc.h) setSel(null)
  }, [doc, sel])

  // ---- reprodução ------------------------------------------------------------
  useEffect(() => {
    if (!playing || !doc) return
    const seq = playSequence(doc, loopTag !== null ? doc.tags[loopTag] ?? null : null)
    if (!seq.length) return
    let i = Math.max(0, seq.indexOf(frame))
    let t: number
    const step = () => {
      i = (i + 1) % seq.length
      setFrame(seq[i])
      t = window.setTimeout(step, doc.frames[seq[i]]?.duration ?? 100)
    }
    t = window.setTimeout(step, doc.frames[seq[i]]?.duration ?? 100)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, loopTag, doc?.frames, doc?.tags])

  // ---- render ------------------------------------------------------------------
  const toCanvas = (d: SpriteDoc, data: Uint8ClampedArray) => {
    const c = document.createElement('canvas')
    c.width = d.w
    c.height = d.h
    c.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(data), d.w, d.h), 0, 0)
    return c
  }
  const onionLayers = useMemo(() => {
    if (!doc || !onion.on || playing) return []
    const out: { c: HTMLCanvasElement; a: number }[] = []
    for (let k = onion.before; k >= 1; k--) {
      const f = frame - k
      if (f >= 0) out.push({ c: toCanvas(doc, tinted(renderFrame(doc, f), [255, 60, 60], 1)), a: onion.opacity * (1 - (k - 1) / (onion.before + 1)) })
    }
    for (let k = onion.after; k >= 1; k--) {
      const f = frame + k
      if (f < doc.frames.length) out.push({ c: toCanvas(doc, tinted(renderFrame(doc, f), [60, 140, 255], 1)), a: onion.opacity * (1 - (k - 1) / (onion.after + 1)) })
    }
    return out
  }, [doc, frame, onion, playing])

  const floatRender = () => {
    const f = floating.current
    if (!f) return null
    const r = transformImage(f.src, { sx: f.sx, sy: f.sy, angle: f.angle, algo: opts.algo })
    return { img: r as Img, left: Math.round(f.cx + r.dx), top: Math.round(f.cy + r.dy) }
  }
  const preview = (): Uint8ClampedArray | null => {
    if (stroke.current) return stroke.current.frame === frame && stroke.current.layer === layer ? stroke.current.work : null
    if (!doc) return null
    if (tileStroke.current) {
      const L = doc.layers.find((l) => l.id === layer)
      const ts = L && tilesetOf(doc, L)
      if (ts) return renderTilemap(ts, tileStroke.current.tm, doc.w, doc.h)
    }
    const f = floating.current
    if (f && f.frame === frame && f.layer === layer) {
      const r = floatRender()!
      const out = f.base.slice()
      stampOver(out, doc.w, doc.h, r.img, r.left, r.top)
      return out
    }
    const ctx = { w: doc.w, h: doc.h, opts, ramp }
    if (curve.current) return clipSel(strokeResult(curve.current.s, ctx, { curve: curve.current.pts }), curve.current.s.base)
    if (poly.current) {
      const verts = hover ? [...poly.current.verts, hover] : poly.current.verts
      return clipSel(strokeResult(poly.current.s, ctx, { polygon: verts }), poly.current.s.base)
    }
    return null
  }

  useLayoutEffect(() => {
    const c = canvas.current
    const box = wrap.current
    if (!c || !box || !doc) return
    const dpr = window.devicePixelRatio || 1
    const r = box.getBoundingClientRect()
    if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) {
      c.width = Math.round(r.width * dpr)
      c.height = Math.round(r.height * dpr)
      c.style.width = `${r.width}px`
      c.style.height = `${r.height}px`
    }
    const g = c.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, r.width, r.height)
    const { z, ox, oy } = view
    const W = doc.w * z
    const H = doc.h * z
    const cell = Math.max(4, Math.min(16, z * 2))
    const work = preview()
    const shown = previewDoc ?? doc
    const comp = toCanvas(shown, renderFrame(shown, frame, work && !previewDoc ? { override: { layer, data: work } } : {}))
    g.imageSmoothingEnabled = false
    // modo tiled: cópias em volta (antes do clip do canvas principal)
    if (vs.tiled !== 'none') {
      const xs = vs.tiled === 'y' ? [0] : [-1, 0, 1]
      const ys = vs.tiled === 'x' ? [0] : [-1, 0, 1]
      g.globalAlpha = 0.9
      for (const dy of ys) for (const dx of xs) if (dx || dy) g.drawImage(comp, ox + dx * W, oy + dy * H, W, H)
      g.globalAlpha = 1
    }
    const ref = vs.reference
    const drawRef = () => {
      if (!ref || !ref.visible || !refEl) return
      g.globalAlpha = ref.opacity
      g.imageSmoothingEnabled = ref.scale * z < 1
      g.drawImage(refEl, ox + ref.x * z, oy + ref.y * z, refEl.naturalWidth * ref.scale * z, refEl.naturalHeight * ref.scale * z)
      g.imageSmoothingEnabled = false
      g.globalAlpha = 1
    }
    g.save()
    g.beginPath()
    g.rect(ox, oy, W, H)
    g.clip()
    for (let y = 0; y < H; y += cell)
      for (let x = 0; x < W; x += cell) {
        g.fillStyle = ((x / cell + y / cell) & 1) === 0 ? '#2b2b2b' : '#383838'
        g.fillRect(ox + x, oy + y, cell, cell)
      }
    g.imageSmoothingEnabled = false
    if (ref && !ref.above) drawRef()
    for (const o of onionLayers) {
      g.globalAlpha = o.a
      g.drawImage(o.c, ox, oy, W, H)
    }
    g.globalAlpha = 1
    g.drawImage(comp, ox, oy, W, H)
    g.restore()
    if (ref?.above) drawRef()
    if (showGrid && z >= 6) {
      g.strokeStyle = 'rgba(255,255,255,0.08)'
      g.lineWidth = 1
      g.beginPath()
      for (let x = 0; x <= doc.w; x++) {
        g.moveTo(Math.round(ox + x * z) + 0.5, oy)
        g.lineTo(Math.round(ox + x * z) + 0.5, oy + H)
      }
      for (let y = 0; y <= doc.h; y++) {
        g.moveTo(ox, Math.round(oy + y * z) + 0.5)
        g.lineTo(ox + W, Math.round(oy + y * z) + 0.5)
      }
      g.stroke()
    }
    g.strokeStyle = 'rgba(255,255,255,0.35)'
    g.strokeRect(ox - 0.5, oy - 0.5, W + 1, H + 1)
    // grade do sprite e grade de tiles da camada de tilemap
    const gridLines = (gw: number, gh: number, gx: number, gy: number, color: string) => {
      if (gw * z < 4 || gh * z < 4) return
      g.strokeStyle = color
      g.lineWidth = 1
      g.beginPath()
      for (let x = ((gx % gw) + gw) % gw; x <= doc.w; x += gw) {
        g.moveTo(Math.round(ox + x * z) + 0.5, oy)
        g.lineTo(Math.round(ox + x * z) + 0.5, oy + H)
      }
      for (let y = ((gy % gh) + gh) % gh; y <= doc.h; y += gh) {
        g.moveTo(ox, Math.round(oy + y * z) + 0.5)
        g.lineTo(ox + W, Math.round(oy + y * z) + 0.5)
      }
      g.stroke()
    }
    if (vs.showGrid) gridLines(doc.grid.w, doc.grid.h, doc.grid.x, doc.grid.y, 'rgba(90,160,255,0.45)')
    const curL = doc.layers.find((l) => l.id === layer)
    const curTs = curL?.kind === 'tilemap' ? tilesetOf(doc, curL) : undefined
    if (curTs) gridLines(curTs.tw, curTs.th, 0, 0, tileMode ? 'rgba(255,200,80,0.7)' : 'rgba(255,200,80,0.3)')
    // guias
    if (vs.guides.x.length || vs.guides.y.length) {
      const rr = c.getBoundingClientRect()
      g.strokeStyle = 'rgba(77,208,255,0.85)'
      g.beginPath()
      for (const x of vs.guides.x) {
        g.moveTo(Math.round(ox + x * z) + 0.5, 0)
        g.lineTo(Math.round(ox + x * z) + 0.5, rr.height)
      }
      for (const y of vs.guides.y) {
        g.moveTo(0, Math.round(oy + y * z) + 0.5)
        g.lineTo(rr.width, Math.round(oy + y * z) + 0.5)
      }
      g.stroke()
    }
    // fatias
    const sd2 = sliceDrag.current
    const slicesShown = sd2?.draft ? (sd2.index >= 0 ? doc.slices.map((x, i) => (i === sd2.index ? sd2.draft! : x)) : [...doc.slices, sd2.draft]) : doc.slices
    slicesShown.forEach((sl, i) => {
      const on = i === selSlice || (sd2?.index === -1 && i === slicesShown.length - 1 && !!sd2.draft)
      if (!on && tool !== 'slice' && selSlice === null) return
      g.strokeStyle = sl.color
      g.lineWidth = on ? 2 : 1
      g.strokeRect(ox + sl.x * z + 0.5, oy + sl.y * z + 0.5, sl.w * z - 1, sl.h * z - 1)
      g.lineWidth = 1
      if (sl.center) {
        g.setLineDash([3, 3])
        g.beginPath()
        for (const x of [sl.center.x, sl.center.x + sl.center.w]) {
          g.moveTo(ox + (sl.x + x) * z + 0.5, oy + sl.y * z)
          g.lineTo(ox + (sl.x + x) * z + 0.5, oy + (sl.y + sl.h) * z)
        }
        for (const y of [sl.center.y, sl.center.y + sl.center.h]) {
          g.moveTo(ox + sl.x * z, oy + (sl.y + y) * z + 0.5)
          g.lineTo(ox + (sl.x + sl.w) * z, oy + (sl.y + y) * z + 0.5)
        }
        g.stroke()
        g.setLineDash([])
      }
      if (sl.pivot) {
        const px = ox + (sl.x + sl.pivot.x + 0.5) * z
        const py = oy + (sl.y + sl.pivot.y + 0.5) * z
        g.beginPath()
        g.arc(px, py, 4, 0, Math.PI * 2)
        g.moveTo(px - 6, py)
        g.lineTo(px + 6, py)
        g.moveTo(px, py - 6)
        g.lineTo(px, py + 6)
        g.stroke()
      }
      g.fillStyle = sl.color
      g.font = '11px ui-sans-serif, system-ui'
      g.fillText(sl.name, ox + sl.x * z + 2, oy + sl.y * z - 3)
    })
    // eixos de simetria
    const sym = opts.symmetry
    if (sym.mode !== 'none') {
      g.save()
      g.setLineDash([6, 4])
      g.strokeStyle = 'rgba(80,200,255,0.9)'
      g.beginPath()
      if (sym.mode === 'h' || sym.mode === 'both') {
        g.moveTo(ox + sym.x * z, oy - 8)
        g.lineTo(ox + sym.x * z, oy + H + 8)
      }
      if (sym.mode === 'v' || sym.mode === 'both') {
        g.moveTo(ox - 8, oy + sym.y * z)
        g.lineTo(ox + W + 8, oy + sym.y * z)
      }
      g.stroke()
      g.restore()
    }
    const center = (p: Pt): [number, number] => [ox + (p[0] + 0.5) * z, oy + (p[1] + 0.5) * z]
    // controles da curva
    if (curve.current) {
      const [a, c1, c2, b] = curve.current.pts.map(center)
      g.strokeStyle = 'rgba(255,225,77,0.8)'
      g.setLineDash([3, 3])
      g.beginPath()
      g.moveTo(...a)
      g.lineTo(...c1)
      g.moveTo(...b)
      g.lineTo(...c2)
      g.stroke()
      g.setLineDash([])
      for (const p of [c1, c2]) {
        g.fillStyle = '#ffe14d'
        g.fillRect(p[0] - 3, p[1] - 3, 6, 6)
      }
    }
    // vértices do polígono
    if (poly.current) {
      g.fillStyle = '#ffe14d'
      for (const v of poly.current.verts) {
        const [x, y] = center(v)
        g.fillRect(x - 2.5, y - 2.5, 5, 5)
      }
    }
    // seleção (formigas): a definitiva ou o rascunho do arraste
    const antMask = selDraft.current?.mask ?? (floating.current ? null : sel)
    if (antMask) drawAnts(g, outline(antMask, doc.w, doc.h), ox, oy, z)
    if (polyLasso.current) {
      g.strokeStyle = '#ffe14d'
      g.beginPath()
      polyLasso.current.verts.forEach((v, i) => (i ? g.lineTo(...center(v)) : g.moveTo(...center(v))))
      if (hover) g.lineTo(...center(hover))
      g.stroke()
    }
    // caixa da transformação
    const fl = floating.current
    if (fl) {
      const corners = floatCorners(fl).map(([x, y]) => [ox + x * z, oy + y * z] as [number, number])
      g.strokeStyle = '#4dd0ff'
      g.setLineDash([4, 3])
      g.beginPath()
      corners.forEach((c, i) => (i ? g.lineTo(...c) : g.moveTo(...c)))
      g.closePath()
      g.stroke()
      g.setLineDash([])
      for (const [hx, hy] of HANDLES) {
        const [x, y] = handlePos(fl, hx, hy)
        g.fillStyle = '#fff'
        g.strokeStyle = '#0a84ff'
        g.fillRect(ox + x * z - 4, oy + y * z - 4, 8, 8)
        g.strokeRect(ox + x * z - 4 + 0.5, oy + y * z - 4 + 0.5, 7, 7)
      }
    }
    // cursor do pincel
    if (hover && ['pencil', 'eraser', 'line', 'curve', 'rect', 'ellipse'].includes(tool)) {
      g.fillStyle = 'rgba(255,255,255,0.25)'
      g.strokeStyle = 'rgba(255,255,255,0.8)'
      for (const [dx, dy] of brushOffsets(opts.brush, opts.size)) g.fillRect(ox + (hover[0] + dx) * z, oy + (hover[1] + dy) * z, z, z)
      g.strokeRect(ox + hover[0] * z + 0.5, oy + hover[1] * z + 0.5, z - 1, z - 1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, frame, layer, view, showGrid, hover, tool, opts, onionLayers, tick, sel, previewDoc, vs, refEl, selSlice, tileMode])

  useEffect(() => {
    const box = wrap.current
    if (!box) return
    const ro = new ResizeObserver(() => redraw())
    ro.observe(box)
    return () => ro.disconnect()
  }, [])

  // ---- ponteiro ----------------------------------------------------------------
  const toPxF = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = canvas.current!.getBoundingClientRect()
    return [(e.clientX - r.left - view.ox) / view.z, (e.clientY - r.top - view.oy) / view.z]
  }
  const toPx = (e: { clientX: number; clientY: number }): Pt => {
    const [x, y] = toPxF(e)
    return [Math.floor(x), Math.floor(y)]
  }

  const curLayer = doc?.layers.find((l) => l.id === layer)
  const canPaint = () => {
    if (!doc || !curLayer) return false
    if (curLayer.kind === 'group') return toast.error('Selecione uma camada de imagem ou de tilemap (não um grupo)'), false
    if (effectiveLocked(doc, curLayer)) return toast.error(`A camada “${curLayer.name}” está bloqueada`), false
    if (!effectiveVisible(doc, curLayer)) return toast.error(`A camada “${curLayer.name}” está oculta`), false
    return true
  }
  const baseOf = (d: SpriteDoc) => getCel(d, layer, frame)?.data.slice() ?? new Uint8ClampedArray(d.w * d.h * 4)
  const ctxOf = (d: SpriteDoc) => ({ w: d.w, h: d.h, opts, ramp, wrap: vs.tiled })
  /** Grava pixels num cel; em camadas de tilemap, vira edição de tiles (modo auto/manual). */
  const writeData = (d: SpriteDoc, l: number, f: number, data: Uint8ClampedArray) =>
    d.layers.find((x) => x.id === l)?.kind === 'tilemap' ? applyPixels(d, l, f, enforceData(d, data), pixelMode) : writeCel(d, l, f, enforceData(d, data))

  const commitWork = (work: Uint8ClampedArray, l = layer, f = frame) => {
    const d = hist.current()
    if (!d) return
    hist.commit(writeData(d, l, f, work))
  }

  // tocando com "em cada quadro": quando o quadro muda no meio do traço, grava o que foi feito e continua no novo
  useEffect(() => {
    const st = stroke.current
    const d = hist.current()
    if (!st || !d || !playing || !paintFollow || st.frame === frame || st.s.tool === 'move') return
    hist.commit(writeData(d, st.layer, st.frame, st.work))
    const d2 = hist.current()!
    const last = st.s.points[st.s.points.length - 1]
    const b = getCel(d2, st.layer, frame)?.data.slice() ?? new Uint8ClampedArray(d2.w * d2.h * 4)
    st.s = { ...st.s, base: b, points: [last] }
    st.frame = frame
    st.work = clipSel(strokeResult(st.s, ctxOf(d2), { gradTo: bg }), b)
    redraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame])
  /** Pinturas ficam restritas à seleção, como no Aseprite. */
  const clipSel = (work: Uint8ClampedArray, base: Uint8ClampedArray) => (sel ? clipToMask(work, base, sel) : work)

  const celData = (d: SpriteDoc, l = layer, f = frame) => getCel(d, l, f)?.data ?? new Uint8ClampedArray(d.w * d.h * 4)

  const applySel = (m: Mask | null, mode: SelMode) => {
    if (!m) return
    const next = combine(sel, m, mode)
    setSel(isEmptyMask(next) ? null : next)
  }

  /** Levanta a seleção (ou o cel inteiro) para uma seleção flutuante. */
  const liftSelection = (): boolean => {
    const d = hist.current()
    if (!d || !canPaint()) return false
    const m = sel ?? (() => { const all = new Uint8Array(d.w * d.h); all.fill(1); return all })()
    const data = celData(d)
    const ex = extract(data, d.w, m)
    if (!ex) return false
    floating.current = { src: ex, cx: ex.x + ex.w / 2, cy: ex.y + ex.h / 2, sx: 1, sy: 1, angle: 0, base: cleared(data, m), layer, frame, lifted: true }
    redraw()
    return true
  }

  function commitFloating() {
    const d = hist.current()
    const f = floating.current
    if (!d || !f) return
    floating.current = null
    const r = transformImage(f.src, { sx: f.sx, sy: f.sy, angle: f.angle, algo: opts.algo })
    const left = Math.round(f.cx + r.dx)
    const top = Math.round(f.cy + r.dy)
    const out = f.base.slice()
    stampOver(out, d.w, d.h, r, left, top)
    hist.commit(writeData(d, f.layer, f.frame, out))
    const m = maskFromImage(d.w, d.h, r, left, top)
    setSel(isEmptyMask(m) ? null : m)
    redraw()
  }
  const cancelFloating = () => {
    floating.current = null
    floatDrag.current = null
    redraw()
  }
  const floatState = (): FloatState | null => {
    const f = floating.current
    if (!f) return null
    const w = f.src.w * Math.abs(f.sx)
    const h = f.src.h * Math.abs(f.sy)
    return { x: f.cx - w / 2, y: f.cy - h / 2, w, h, angle: f.angle }
  }
  const patchFloat = (p: Partial<FloatState>) => {
    const f = floating.current
    const cur = floatState()
    if (!f || !cur) return
    const w = Math.max(1, p.w ?? cur.w)
    const h = Math.max(1, p.h ?? cur.h)
    const x = p.x ?? cur.x
    const y = p.y ?? cur.y
    f.sx = Math.sign(f.sx || 1) * (w / f.src.w)
    f.sy = Math.sign(f.sy || 1) * (h / f.src.h)
    f.cx = x + w / 2
    f.cy = y + h / 2
    if (p.angle !== undefined) f.angle = p.angle
    redraw()
  }

  // ---- copiar / colar --------------------------------------------------------
  const writeSystemClipboard = async (img: Img) => {
    try {
      const c = document.createElement('canvas')
      c.width = img.w
      c.height = img.h
      c.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(img.data), img.w, img.h), 0, 0)
      const blob = await new Promise<Blob | null>((res) => c.toBlob(res, 'image/png'))
      if (!blob) throw new Error('sem PNG')
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      sysClipOk.current = true
    } catch {
      sysClipOk.current = false
    }
  }
  const copy = (merged: boolean, cut: boolean) => {
    const d = hist.current()
    if (!d) return
    const f = floating.current
    let item: Clip | null = null
    if (f) {
      const r = floatRender()!
      item = { img: r.img, x: r.left, y: r.top }
    } else {
      const m = sel ?? (() => { const all = new Uint8Array(d.w * d.h); all.fill(1); return all })()
      const ex = extract(merged ? renderFrame(d, frame) : celData(d), d.w, m)
      if (ex) item = { img: ex, x: ex.x, y: ex.y }
    }
    if (!item) return toast.info('Nada para copiar')
    clip.current = item
    void writeSystemClipboard(item.img)
    if (cut) {
      if (f) {
        floating.current = null
        if (f.lifted) hist.commit(writeData(d, f.layer, f.frame, f.base))
        setSel(null)
        redraw()
      } else if (canPaint()) {
        commitWork(sel ? cleared(celData(d), sel) : new Uint8ClampedArray(d.w * d.h * 4))
      }
    }
    toast.success(cut ? 'Recortado' : 'Copiado', { description: `${item.img.w}×${item.img.h} px` })
  }
  const pasteImg = (img: Img, x?: number, y?: number) => {
    const d = hist.current()
    if (!d || !canPaint()) return
    if (floating.current) commitFloating()
    const d2 = hist.current()!
    const fits = x !== undefined && y !== undefined && x > -img.w && y > -img.h && x < d2.w && y < d2.h
    const left = fits ? x! : Math.round((d2.w - img.w) / 2)
    const top = fits ? y! : Math.round((d2.h - img.h) / 2)
    floating.current = { src: img, cx: left + img.w / 2, cy: top + img.h / 2, sx: 1, sy: 1, angle: 0, base: celData(d2).slice(), layer, frame, lifted: false }
    setSel(null)
    setTool('move')
    redraw()
  }
  const pasteInternal = () => {
    const c = clip.current
    if (c) pasteImg(c.img, c.x, c.y)
    else toast.info('A área de transferência está vazia')
  }

  const finishPolygon = () => {
    const p = poly.current
    if (!p || !doc) return
    poly.current = null
    if (p.verts.length >= 2) commitWork(strokeResult(p.s, ctxOf(doc), { polygon: p.verts }))
    redraw()
  }
  const finishCurve = () => {
    const c = curve.current
    if (!c || !doc) return
    curve.current = null
    commitWork(strokeResult(c.s, ctxOf(doc), { curve: c.pts }))
    redraw()
  }

  const onDown = (e: React.PointerEvent) => {
    if (!doc) return
    canvas.current!.setPointerCapture(e.pointerId)
    if (e.button === 1 || (e.button === 0 && space.current)) {
      pan.current = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy }
      return
    }
    if (e.button !== 0 && e.button !== 2) return
    const p = toPx(e)
    const right = e.button === 2
    const color = right ? bg : fg

    // transformação em andamento: alças, mover, girar; clicar fora aplica
    if (floating.current && tool === 'move') {
      const pf = toPxF(e)
      const hit = hitFloat(floating.current, pf, view.z)
      if (hit) {
        floatDrag.current = { ...hit, start: pf, f0: { ...floating.current } }
        return
      }
      commitFloating()
      return
    }

    // Alt+arrastar move a imagem de referência
    if (e.altKey && vs.reference?.visible && !SELECT_TOOLS.includes(tool)) {
      refDrag.current = { x: e.clientX, y: e.clientY, rx: vs.reference.x, ry: vs.reference.y }
      return
    }

    if (tool === 'slice') {
      const hitIdx = [...doc.slices].reverse().findIndex((sl) => p[0] >= sl.x && p[1] >= sl.y && p[0] < sl.x + sl.w && p[1] < sl.y + sl.h)
      const idx = hitIdx < 0 ? -1 : doc.slices.length - 1 - hitIdx
      if (idx >= 0) {
        setSelSlice(idx)
        sliceDrag.current = { mode: 'move', start: p, index: idx, draft: null }
      } else sliceDrag.current = { mode: 'new', start: p, index: -1, draft: null }
      return
    }

    if (tool === 'text') {
      if (!canPaint()) return
      setTextAt(p)
      return
    }

    // camada de tilemap no modo Tiles: lápis/borracha/balde/conta-gotas trabalham com tiles
    if (curLayer?.kind === 'tilemap' && tileMode && ['pencil', 'eraser', 'fill', 'picker'].includes(tool)) {
      const ts = tilesetOf(doc, curLayer)
      const tm = getTilemap(doc, curLayer, frame)
      if (!ts || !tm) return
      const c = Math.floor(p[0] / ts.tw)
      const r = Math.floor(p[1] / ts.th)
      if (c < 0 || r < 0 || c >= tm.cols || r >= tm.rows) return
      if (tool === 'picker') return setTile(tm.tiles[r * tm.cols + c])
      if (!canPaint()) return
      const erase = tool === 'eraser' || right
      if (tool === 'fill') return hist.commit(setTilemap(doc, layer, frame, fillTiles(tm, c, r, erase ? 0 : tile)))
      const copy: Tilemap = { ...tm, tiles: tm.tiles.slice() }
      copy.tiles[r * tm.cols + c] = erase ? 0 : tile
      tileStroke.current = { tm: copy, erase }
      redraw()
      return
    }

    if (SELECT_TOOLS.includes(tool)) {
      const mode = selModeOf(e, opts.selMode)
      if (tool === 'wand' || tool === 'sel_color') {
        if (p[0] < 0 || p[1] < 0 || p[0] >= doc.w || p[1] >= doc.h) return
        const sample = opts.sampleAll ? renderFrame(doc, frame) : celData(doc)
        applySel(colorMask(sample, doc.w, doc.h, p, tool === 'wand', opts.tolerance), mode)
        return
      }
      if (tool === 'poly_lasso') {
        const pl = polyLasso.current
        if (!pl) polyLasso.current = { mode, verts: [p] }
        else {
          const first = pl.verts[0]
          if (pl.verts.length >= 3 && Math.abs(first[0] - p[0]) <= 1 && Math.abs(first[1] - p[1]) <= 1) finishPolyLasso()
          else pl.verts.push(p)
        }
        redraw()
        return
      }
      selDraft.current = { mode, start: p, pts: [p], mask: null }
      return
    }

    if (tool === 'move' && sel && p[0] >= 0 && p[1] >= 0 && p[0] < doc.w && p[1] < doc.h && sel[p[1] * doc.w + p[0]]) {
      if (liftSelection()) floatDrag.current = { mode: 'move', hx: 0, hy: 0, start: toPxF(e), f0: { ...floating.current! } }
      return
    }

    if (tool === 'picker') {
      if (p[0] < 0 || p[1] < 0 || p[0] >= doc.w || p[1] >= doc.h) return
      const src = opts.sampleAll ? renderFrame(doc, frame) : (getCel(doc, layer, frame)?.data ?? new Uint8ClampedArray(doc.w * doc.h * 4))
      const i = (p[1] * doc.w + p[0]) * 4
      const c: RGBA = [src[i], src[i + 1], src[i + 2], src[i + 3]]
      if (c[3] === 0) return
      ;(right ? setBg : setFg)(c)
      return
    }

    // curva: os cliques seguintes ajustam os controles
    if (tool === 'curve' && curve.current) {
      const c = curve.current
      c.pts[c.stage] = p
      redraw()
      return
    }
    if (!canPaint()) return

    const s: StrokeState = { tool, base: baseOf(doc), points: [p], color: color[3] === 0 && opts.ink !== 'copy' ? null : color, step: right ? -1 : 1, shift: e.shiftKey }

    if (tool === 'polygon') {
      const pc = poly.current
      if (!pc) poly.current = { s, verts: [p] }
      else {
        const first = pc.verts[0]
        if (pc.verts.length >= 3 && Math.abs(first[0] - p[0]) <= 1 && Math.abs(first[1] - p[1]) <= 1) finishPolygon()
        else pc.verts.push(p)
      }
      redraw()
      return
    }

    if (tool === 'fill') {
      const sample = opts.sampleAll ? renderFrame(doc, frame) : s.base
      const region = fillRegion(sample, doc.w, doc.h, p[0], p[1], { contiguous: opts.contiguous, tolerance: opts.tolerance })
      if (sel) for (const q of [...region]) if (!sel[q]) region.delete(q)
      const out = s.base.slice()
      applyInk(s.base, out, region, { ink: opts.ink, color: s.color, ramp, rampStep: s.step })
      commitWork(out)
      return
    }

    if (tool === 'move') {
      stroke.current = { s, work: s.base.slice(), stab: [p[0], p[1]], layer, frame }
      return
    }

    // Shift no lápis: linha desde o último ponto (como no Aseprite)
    if ((tool === 'pencil' || tool === 'eraser') && e.shiftKey && lastPoint.current) s.points = [lastPoint.current, p]
    if (tool === 'gradient') s.color = fg
    const start = toPxF(e)
    stroke.current = { s, work: clipSel(strokeResult(s, ctxOf(doc), { gradTo: bg }), s.base), stab: start, layer, frame }
    redraw()
  }

  const finishPolyLasso = () => {
    const pl = polyLasso.current
    const d = hist.current()
    polyLasso.current = null
    if (pl && d && pl.verts.length >= 3) applySel(polygonMask(d.w, d.h, pl.verts), pl.mode)
    redraw()
  }

  const onMove = (e: React.PointerEvent) => {
    if (pan.current) {
      const pn = pan.current
      setView((v) => ({ ...v, ox: pn.ox + e.clientX - pn.x, oy: pn.oy + e.clientY - pn.y }))
      return
    }
    const p = toPx(e)
    setHover((h) => (h && h[0] === p[0] && h[1] === p[1] ? h : p))
    if (!doc) return
    // referência
    const rd = refDrag.current
    if (rd && vs.reference) {
      setVs('reference', { ...vs.reference, x: Math.round(rd.rx + (e.clientX - rd.x) / view.z), y: Math.round(rd.ry + (e.clientY - rd.y) / view.z) })
      return
    }
    // tiles
    const ts2 = tileStroke.current
    if (ts2 && curLayer?.kind === 'tilemap') {
      const tset = tilesetOf(doc, curLayer)
      if (tset) {
        const c = Math.floor(p[0] / tset.tw)
        const r = Math.floor(p[1] / tset.th)
        if (c >= 0 && r >= 0 && c < ts2.tm.cols && r < ts2.tm.rows) {
          ts2.tm.tiles[r * ts2.tm.cols + c] = ts2.erase ? 0 : tile
          redraw()
        }
      }
      return
    }
    // fatia
    const sld = sliceDrag.current
    if (sld) {
      const snapP = (q: Pt, up: boolean): Pt => (vs.snap ? snapToGrid(q, doc.grid, up) : q)
      if (sld.mode === 'new') {
        const a = snapP([Math.min(sld.start[0], p[0]), Math.min(sld.start[1], p[1])], false)
        const b = snapP([Math.max(sld.start[0], p[0]), Math.max(sld.start[1], p[1])], true)
        sld.draft = { name: `fatia${doc.slices.length + 1}`, color: '#3b82f6', x: a[0], y: a[1], w: b[0] - a[0] + 1, h: b[1] - a[1] + 1 }
      } else {
        const o = doc.slices[sld.index]
        const q = snapP([o.x + p[0] - sld.start[0], o.y + p[1] - sld.start[1]], false)
        sld.draft = { ...o, x: q[0], y: q[1] }
      }
      redraw()
      return
    }
    // transformação
    const fd = floatDrag.current
    const fl = floating.current
    if (fd && fl) {
      const pf = toPxF(e)
      dragFloat(fl, fd, pf, e.shiftKey)
      if (vs.snap && fd.mode === 'move') {
        const hw = (fl.src.w * Math.abs(fl.sx)) / 2
        const hh = (fl.src.h * Math.abs(fl.sy)) / 2
        const [lx, ly] = snapToGrid([Math.round(fl.cx - hw), Math.round(fl.cy - hh)], doc.grid, false, true)
        fl.cx = lx + hw
        fl.cy = ly + hh
      }
      redraw()
      return
    }
    // rascunho da seleção
    const sd = selDraft.current
    if (sd) {
      if (tool === 'lasso') sd.pts.push(p)
      else sd.pts = [sd.start, p]
      const b = sd.pts[sd.pts.length - 1]
      const [sa, sb] = vs.snap && tool === 'sel_rect'
        ? [snapToGrid([Math.min(sd.start[0], b[0]), Math.min(sd.start[1], b[1])], doc.grid, false), snapToGrid([Math.max(sd.start[0], b[0]), Math.max(sd.start[1], b[1])], doc.grid, true)]
        : [sd.start, b]
      sd.mask = tool === 'sel_rect' ? rectMask(doc.w, doc.h, sa, sb) : tool === 'sel_ellipse' ? ellipseMask(doc.w, doc.h, sd.start, b) : sd.pts.length > 2 ? polygonMask(doc.w, doc.h, sd.pts) : null
      redraw()
      return
    }
    // curva: arrastar ajusta o controle da vez
    if (curve.current && e.buttons & 1) {
      curve.current.pts[curve.current.stage] = p
      redraw()
      return
    }
    const st = stroke.current
    if (!st) return
    const s = st.s
    if (tool === 'move') {
      st.work = shifted(s.base, doc.w, doc.h, p[0] - s.points[0][0], p[1] - s.points[0][1])
      redraw()
      return
    }
    let q = p
    if (FREEHAND.includes(tool) && opts.stabilizer > 0) {
      st.stab = stabilize(st.stab, toPxF(e), opts.stabilizer / Math.max(1, view.z) * 4)
      q = [Math.floor(st.stab[0]), Math.floor(st.stab[1])]
    }
    const last = s.points[s.points.length - 1]
    if (last[0] === q[0] && last[1] === q[1]) return
    if (FREEHAND.includes(tool)) s.points.push(q)
    else if (SHAPES.includes(tool) || tool === 'curve') s.points = [s.points[0], q]
    s.shift = e.shiftKey
    st.work = clipSel(strokeResult(s, ctxOf(doc), { gradTo: bg }), s.base)
    redraw()
  }

  const onUp = () => {
    pan.current = null
    refDrag.current = null
    if (tileStroke.current && doc) {
      const tm = tileStroke.current.tm
      tileStroke.current = null
      hist.commit(setTilemap(doc, layer, frame, tm))
      return
    }
    const sld = sliceDrag.current
    if (sld && doc) {
      sliceDrag.current = null
      if (sld.draft) {
        if (sld.index >= 0) hist.commit({ ...doc, slices: doc.slices.map((x, i) => (i === sld.index ? sld.draft! : x)) })
        else {
          hist.commit({ ...doc, slices: [...doc.slices, sld.draft] })
          setSelSlice(doc.slices.length)
        }
      } else if (sld.mode === 'new') setSelSlice(null)
      redraw()
      return
    }
    if (floatDrag.current) {
      floatDrag.current = null
      redraw()
      return
    }
    const sd = selDraft.current
    if (sd) {
      selDraft.current = null
      const moved = sd.pts.length > 1 && (sd.pts[sd.pts.length - 1][0] !== sd.start[0] || sd.pts[sd.pts.length - 1][1] !== sd.start[1])
      if (!moved && sd.mode === 'replace') setSel(null) // clique sem arrastar: tira a seleção
      else if (sd.mask) applySel(sd.mask, sd.mode)
      redraw()
      return
    }
    if (curve.current) {
      // soltou o controle 1 → passa ao 2; soltou o 2 → aplica
      if (curve.current.stage === 1) curve.current.stage = 2
      else finishCurve()
      redraw()
      return
    }
    const st = stroke.current
    if (!st || !doc) return
    stroke.current = null
    const s = st.s
    if (tool === 'curve') {
      const a = s.points[0]
      const b = s.points[s.points.length - 1]
      const third = (k: number): Pt => [Math.round(a[0] + ((b[0] - a[0]) * k) / 3), Math.round(a[1] + ((b[1] - a[1]) * k) / 3)]
      curve.current = { s: { ...s, points: [a, b] }, pts: [a, third(1), third(2), b], stage: 1 }
      redraw()
      return
    }
    lastPoint.current = s.points[s.points.length - 1]
    commitWork(st.work, st.layer, st.frame)
  }

  const onWheel = (e: { clientX: number; clientY: number; deltaY: number }) => {
    const r = canvas.current!.getBoundingClientRect()
    const mx = e.clientX - r.left
    const my = e.clientY - r.top
    setView((v) => {
      const steps = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64]
      let i = steps.findIndex((s) => s >= v.z)
      if (i < 0) i = steps.length - 1
      i = Math.max(0, Math.min(steps.length - 1, i + (e.deltaY < 0 ? 1 : -1)))
      const z = steps[i]
      return { z, ox: mx - ((mx - v.ox) * z) / v.z, oy: my - ((my - v.oy) * z) / v.z }
    })
  }
  const zoomBy = (dir: 1 | -1) => {
    const r = wrap.current!.getBoundingClientRect()
    onWheel({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, deltaY: -dir })
  }

  // ---- salvar ------------------------------------------------------------------
  const save = useCallback(async () => {
    if (!doc) return
    setBusy('Salvando…')
    try {
      const v = await saveAsVersion(project, asset, doc)
      hist.markSaved()
      qc.invalidateQueries({ queryKey: ['versions', project.slug, asset.slug] })
      qc.invalidateQueries({ queryKey: ['assets', project.slug] })
      toast.success(`Salvo: ${v.ref}`, { description: `${doc.layers.length} camada(s), ${doc.frames.length} quadro(s)` })
      nav(`/p/${project.slug}/a/${v.slug}/${v.version}?tab=sprite`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }, [doc, project, asset, hist, qc, nav])

  const download = async () => {
    if (!doc) return
    downloadBlob(await docBlob(doc), `${asset.slug}.aseprite`)
  }

  // ---- atalhos -----------------------------------------------------------------
  const selectCel = (l: number, f: number, extend = false) => {
    setLayer(l)
    setFrame(f)
    setRange((r) => (extend ? [r[0], f] : [f, f]))
  }
  const [bindings, setBindings] = useBindings()
  const keys = useMemo(() => keymap(bindings), [bindings])
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const hint = (id: string) => keyHint(bindings, id)
  const runAction = (id: string, combo: string) => {
    if (!doc) return
    if (id.startsWith('tool:')) return pickTool(id.slice(5) as Tool)
    switch (id) {
      case 'save': return void save()
      case 'undo': return cancelFloating(), hist.undo()
      case 'redo': return cancelFloating(), hist.redo()
      case 'copy': return copy(false, false)
      case 'copy_merged': return copy(true, false)
      case 'cut': return copy(false, true)
      case 'paste':
        if (combo !== 'Ctrl+V') return pasteInternal()
        // o evento "paste" traz a imagem do sistema; sem ele (ou sem imagem), cola a cópia interna
        if (pasteWanted.current) window.clearTimeout(pasteWanted.current)
        pasteWanted.current = window.setTimeout(() => {
          pasteWanted.current = null
          pasteInternal()
        }, 250)
        return
      case 'transform':
        if (!floating.current && liftSelection()) setTool('move')
        return
      case 'select_all': {
        const all = new Uint8Array(doc.w * doc.h)
        all.fill(1)
        return setSel(all)
      }
      case 'deselect': return setSel(null)
      case 'invert_sel': return setSel((m) => (m ? invert(m) : null))
      case 'new_frame': return hist.commit(insertFrame(doc, frame, 'empty')), selectCel(layer, frame + 1)
      case 'dup_frame': return hist.commit(insertFrame(doc, frame, 'duplicate')), selectCel(layer, frame + 1)
      case 'new_layer': {
        const r = addLayer(doc, layer)
        hist.commit(r.doc)
        return setLayer(r.id)
      }
      case 'prev_frame': return selectCel(layer, Math.max(0, frame - 1))
      case 'next_frame': return selectCel(layer, Math.min(doc.frames.length - 1, frame + 1))
      case 'first_frame': return selectCel(layer, 0)
      case 'last_frame': return selectCel(layer, doc.frames.length - 1)
      case 'play': return setPlaying((p) => !p)
      case 'onion': return setOnion((o) => ({ ...o, on: !o.on }))
      case 'swap_colors': return setFg(bg), setBg(fg)
      case 'zoom_in': return zoomBy(1)
      case 'zoom_out': return zoomBy(-1)
      case 'brush_smaller': return setOpt('size', Math.max(1, opts.size - 1))
      case 'brush_bigger': return setOpt('size', Math.min(64, opts.size + 1))
    }
  }
  const pickTool = (id: Tool) => {
    if (id === tool && (id === 'rect' || id === 'ellipse')) setOpt('filled', !opts.filled)
    setTool(id)
  }
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable) return
      if (!doc) return
      const k = e.key.toLowerCase()
      if (e.key === ' ') {
        space.current = true
        e.preventDefault()
        return
      }
      if (e.key === 'Tab' && curLayer?.kind === 'tilemap') {
        e.preventDefault()
        setTileMode((m) => !m)
        return
      }
      if (k === 'escape') {
        if (floating.current) return cancelFloating()
        if (!curve.current && !poly.current && !polyLasso.current) setSel(null)
        curve.current = null
        poly.current = null
        polyLasso.current = null
        redraw()
        return
      }
      if (k === 'enter' && (poly.current || curve.current || polyLasso.current || floating.current)) {
        e.preventDefault()
        if (floating.current) commitFloating()
        else if (polyLasso.current) finishPolyLasso()
        else if (poly.current) finishPolygon()
        else finishCurve()
        return
      }
      if ((k === 'delete' || k === 'backspace') && (sel || floating.current)) {
        e.preventDefault()
        const f = floating.current
        if (f) {
          floating.current = null
          if (f.lifted) hist.commit(writeData(doc, f.layer, f.frame, f.base))
          setSel(null)
          redraw()
        } else if (sel && canPaint()) commitWork(cleared(celData(doc), sel))
        return
      }
      const combo = comboOf(e)
      const action = combo ? keys.get(combo) : undefined
      if (!action) return
      // Ctrl+V deixa o evento "paste" do sistema acontecer (traz a imagem copiada de fora); os demais cancelam o padrão
      if (!(action === 'paste' && combo === 'Ctrl+V')) e.preventDefault()
      runAction(action, combo!)
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === ' ') space.current = false
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  })

  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
      const file = [...(e.clipboardData?.files ?? [])].find((f) => f.type.startsWith('image/'))
      // a cópia interna é a mesma imagem que mandamos ao sistema; se o sistema falhou, ela vale mais
      const useInternal = clip.current && !sysClipOk.current
      if (!file || useInternal) return
      e.preventDefault()
      if (pasteWanted.current) {
        window.clearTimeout(pasteWanted.current)
        pasteWanted.current = null
      }
      const url = URL.createObjectURL(file)
      const im = new Image()
      im.onload = () => {
        const c = document.createElement('canvas')
        c.width = im.naturalWidth
        c.height = im.naturalHeight
        const g = c.getContext('2d', { willReadFrequently: true })!
        g.drawImage(im, 0, 0)
        URL.revokeObjectURL(url)
        const same = clip.current && clip.current.img.w === c.width && clip.current.img.h === c.height
        pasteImg({ w: c.width, h: c.height, data: g.getImageData(0, 0, c.width, c.height).data }, same ? clip.current!.x : undefined, same ? clip.current!.y : undefined)
      }
      im.src = url
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  })

  if (loadErr) return <div className="p-6 text-sm text-destructive">Não consegui abrir no editor: {loadErr}</div>
  if (!doc) return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Abrindo…</div>

  const swatch = (c: RGBA) => `rgba(${c[0]},${c[1]},${c[2]},${c[3] / 255})`

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* barra principal */}
      <div className="flex flex-wrap items-center gap-0.5 border-b px-2 py-1">
        {TOOL_DEFS.map((t) => (
          <Button key={t.id} size="icon-sm" variant={tool === t.id ? 'secondary' : 'ghost'} title={`${t.label} (${hint(`tool:${t.id}`)})`} onClick={() => pickTool(t.id)}>
            {ICONS[t.id]}
          </Button>
        ))}
        <span className="mx-1 h-5 border-l" />
        <SpriteMenu doc={doc} commit={(d) => { cancelFloating(); setSel(null); hist.commit(d) }} selection={sel ? bounds(sel, doc.w) : null} />
        <ColorMenu
          doc={doc}
          commit={(d) => { cancelFloating(); hist.commit(d) }}
          layer={layer}
          frame={frame}
          mask={sel}
          fg={fg}
          bg={bg}
          onPreview={setPreviewDoc}
        />
        <ViewMenu view={vs} setView={setVs} doc={doc} setGrid={(gr) => hist.commit({ ...doc, grid: gr })} />
        <Button size="icon-sm" variant="ghost" title={`Desfazer (${hint('undo')})`} disabled={!hist.canUndo} onClick={() => { cancelFloating(); hist.undo() }}><Undo2 /></Button>
        <Button size="icon-sm" variant="ghost" title={`Refazer (${hint('redo')})`} disabled={!hist.canRedo} onClick={hist.redo}><Redo2 /></Button>
        <span className="mx-1 h-5 border-l" />
        <Button size="icon-sm" variant={showGrid ? 'secondary' : 'ghost'} title="Grade de pixels" onClick={() => setShowGrid(!showGrid)}><Grid3x3 /></Button>
        <Button size="icon-sm" variant="ghost" title={`Menos zoom (${hint('zoom_out')})`} onClick={() => zoomBy(-1)}><Minus /></Button>
        <span className="w-12 text-center text-xs tabular-nums">{view.z * 100}%</span>
        <Button size="icon-sm" variant="ghost" title={`Mais zoom (${hint('zoom_in')})`} onClick={() => zoomBy(1)}><Plus /></Button>
        <span className="ml-2 text-xs text-muted-foreground tabular-nums">
          {doc.w}×{doc.h}{hover && hover[0] >= 0 && hover[1] >= 0 && hover[0] < doc.w && hover[1] < doc.h ? ` · ${hover[0]}, ${hover[1]}` : ''}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {hist.dirty && <span className="text-xs text-amber-400">não salvo</span>}
          <Button size="icon-sm" variant="ghost" onClick={() => setShortcutsOpen(true)} title="Atalhos de teclado (configurar)"><Keyboard /></Button>
          {shortcutsOpen && <ShortcutsDialog bindings={bindings} onChange={setBindings} onClose={() => setShortcutsOpen(false)} />}
          <Button size="sm" variant="ghost" onClick={download} title="Baixar como .aseprite"><Download /> .aseprite</Button>
          <Button size="sm" onClick={save} disabled={!!busy}>{busy ? <Loader2 className="animate-spin" /> : <Save />} Salvar versão</Button>
        </div>
      </div>
      {floating.current ? (
        <TransformBar
          f={floatState()!}
          algo={opts.algo}
          setAlgo={(a) => { setOpt('algo', a); redraw() }}
          onChange={patchFloat}
          onFlip={(axis) => {
            const f = floating.current!
            f.src = flipImg(f.src, axis)
            redraw()
          }}
          onRotate={(deg) => patchFloat({ angle: ((floating.current!.angle + deg) % 360 + 360) % 360 })}
          onApply={commitFloating}
          onCancel={cancelFloating}
        />
      ) : (
        <ToolOptionsBar tool={tool} opts={opts} set={setOpt} docW={doc.w} docH={doc.h} />
      )}

      <div className="flex min-h-0 flex-1">
        <div ref={wrap} className="relative min-h-0 min-w-0 flex-1 overflow-hidden bg-[#1e1e1e]" onContextMenu={(e) => e.preventDefault()}>
          <canvas
            ref={canvas}
            className={cn('absolute inset-0 touch-none', tool === 'move' ? 'cursor-move' : 'cursor-crosshair')}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
            onPointerLeave={() => setHover(null)}
            onDoubleClick={() => (tool === 'polygon' ? finishPolygon() : tool === 'poly_lasso' ? finishPolyLasso() : undefined)}
            onWheel={onWheel}
          />
          {vs.rulers && <Rulers z={view.z} ox={view.ox} oy={view.oy} w={doc.w} h={doc.h} guides={vs.guides} setGuides={(gd) => setVs('guides', gd)} />}
          {vs.refPanel && (
            <ReferencePanel refImg={vs.reference} setRef={(r) => setVs('reference', r)} onClose={() => setVs('refPanel', false)} projectImages={projectImages} />
          )}
          {vs.preview.open && <PreviewWindow doc={previewDoc ?? doc} frame={frame} tiled={vs.tiled} state={vs.preview} setState={(pw) => setVs('preview', pw)} loopTag={loopTag} />}
        </div>

        <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-l p-2">
          <div className="shrink-0 space-y-2">
            <div className="flex items-center gap-2">
              <div className="relative h-10 w-12">
                <label className="absolute top-0 left-0 size-7 cursor-pointer rounded border-2 border-white/80 shadow" style={{ background: swatch(fg) }} title="Cor principal (clique esquerdo)">
                  <input type="color" className="invisible" value={rgbaToHex(fg)} onChange={(e) => setFg(hexToRgba(e.target.value, fg[3] || 255))} />
                </label>
                <label className="absolute right-0 bottom-0 size-6 cursor-pointer rounded border border-white/40" style={{ background: swatch(bg) }} title="Cor secundária (clique direito)">
                  <input type="color" className="invisible" value={rgbaToHex(bg)} onChange={(e) => setBg(hexToRgba(e.target.value, bg[3] || 255))} />
                </label>
              </div>
              <Button size="icon-xs" variant="ghost" title="Trocar cores (X)" onClick={() => { setFg(bg); setBg(fg) }}><ArrowLeftRight /></Button>
              <span className="font-mono text-[11px] text-muted-foreground">{rgbaToHex(fg)}</span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">Alfa</span>
              <Slider className="flex-1" min={0} max={255} value={[fg[3]]} onValueChange={(v) => setFg([fg[0], fg[1], fg[2], Array.isArray(v) ? v[0] : (v as number)])} />
              <span className="w-7 text-right tabular-nums">{fg[3]}</span>
            </div>
            <PalettePanel
              doc={doc}
              commit={(d) => { cancelFloating(); hist.commit(d) }}
              fg={fg}
              setFg={setFg}
              setBg={setBg}
              projectPalette={projectPalette}
              saveProjectPalette={saveProjectPalette}
            />
            {opts.ink === 'shading' && <p className="text-[11px] text-muted-foreground">Sombreamento usa a ordem da paleta como rampa.</p>}
          </div>
          <LayersPanel doc={doc} layer={layer} onLayer={setLayer} commit={hist.commit} />
          {curLayer && curLayer.kind !== 'group' && <EffectsPanel doc={doc} layer={curLayer} commit={hist.commit} fg={fg} bg={bg} />}
          {curLayer?.kind === 'tilemap' && (
            <TilesetPanel doc={doc} layer={curLayer} commit={hist.commit} tile={tile} setTile={setTile} tileMode={tileMode} setTileMode={setTileMode} pixelMode={pixelMode} setPixelMode={setPixelMode} />
          )}
          {(tool === 'slice' || doc.slices.length > 0) && <SlicePanel doc={doc} commit={hist.commit} selected={selSlice} setSelected={setSelSlice} />}
        </aside>
        {textAt && (
          <TextDialog
            color={fg}
            onClose={() => setTextAt(null)}
            onInsert={(img) => {
              const at = textAt
              setTextAt(null)
              pasteImg(img, at[0], at[1])
            }}
          />
        )}
      </div>

      <div className="h-56 shrink-0">
        <Timeline
          doc={doc}
          layer={layer}
          frame={frame}
          range={range}
          onSelect={selectCel}
          commit={(d, f) => {
            hist.commit(d)
            if (f !== undefined) selectCel(layer, Math.max(0, Math.min(d.frames.length - 1, f)))
            else if (frame >= d.frames.length) selectCel(layer, d.frames.length - 1)
          }}
          playing={playing}
          onPlay={() => setPlaying((p) => !p)}
          paintFollow={paintFollow}
          setPaintFollow={setPaintFollow}
          loopTag={loopTag}
          setLoopTag={setLoopTag}
          onion={onion}
          setOnion={setOnion}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- transformação: geometria

const HANDLES: [number, number][] = [[-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0]]

function rot(x: number, y: number, deg: number): [number, number] {
  const r = (deg * Math.PI) / 180
  return [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r)]
}

function halfSize(f: Floating): [number, number] {
  return [(f.src.w * Math.abs(f.sx)) / 2, (f.src.h * Math.abs(f.sy)) / 2]
}

function handlePos(f: Floating, hx: number, hy: number): [number, number] {
  const [hw, hh] = halfSize(f)
  const [x, y] = rot(hx * hw, hy * hh, f.angle)
  return [f.cx + x, f.cy + y]
}

function floatCorners(f: Floating): [number, number][] {
  return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([hx, hy]) => handlePos(f, hx, hy))
}

/** O que há sob o ponteiro: alça (escalar), dentro (mover), perto de um canto por fora (girar). */
function hitFloat(f: Floating, p: [number, number], z: number): Omit<FloatDrag, 'start' | 'f0'> | null {
  const tol = 7 / z
  for (const [hx, hy] of HANDLES) {
    const [x, y] = handlePos(f, hx, hy)
    if (Math.abs(p[0] - x) <= tol && Math.abs(p[1] - y) <= tol) return { mode: 'scale', hx, hy }
  }
  const [hw, hh] = halfSize(f)
  const [lx, ly] = rot(p[0] - f.cx, p[1] - f.cy, -f.angle)
  if (Math.abs(lx) <= hw && Math.abs(ly) <= hh) return { mode: 'move', hx: 0, hy: 0 }
  for (const [x, y] of floatCorners(f)) if (Math.hypot(p[0] - x, p[1] - y) <= 28 / z) return { mode: 'rotate', hx: 0, hy: 0 }
  return null
}

function dragFloat(f: Floating, d: FloatDrag, p: [number, number], shift: boolean) {
  const f0 = d.f0
  if (d.mode === 'move') {
    f.cx = f0.cx + Math.round(p[0] - d.start[0])
    f.cy = f0.cy + Math.round(p[1] - d.start[1])
    return
  }
  if (d.mode === 'rotate') {
    const a0 = Math.atan2(d.start[1] - f0.cy, d.start[0] - f0.cx)
    const a1 = Math.atan2(p[1] - f0.cy, p[0] - f0.cx)
    let ang = f0.angle + ((a1 - a0) * 180) / Math.PI
    if (shift) ang = Math.round(ang / 15) * 15
    f.angle = ((ang % 360) + 360) % 360
    return
  }
  // escala com o lado oposto parado (no referencial girado da caixa)
  const [hw0, hh0] = halfSize(f0)
  const [lx, ly] = rot(p[0] - f0.cx, p[1] - f0.cy, -f0.angle)
  let w = hw0 * 2
  let h = hh0 * 2
  let ncx = 0
  let ncy = 0
  if (d.hx) {
    const opp = -d.hx * hw0
    w = Math.max(1, Math.round(d.hx * (lx - opp)))
    ncx = opp + (d.hx * w) / 2
  }
  if (d.hy) {
    const opp = -d.hy * hh0
    h = Math.max(1, Math.round(d.hy * (ly - opp)))
    ncy = opp + (d.hy * h) / 2
  }
  if (shift && d.hx && d.hy) {
    const k = Math.max(w / (hw0 * 2), h / (hh0 * 2))
    w = Math.max(1, Math.round(hw0 * 2 * k))
    h = Math.max(1, Math.round(hh0 * 2 * k))
    ncx = -d.hx * hw0 + (d.hx * w) / 2
    ncy = -d.hy * hh0 + (d.hy * h) / 2
  }
  f.sx = Math.sign(f0.sx || 1) * (w / f0.src.w)
  f.sy = Math.sign(f0.sy || 1) * (h / f0.src.h)
  const [dx, dy] = rot(ncx, ncy, f0.angle)
  f.cx = f0.cx + dx
  f.cy = f0.cy + dy
}

/** "Formigas": linha branca com tracejado preto por cima. */
function drawAnts(g: CanvasRenderingContext2D, segs: [number, number, number, number][], ox: number, oy: number, z: number) {
  g.save()
  g.lineWidth = 1
  g.beginPath()
  for (const [x0, y0, x1, y1] of segs) {
    g.moveTo(Math.round(ox + x0 * z) + 0.5, Math.round(oy + y0 * z) + 0.5)
    g.lineTo(Math.round(ox + x1 * z) + 0.5, Math.round(oy + y1 * z) + 0.5)
  }
  g.strokeStyle = '#fff'
  g.stroke()
  g.setLineDash([4, 4])
  g.strokeStyle = '#000'
  g.stroke()
  g.restore()
}

/** Encaixa um ponto na grade: `up` leva ao último pixel da célula (fim de retângulo); `round` ao canto mais próximo. */
function snapToGrid(p: Pt, g: { x: number; y: number; w: number; h: number }, up: boolean, round = false): Pt {
  const f = (v: number, o: number, s: number) => {
    const k = (v - o) / s
    const c = round ? Math.round(k) : Math.floor(k)
    return up ? o + (c + 1) * s - 1 : o + c * s
  }
  return [f(p[0], g.x, g.w), f(p[1], g.y, g.h)]
}

// Pixel Studio: editor de pixel art preso à paleta do projeto e à spec do asset, com validação ao vivo.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Brush, Crosshair, Eraser, FlipHorizontal2, Grid3x3, Map as MapIcon, Minus, PaintBucket, Pipette, Plus, Redo2, Save, Scan,
  Slash, Square, SquareDashed, SquareDashedMousePointer, Trash2, Undo2,
} from 'lucide-react'
import { toast } from 'sonner'
import { pixelApi, type PixelCheck, type PixelConfig, type PixelMap, type PixelReport, type PixelSpec, type Project } from '@/core/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  drawBorder, drawChecker, drawCursor, drawGridLines, drawHighlight, drawImageSource, drawMirrorAxis, drawSelection, drawSpecOverlay, gridCanvas, type View,
} from './draw'
import { useGridHistory } from './history'
import { MapTextView } from './MapTextView'
import { PaletteBar } from './PaletteBar'
import { PreviewStrip } from './Previews'
import {
  allowedColors, buildLegend, emptyGrid, extractHighlight, getPx, gridsEqual, gridToMap, hexToInt, intToHex, mapToGrid, renderGrid, TRANSPARENT, usedColors,
  type Grid,
} from './render'
import {
  clearRegion, clipRect, floodFill, inRect, inside, linePoints, moveRegion, normRect, plot, rectPoints, shiftGrid, withMirror, type Pt, type Rect, type Tool,
} from './tools'
import { ValidationPanel } from './ValidationPanel'

export type PixelSide = 'R' | 'L' | 'both'
export type PixelSaveArgs = { map: PixelMap; side: PixelSide; paletteGroup: string | null }

export type PixelEditorProps = {
  project: Project
  config: PixelConfig
  spec: PixelSpec
  /** Enviado ao validador/pipeline: nome da spec no config ou o dict completo. */
  specRef: string | PixelSpec
  initial: PixelMap
  initialGroup: string | null
  initialSide?: PixelSide
  saving?: boolean
  /** Permite salvar sem alterações (asset novo). */
  allowClean?: boolean
  saveLabel?: string
  onSave: (a: PixelSaveArgs) => void
  onGroupChange?: (g: string | null) => void
  /** Conteúdo extra no topo da coluna lateral (ex.: nome/pasta do asset novo). */
  sideExtra?: ReactNode
}

const ZMIN = 4
const ZMAX = 40
const clampZ = (z: number) => Math.max(ZMIN, Math.min(ZMAX, Math.round(z)))

type Drag =
  | { kind: 'pan'; sm: Pt; sv: View }
  | { kind: 'paint'; before: Grid; last: Pt; color: number }
  | { kind: 'shape'; shape: 'line' | 'rect'; start: Pt; color: number }
  | { kind: 'select'; start: Pt }
  | { kind: 'move'; start: Pt; before: Grid; sel: Rect }

const TOOLS: { id: Tool; icon: ReactNode; label: string; key: string }[] = [
  { id: 'pencil', icon: <Brush />, label: 'Lápis', key: 'B' },
  { id: 'eraser', icon: <Eraser />, label: 'Borracha', key: 'E' },
  { id: 'fill', icon: <PaintBucket />, label: 'Balde (4-conexo)', key: 'G' },
  { id: 'picker', icon: <Pipette />, label: 'Conta-gotas', key: 'I' },
  { id: 'line', icon: <Slash />, label: 'Linha', key: 'L' },
  { id: 'rect', icon: <Square />, label: 'Retângulo', key: 'R' },
  { id: 'select', icon: <SquareDashedMousePointer />, label: 'Selecionar e mover', key: 'S' },
]
const KEY_TOOL: Record<string, Tool> = { b: 'pencil', e: 'eraser', g: 'fill', i: 'picker', l: 'line', r: 'rect', s: 'select' }

export function PixelEditor(props: PixelEditorProps) {
  const { project, config, spec, specRef, initial, saving, allowClean, onSave, sideExtra } = props
  const initialGrid = useMemo(() => {
    try {
      return { grid: mapToGrid(initial), error: null as string | null }
    } catch (e) {
      return { grid: emptyGrid(initial.width, initial.height), error: String(e) }
    }
  }, [initial])
  const outlineColor = useMemo(() => {
    try {
      return hexToInt(initial.outline.color || spec.outline_color)
    } catch {
      return 0x0b0a12
    }
  }, [initial, spec])
  const legend = useMemo(() => buildLegend(config.palette, initial.legend, intToHex(outlineColor)), [config.palette, initial.legend, outlineColor])

  const hist = useGridHistory(initialGrid.grid)
  const grid = hist.grid
  const W = grid.w, H = grid.h

  const [group, setGroup] = useState<string | null>(props.initialGroup)
  const allowed = useMemo(() => allowedColors(config.palette, group), [config.palette, group])
  const coreCount = useMemo(() => allowedColors(config.palette, null).length, [config.palette])
  const [colorPick, setColor] = useState<number>(() => allowed.find((c) => c !== outlineColor) ?? allowed[0] ?? 0)
  const color = allowed.includes(colorPick) ? colorPick : (allowed[0] ?? 0)

  const [tool, setTool] = useState<Tool>('pencil')
  const [mirror, setMirror] = useState(false)
  const [rectFilled, setRectFilled] = useState(false)
  const [showGrid, setShowGrid] = useState(true)
  const [showOverlay, setShowOverlay] = useState(true)
  const [outlineAuto, setOutlineAuto] = useState(initial.outline.auto)
  const [showOutline, setShowOutline] = useState(true)
  const [showL, setShowL] = useState(false)
  const [mode, setMode] = useState<'canvas' | 'text'>('canvas')
  const [side, setSide] = useState<PixelSide>(props.initialSide ?? 'R')
  const [sel, setSel] = useState<Rect | null>(null)
  const [hover, setHover] = useState<Pt | null>(null)
  const [preview, setPreviewState] = useState<{ pts: Pt[]; color: number } | null>(null)
  const previewRef = useRef<{ pts: Pt[]; color: number } | null>(null)
  const setPreview = (pv: { pts: Pt[]; color: number } | null) => {
    previewRef.current = pv
    setPreviewState(pv)
  }
  const [view, setView] = useState<View>({ z: 16, ox: 0, oy: 0 })
  const [box, setBox] = useState({ w: 0, h: 0 })
  const userView = useRef(false) // usuário mexeu no zoom/pan: para de reajustar ao redimensionar
  const drag = useRef<Drag | null>(null)
  const space = useRef(false)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  // ---------- mapa + validação ----------
  const map = useMemo(
    () => gridToMap(grid, legend, { outline: { auto: outlineAuto, color: intToHex(outlineColor) }, side: initial.side, shade_L: initial.shade_L }),
    [grid, legend, outlineAuto, outlineColor, initial.side, initial.shade_L],
  )
  const mapKey = JSON.stringify(map)
  const specKey = typeof specRef === 'string' ? specRef : JSON.stringify(specRef)
  const [report, setReport] = useState<PixelReport | null>(null)
  const [vErr, setVErr] = useState<string | null>(null)
  const [vLoading, setVLoading] = useState(false)
  const [checkId, setCheckId] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const t = window.setTimeout(async () => {
      setVLoading(true)
      try {
        const r = await pixelApi.validate(project.slug, { map: JSON.parse(mapKey) as PixelMap, spec: specRef, palette_group: group })
        if (live) {
          setReport(r)
          setVErr(null)
        }
      } catch (e) {
        if (live) setVErr(e instanceof Error ? e.message : String(e))
      } finally {
        if (live) setVLoading(false)
      }
    }, 500)
    return () => {
      live = false
      window.clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapKey, project.slug, specKey, group])
  const highlight = useMemo(() => {
    const c = report?.checks.find((k) => k.id === checkId)
    return c ? extractHighlight(c.data) : null
  }, [report, checkId])

  // ---------- render ----------
  const finalGrid = useMemo(
    () => renderGrid(grid, { outline: outlineAuto ? outlineColor : null, shade: showL ? initial.shade_L : null, outlineColor }),
    [grid, outlineAuto, outlineColor, showL, initial.shade_L],
  )
  const displayGrid = useMemo(() => {
    const base = preview ? plot(grid, preview.pts, preview.color) : grid
    return renderGrid(base, { outline: outlineAuto && showOutline ? outlineColor : null, shade: showL ? initial.shade_L : null, outlineColor })
  }, [grid, preview, outlineAuto, showOutline, outlineColor, showL, initial.shade_L])

  const used = useMemo(() => usedColors(finalGrid), [finalGrid])
  const offPalette = useMemo(() => usedColors(grid).filter((c) => !allowed.includes(c)), [grid, allowed])
  const tolerated = useMemo(() => new Set(usedColors(initialGrid.grid)), [initialGrid])

  // tamanho do viewport
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: Math.floor(el.clientWidth), h: Math.floor(el.clientHeight) }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [mode])

  const fit = useCallback(() => {
    if (!box.w || !box.h) return
    const z = clampZ(Math.floor(Math.min((box.w - 32) / W, (box.h - 32) / H)))
    setView({ z, ox: Math.round((box.w - W * z) / 2), oy: Math.round((box.h - H * z) / 2) })
  }, [box, W, H])
  useEffect(() => {
    if (!userView.current && box.w && box.h) fit()
  }, [box, fit])

  const zoomAt = useCallback((nz: number, mx: number, my: number) => {
    userView.current = true
    setView((v) => {
      const z = clampZ(nz)
      if (z === v.z) return v
      return { z, ox: Math.round(mx - ((mx - v.ox) * z) / v.z), oy: Math.round(my - ((my - v.oy) * z) / v.z) }
    })
  }, [])

  useEffect(() => {
    const c = canvasRef.current
    if (!c || mode !== 'canvas') return
    if (c.width !== box.w) c.width = box.w
    if (c.height !== box.h) c.height = box.h
    const ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, c.width, c.height)
    drawChecker(ctx, W, H, view)
    drawImageSource(ctx, gridCanvas(displayGrid), W, H, view)
    if (showGrid) drawGridLines(ctx, W, H, view)
    drawBorder(ctx, W, H, view)
    if (showOverlay) drawSpecOverlay(ctx, spec, H, view, { safe: true, pivot: true })
    if (mirror) drawMirrorAxis(ctx, W, H, view)
    if (highlight) drawHighlight(ctx, highlight, view)
    if (sel) drawSelection(ctx, sel, view, 0)
    if (hover && inside(grid, hover[0], hover[1]) && tool !== 'select') drawCursor(ctx, withMirror([hover], W, mirror && tool !== 'picker'), view)
  }, [box, view, displayGrid, showGrid, showOverlay, spec, mirror, highlight, sel, hover, tool, grid, W, H, mode])

  // zoom pela roda (listener não-passivo)
  useEffect(() => {
    const c = canvasRef.current
    if (!c) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      userView.current = true
      const r = c.getBoundingClientRect()
      setView((v) => {
        const z = clampZ(v.z + (e.deltaY < 0 ? 1 : -1) * Math.max(1, Math.round(v.z / 8)))
        const mx = e.clientX - r.left, my = e.clientY - r.top
        return z === v.z ? v : { z, ox: Math.round(mx - ((mx - v.ox) * z) / v.z), oy: Math.round(my - ((my - v.oy) * z) / v.z) }
      })
    }
    c.addEventListener('wheel', onWheel, { passive: false })
    return () => c.removeEventListener('wheel', onWheel)
  }, [mode])

  // ---------- ponteiro ----------
  const locate = (e: React.PointerEvent) => {
    const r = canvasRef.current!.getBoundingClientRect()
    const mx = e.clientX - r.left, my = e.clientY - r.top
    return { mx, my, p: [Math.floor((mx - view.ox) / view.z), Math.floor((my - view.oy) / view.z)] as Pt }
  }

  const pick = (p: Pt) => {
    const v = getPx(hist.ref.current, p[0], p[1])
    if (v === TRANSPARENT) return
    if (allowed.includes(v)) {
      setColor(v)
      setTool('pencil')
    } else toast.warning(`${intToHex(v)} não está na paleta permitida`)
  }

  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    const { mx, my, p } = locate(e)
    if (e.button === 1 || space.current || (e.button === 0 && e.altKey)) {
      userView.current = true
      drag.current = { kind: 'pan', sm: [mx, my], sv: view }
      return
    }
    const g = hist.ref.current
    const erase = e.button === 2
    const c = erase ? TRANSPARENT : color
    switch (tool) {
      case 'picker':
        pick(p)
        return
      case 'fill': {
        if (!inside(g, p[0], p[1])) return
        let out = floodFill(g, p[0], p[1], c)
        if (mirror) out = floodFill(out, W - 1 - p[0], p[1], c)
        hist.commit(out)
        return
      }
      case 'pencil':
      case 'eraser': {
        const col = tool === 'eraser' ? TRANSPARENT : c
        drag.current = { kind: 'paint', before: g, last: p, color: col }
        hist.set(plot(g, withMirror([p], W, mirror), col))
        return
      }
      case 'line':
      case 'rect':
        drag.current = { kind: 'shape', shape: tool, start: p, color: c }
        setPreview({ pts: withMirror([p], W, mirror), color: c })
        return
      case 'select':
        if (sel && inRect(sel, p[0], p[1])) drag.current = { kind: 'move', start: p, before: g, sel }
        else {
          drag.current = { kind: 'select', start: p }
          setSel(clipRect(normRect(p[0], p[1], p[0], p[1]), g))
        }
    }
  }

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { mx, my, p } = locate(e)
    setHover((h) => (h && h[0] === p[0] && h[1] === p[1] ? h : p))
    const d = drag.current
    if (!d) return
    switch (d.kind) {
      case 'pan':
        setView({ ...d.sv, ox: d.sv.ox + mx - d.sm[0], oy: d.sv.oy + my - d.sm[1] })
        return
      case 'paint':
        if (d.last[0] === p[0] && d.last[1] === p[1]) return
        hist.set(plot(hist.ref.current, withMirror(linePoints(d.last[0], d.last[1], p[0], p[1]), W, mirror), d.color))
        d.last = p
        return
      case 'shape': {
        const pts = d.shape === 'line' ? linePoints(d.start[0], d.start[1], p[0], p[1]) : rectPoints(d.start[0], d.start[1], p[0], p[1], rectFilled)
        setPreview({ pts: withMirror(pts, W, mirror), color: d.color })
        return
      }
      case 'select':
        setSel(clipRect(normRect(d.start[0], d.start[1], p[0], p[1]), hist.ref.current))
        return
      case 'move': {
        const dx = p[0] - d.start[0], dy = p[1] - d.start[1]
        hist.set(moveRegion(d.before, d.sel, dx, dy))
        setSel({ ...d.sel, x: d.sel.x + dx, y: d.sel.y + dy })
      }
    }
  }

  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (!d) return
    if ((d.kind === 'paint' || d.kind === 'move') && !gridsEqual(d.before, hist.ref.current)) hist.record(d.before)
    if (d.kind === 'move') setSel((s) => (s ? clipRect(s, hist.ref.current) : s))
    if (d.kind === 'shape') {
      const pv = previewRef.current
      setPreview(null)
      if (pv) {
        const out = plot(hist.ref.current, pv.pts, pv.color)
        if (!gridsEqual(out, hist.ref.current)) hist.commit(out)
      }
    }
  }

  // ---------- teclado ----------
  const shift = (dx: number, dy: number) => {
    const g = hist.ref.current
    if (sel) {
      hist.commit(moveRegion(g, sel, dx, dy))
      setSel({ ...sel, x: sel.x + dx, y: sel.y + dy })
    } else hist.commit(shiftGrid(g, dx, dy))
  }
  const zoomCenter = (dir: number) => zoomAt(view.z + dir * Math.max(1, Math.round(view.z / 8)), box.w / 2, box.h / 2)

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable) return
      const k = e.key.toLowerCase()
      if (e.ctrlKey || e.metaKey) {
        if (k === 'z' && !e.shiftKey) hist.undo()
        else if (k === 'y' || (k === 'z' && e.shiftKey)) hist.redo()
        else if (k === 'a') setSel({ x: 0, y: 0, w: W, h: H })
        else return
        e.preventDefault()
        return
      }
      if (e.altKey) return
      if (k === ' ') {
        space.current = true
        e.preventDefault()
        return
      }
      if (KEY_TOOL[k]) {
        if (k === 'r' && tool === 'rect') setRectFilled((f) => !f)
        setTool(KEY_TOOL[k])
      } else if (k === 'm') setMirror((m) => !m)
      else if (k === '[') zoomCenter(-1)
      else if (k === ']') zoomCenter(1)
      else if (/^[1-9]$/.test(k)) {
        const c = allowed[Number(k) - 1]
        if (c !== undefined) setColor(c)
      } else if (k.startsWith('arrow')) {
        const d = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[k]
        if (!d) return
        shift(d[0], d[1])
      } else if ((k === 'delete' || k === 'backspace') && sel) hist.commit(clearRegion(hist.ref.current, sel))
      else if (k === 'escape') {
        setSel(null)
        setCheckId(null)
      } else return
      e.preventDefault()
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

  const dirty = !gridsEqual(grid, initialGrid.grid) || group !== props.initialGroup || outlineAuto !== initial.outline.auto
  const hv = hover && inside(grid, hover[0], hover[1]) ? hover : null
  const hvColor = hv ? getPx(grid, hv[0], hv[1]) : TRANSPARENT
  const onSelectCheck = (c: PixelCheck | null) => {
    setCheckId(c?.id ?? null)
    if (c) {
      const h = extractHighlight(c.data)
      if (!h.pixels.length && !h.rects.length) toast.message('Esta checagem não indica pixels')
    }
  }

  const toggle = (on: boolean, set: (v: boolean) => void, icon: ReactNode, title: string, disabled?: boolean) => (
    <Button size="icon-sm" variant={on ? 'secondary' : 'ghost'} title={title} disabled={disabled} onClick={() => set(!on)}>{icon}</Button>
  )

  return (
    <div className="@container flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5">
        {TOOLS.map((t) => (
          <Button key={t.id} size="icon-sm" variant={tool === t.id ? 'secondary' : 'ghost'} title={`${t.label} (${t.key})`} onClick={() => (t.id === 'rect' && tool === 'rect' ? setRectFilled(!rectFilled) : setTool(t.id))}>
            {t.id === 'rect' && rectFilled ? <Square className="fill-current" /> : t.icon}
          </Button>
        ))}
        {tool === 'rect' && (
          <Button size="xs" variant="ghost" onClick={() => setRectFilled(!rectFilled)} title="Alternar contorno/preenchido (R de novo)">
            {rectFilled ? 'preenchido' : 'contorno'}
          </Button>
        )}
        {toggle(mirror, setMirror, <FlipHorizontal2 />, 'Espelho X ao desenhar (M)')}
        <span className="mx-1 h-5 w-px bg-border" />
        <Button size="icon-sm" variant="ghost" title="Desfazer (Ctrl+Z)" disabled={!hist.canUndo} onClick={hist.undo}><Undo2 /></Button>
        <Button size="icon-sm" variant="ghost" title="Refazer (Ctrl+Y)" disabled={!hist.canRedo} onClick={hist.redo}><Redo2 /></Button>
        <Button size="icon-sm" variant="ghost" title="Limpar tudo" onClick={() => hist.commit(emptyGrid(W, H))}><Trash2 /></Button>
        {sel && <Button size="xs" variant="ghost" onClick={() => setSel(null)} title="Esc">limpar seleção</Button>}
        <span className="mx-1 h-5 w-px bg-border" />
        {toggle(showGrid, setShowGrid, <Grid3x3 />, 'Grade')}
        {toggle(showOverlay, setShowOverlay, <Crosshair />, 'Overlay da spec (área segura e pivô)')}
        {toggle(showOutline, setShowOutline, <SquareDashed />, outlineAuto ? 'Prévia do contorno automático' : 'Contorno automático desligado no mapa', !outlineAuto)}
        <Button size="xs" variant={showL ? 'secondary' : 'ghost'} title="Prévia do lado L (escurecido, exceto contorno)" onClick={() => setShowL(!showL)}>_L</Button>
        <div className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
          <Button size="xs" variant={mode === 'canvas' ? 'secondary' : 'ghost'} onClick={() => setMode('canvas')}><Brush /> Canvas</Button>
          <Button size="xs" variant={mode === 'text' ? 'secondary' : 'ghost'} onClick={() => setMode('text')}><MapIcon /> Mapa</Button>
          <span className="mx-1 h-5 w-px bg-border" />
          <Button size="icon-xs" variant="ghost" title="Diminuir zoom ([)" onClick={() => zoomCenter(-1)}><Minus /></Button>
          <span className="w-9 text-center tabular-nums">{view.z}x</span>
          <Button size="icon-xs" variant="ghost" title="Aumentar zoom (])" onClick={() => zoomCenter(1)}><Plus /></Button>
          <Button size="icon-xs" variant="ghost" title="Ajustar" onClick={() => {
            userView.current = false
            fit()
          }}><Scan /></Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto @3xl:grid-cols-[minmax(0,1fr)_280px] @3xl:overflow-hidden">
        <div className="flex min-h-[360px] min-w-0 flex-col">
          {initialGrid.error && <p className="bg-red-950/70 px-2 py-1 text-xs text-red-200">pixelmap inválido ({initialGrid.error}); começando em branco.</p>}
          {mode === 'canvas' ? (
            <div ref={wrapRef} className="relative min-h-0 flex-1 overflow-hidden bg-[#141418]">
              <canvas
                ref={canvasRef}
                className={cn('absolute inset-0 touch-none', drag.current?.kind === 'pan' || space.current ? 'cursor-grab' : 'cursor-crosshair')}
                onPointerDown={onDown}
                onPointerMove={onMove}
                onPointerUp={onUp}
                onPointerCancel={onUp}
                onPointerLeave={() => setHover(null)}
                onContextMenu={(e) => e.preventDefault()}
              />
            </div>
          ) : (
            <div className="min-h-0 flex-1 p-2">
              <MapTextView grid={grid} legend={legend} allowed={allowed} tolerated={tolerated} onApply={hist.commit} />
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-3 border-t px-2 py-1 font-mono text-[11px] text-muted-foreground">
            <span>{W}×{H}</span>
            {hv ? (
              <span>
                x{hv[0]} y{hv[1]} <span className="opacity-60">(Unity {hv[0]},{H - 1 - hv[1]})</span>{' '}
                {hvColor === TRANSPARENT ? 'transparente' : `${intToHex(hvColor)} '${legend.charOf.get(hvColor) ?? '?'}'`}
              </span>
            ) : <span className="opacity-60">roda: zoom · botão do meio/Espaço/Alt+arrastar: mover · botão direito: apagar</span>}
            <span className={cn('ml-auto', spec.max_colors && used.length > spec.max_colors && 'text-red-400')}>
              {used.length}{spec.max_colors ? `/${spec.max_colors}` : ''} cores
            </span>
            {dirty && <span className="text-amber-400">● alterado</span>}
          </div>
        </div>

        <aside className="min-h-0 space-y-4 border-t p-3 @3xl:overflow-auto @3xl:border-t-0 @3xl:border-l">
          {sideExtra}
          <section className="space-y-1.5">
            <h4 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Tamanho real (1x / 2x){showL && ' · _L'}</h4>
            <PreviewStrip grid={finalGrid} />
          </section>
          <section className="space-y-1.5">
            <h4 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Paleta</h4>
            <PaletteBar palette={config.palette} group={group} onGroup={(g) => {
              setGroup(g)
              props.onGroupChange?.(g)
            }}allowed={allowed} legend={legend} color={color} onColor={(c) => {
              setColor(c)
              if (tool === 'eraser' || tool === 'picker' || tool === 'select') setTool('pencil')
            }} offPalette={offPalette} coreCount={coreCount} />
            <label className="flex items-center gap-2 pt-1 text-xs">
              <input type="checkbox" checked={outlineAuto} onChange={(e) => setOutlineAuto(e.target.checked)} />
              Contorno automático <span className="inline-block size-3 rounded-sm border" style={{ background: intToHex(outlineColor) }} />
              <span className="text-muted-foreground">(spec: {spec.outline})</span>
            </label>
          </section>
          <section className="space-y-1.5">
            <h4 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Validadores</h4>
            <ValidationPanel report={report} error={vErr} loading={vLoading} selected={checkId} onSelect={onSelectCheck} />
          </section>
          <section className="space-y-2 border-t pt-3">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">Lado</span>
              {(['R', 'L', 'both'] as const).map((s) => (
                <Button key={s} size="xs" variant={side === s ? 'secondary' : 'ghost'} onClick={() => setSide(s)}>{s === 'both' ? 'R + L' : s}</Button>
              ))}
            </div>
            <Button className="w-full" disabled={saving || (!dirty && !allowClean)} onClick={() => onSave({ map, side, paletteGroup: group })}>
              <Save /> {props.saveLabel ?? 'Salvar versão'}
            </Button>
            {report && !report.ok && <p className="text-[11px] text-amber-400">O desenho ainda reprova em validadores; dá para salvar mesmo assim.</p>}
          </section>
        </aside>
      </div>
    </div>
  )
}

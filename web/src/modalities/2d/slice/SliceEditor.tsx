// Editor de recorte de sprites: abre uma folha de sprites (PNG com grade), define a grade (colunas/linhas ou
// tamanho da célula, margem, espaçamento), detecta grade e sprites sozinho ou desenha retângulos à mão,
// escolhe quais células ficam e roda o pipeline 2d.slice (um asset por recorte e/ou uma animação).
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { Grid3x3, Loader2, Minus, Plus, ScanSearch, Scissors, Square, SquareDashedMousePointer, Trash2, Wand2 } from 'lucide-react'
import { toast } from 'sonner'
import { primaryFile } from '@/core/api'
import type { PanelProps } from '@/core/registry'
import { checker } from '@/core/components/ImageViewer'
import { runJob, splitRef } from '@/modalities/3d/editor/jobs'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { contentMask, countIn, defaultGrid, detectGrid, detectSprites, gridCells, integral, resolveGrid, type GridParams, type Rect } from './sheet'

type Mode = 'grid' | 'auto' | 'manual'
type By = 'count' | 'size'

const num = (v: string, min = 0) => Math.max(min, Math.round(Number(v) || 0))

function NumField({ label, value, onChange, min = 0, title }: { label: string; value: number | undefined; onChange: (n: number) => void; min?: number; title?: string }) {
  return (
    <label className="grid gap-0.5 text-[11px] text-muted-foreground" title={title}>
      {label}
      <Input
        type="number"
        min={min}
        className="h-7 px-2 text-xs"
        value={value ?? ''}
        onChange={(e) => onChange(num(e.target.value, min))}
      />
    </label>
  )
}

export function SliceEditor({ asset, project }: PanelProps) {
  const file = primaryFile(asset)!
  const qc = useQueryClient()
  const nav = useNavigate()
  const wrap = useRef<HTMLDivElement>(null)
  const base = useRef<HTMLCanvasElement>(null)
  const over = useRef<HTMLCanvasElement>(null)
  const preview = useRef<HTMLCanvasElement>(null)
  const src = useRef<HTMLCanvasElement | null>(null) // imagem inteira (recortes da prévia)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [pixels, setPixels] = useState<{ rgba: Uint8ClampedArray; mask: Uint8Array; ii: Int32Array } | null>(null)
  const [zoom, setZoom] = useState(1)

  const [mode, setMode] = useState<Mode>('grid')
  const [by, setBy] = useState<By>('count')
  const [grid, setGrid] = useState<GridParams>(defaultGrid)
  const [minArea, setMinArea] = useState(4)
  const [mergeGap, setMergeGap] = useState(0)
  const [autoRects, setAutoRects] = useState<Rect[]>([])
  const [manual, setManual] = useState<Rect[]>([])
  const [active, setActive] = useState<number | null>(null) // retângulo manual selecionado
  const [draft, setDraft] = useState<Rect | null>(null)
  const [on, setOn] = useState<Record<number, boolean>>({})
  const [names, setNames] = useState<Record<number, string>>({})
  const [hover, setHover] = useState<number | null>(null)
  const paint = useRef<{ value: boolean; last?: { x: number; y: number } } | null>(null)
  const dragStart = useRef<{ x: number; y: number; moved: boolean } | null>(null)

  const [prefix, setPrefix] = useState(asset.slug)
  const [makeSprites, setMakeSprites] = useState(true)
  const [makeAnim, setMakeAnim] = useState(false)
  const [trim, setTrim] = useState(false)
  const [pad, setPad] = useState(0)
  const [fps, setFps] = useState(10)
  const [loop, setLoop] = useState(true)
  const [anchor, setAnchor] = useState<'center' | 'bottom' | 'none'>('center')
  const [busy, setBusy] = useState<string | null>(null)

  // ---- carrega a imagem ----------------------------------------------------
  useEffect(() => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const W = img.naturalWidth
      const H = img.naturalHeight
      const c = document.createElement('canvas')
      c.width = W
      c.height = H
      const g = c.getContext('2d', { willReadFrequently: true })!
      g.drawImage(img, 0, 0)
      const rgba = g.getImageData(0, 0, W, H).data
      const mask = contentMask(rgba, W, H)
      src.current = c
      setPixels({ rgba, mask, ii: integral(mask, W, H) })
      setSize({ w: W, h: H })
      const box = wrap.current?.getBoundingClientRect()
      if (box) {
        const k = Math.min((box.width - 32) / W, (box.height - 32) / H)
        // pixel art pequena nunca abaixo de 100% (role a área se não couber)
        setZoom(k >= 1 ? Math.max(1, Math.floor(k)) : Math.max(W <= 1024 && H <= 1024 ? 1 : 0.1, Math.round(k * 20) / 20))
      }
      // primeira sugestão de grade
      const guess = detectGrid(rgba, mask, W, H)
      if (guess.method !== 'none') setGrid(guess.grid)
    }
    img.src = file.url
  }, [file.url])

  // ---- células -------------------------------------------------------------
  const gridForCells: GridParams = useMemo(
    () => (by === 'count' ? { ...grid, cellW: undefined, cellH: undefined } : { ...grid, columns: undefined, rows: undefined }),
    [grid, by],
  )
  const cells: Rect[] = useMemo(() => {
    if (!size.w) return []
    if (mode === 'grid') return gridCells(size.w, size.h, gridForCells)
    return mode === 'auto' ? autoRects : manual
  }, [mode, size, gridForCells, autoRects, manual])

  const isEmpty = (r: Rect) => (pixels ? countIn(pixels.ii, size.w, r) === 0 : false)
  const isOn = (i: number) => on[i] ?? !isEmpty(cells[i])
  const chosen = cells.map((r, i) => ({ r, i })).filter(({ i }) => isOn(i))

  const resolved = size.w ? resolveGrid(size.w, size.h, gridForCells) : null

  const detectAuto = () => {
    if (!pixels) return
    const r = detectSprites(pixels.mask, size.w, size.h, minArea, mergeGap)
    setAutoRects(r)
    setOn({})
    setNames({})
    toast.success(`${r.length} sprite(s) encontrado(s)`)
  }
  const guessGrid = () => {
    if (!pixels) return
    const g = detectGrid(pixels.rgba, pixels.mask, size.w, size.h)
    if (g.method === 'none') {
      toast.info('Não encontrei uma grade clara. Ajuste manualmente.')
      return
    }
    setGrid(g.grid)
    setBy('size')
    toast.success(`Grade ${g.grid.cellW}×${g.grid.cellH} (${g.grid.columns}×${g.grid.rows}) — ${g.method === 'gaps' ? 'pelos vãos entre sprites' : 'pelas emendas'}`)
  }
  const switchMode = (m: Mode) => {
    setMode(m)
    setOn({})
    setNames({})
    setActive(null)
    if (m === 'auto' && !autoRects.length && pixels) setAutoRects(detectSprites(pixels.mask, size.w, size.h, minArea, mergeGap))
  }

  // ---- desenho -------------------------------------------------------------
  const cssW = Math.round(size.w * zoom)
  const cssH = Math.round(size.h * zoom)
  useEffect(() => {
    const c = base.current
    if (!c || !src.current) return
    c.width = cssW
    c.height = cssH
    const g = c.getContext('2d')!
    g.imageSmoothingEnabled = false
    g.drawImage(src.current, 0, 0, cssW, cssH)
  }, [cssW, cssH, pixels])

  useEffect(() => {
    const c = over.current
    if (!c) return
    c.width = cssW
    c.height = cssH
    const g = c.getContext('2d')!
    g.clearRect(0, 0, cssW, cssH)
    g.font = '11px ui-monospace, monospace'
    cells.forEach((r, i) => {
      const [x, y, w, h] = r
      const sel = isOn(i)
      g.fillStyle = sel ? 'rgba(255,64,64,0.16)' : 'rgba(0,0,0,0.35)'
      g.fillRect(x * zoom, y * zoom, w * zoom, h * zoom)
      g.strokeStyle = i === hover || i === active ? '#ffe14d' : sel ? '#ff4040' : 'rgba(180,180,180,0.7)'
      g.lineWidth = i === hover || i === active ? 2 : 1
      g.strokeRect(x * zoom + 0.5, y * zoom + 0.5, w * zoom - 1, h * zoom - 1)
      if (w * zoom >= 26 && h * zoom >= 16) {
        g.fillStyle = sel ? '#fff' : '#aaa'
        g.fillText(String(i + 1), x * zoom + 3, y * zoom + 12)
      }
    })
    if (draft) {
      g.strokeStyle = '#4dd0ff'
      g.setLineDash([4, 3])
      g.strokeRect(draft[0] * zoom + 0.5, draft[1] * zoom + 0.5, draft[2] * zoom - 1, draft[3] * zoom - 1)
      g.setLineDash([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cells, on, zoom, hover, active, draft, cssW, cssH, pixels])

  // ---- prévia da animação --------------------------------------------------
  useEffect(() => {
    const c = preview.current
    if (!c || !src.current || !chosen.length) return
    const rects = chosen.map(({ r }) => r)
    const mw = Math.max(...rects.map((r) => r[2]))
    const mh = Math.max(...rects.map((r) => r[3]))
    const k = Math.max(1, Math.floor(Math.min(120 / mw, 120 / mh)))
    c.width = mw * k
    c.height = mh * k
    const g = c.getContext('2d')!
    g.imageSmoothingEnabled = false
    let f = 0
    const draw = () => {
      const [x, y, w, h] = rects[f % rects.length]
      g.clearRect(0, 0, c.width, c.height)
      const dy = anchor === 'bottom' ? mh - h : Math.floor((mh - h) / 2)
      g.drawImage(src.current!, x, y, w, h, Math.floor((mw - w) / 2) * k, dy * k, w * k, h * k)
      f++
    }
    draw()
    if (rects.length < 2) return
    const t = window.setInterval(draw, 1000 / fps)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen.map((c) => c.i).join(','), cells, fps, anchor, makeAnim])

  // ---- ponteiro ------------------------------------------------------------
  const toImg = (e: React.PointerEvent) => {
    const b = over.current!.getBoundingClientRect()
    return { x: Math.floor((e.clientX - b.left) / zoom), y: Math.floor((e.clientY - b.top) / zoom) }
  }
  const hit = (x: number, y: number): number | null => {
    for (let i = cells.length - 1; i >= 0; i--) {
      const [cx, cy, w, h] = cells[i]
      if (x >= cx && y >= cy && x < cx + w && y < cy + h) return i
    }
    return null
  }
  const clampRect = (a: { x: number; y: number }, b: { x: number; y: number }): Rect => {
    const x0 = Math.max(0, Math.min(a.x, b.x))
    const y0 = Math.max(0, Math.min(a.y, b.y))
    const x1 = Math.min(size.w, Math.max(a.x, b.x) + 1)
    const y1 = Math.min(size.h, Math.max(a.y, b.y) + 1)
    return [x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)]
  }
  const onDown = (e: React.PointerEvent) => {
    over.current!.setPointerCapture(e.pointerId)
    const p = toImg(e)
    dragStart.current = { ...p, moved: false }
    if (mode !== 'manual') {
      const i = hit(p.x, p.y)
      if (i !== null) {
        paint.current = { value: !isOn(i), last: p }
        setOn((s) => ({ ...s, [i]: paint.current!.value }))
      }
    }
  }
  const onMove = (e: React.PointerEvent) => {
    const p = toImg(e)
    const i = hit(p.x, p.y)
    setHover(i)
    const ds = dragStart.current
    if (!ds) return
    if (mode === 'manual') {
      if (Math.abs(p.x - ds.x) + Math.abs(p.y - ds.y) >= 2) ds.moved = true
      if (ds.moved) setDraft(clampRect(ds, p))
    } else if (paint.current) {
      // passa por todas as células entre o ponto anterior e o atual (movimentos rápidos pulam eventos)
      const prev = paint.current.last ?? p
      const steps = Math.max(1, Math.ceil(Math.hypot(p.x - prev.x, p.y - prev.y)))
      const hits = new Set<number>()
      for (let k = 0; k <= steps; k++) {
        const j = hit(Math.round(prev.x + ((p.x - prev.x) * k) / steps), Math.round(prev.y + ((p.y - prev.y) * k) / steps))
        if (j !== null) hits.add(j)
      }
      paint.current.last = p
      const v = paint.current.value
      setOn((s) => ([...hits].every((j) => s[j] === v) ? s : { ...s, ...Object.fromEntries([...hits].map((j) => [j, v])) }))
    }
  }
  const onUp = (e: React.PointerEvent) => {
    const ds = dragStart.current
    dragStart.current = null
    paint.current = null
    if (mode !== 'manual' || !ds) return
    const p = toImg(e)
    if (ds.moved) {
      const r = clampRect(ds, p)
      setManual((m) => [...m, r])
      setActive(manual.length)
      setDraft(null)
    } else {
      const i = hit(p.x, p.y)
      setActive(i)
    }
  }
  const removeActive = () => {
    if (active === null) return
    setManual((m) => m.filter((_, i) => i !== active))
    setOn({})
    setNames({})
    setActive(null)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = (e.target as HTMLElement).tagName
      if (t === 'INPUT' || t === 'TEXTAREA') return
      if (mode === 'manual' && (e.key === 'Delete' || e.key === 'Backspace')) removeActive()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // ---- executar ------------------------------------------------------------
  const run = async () => {
    if (!chosen.length) return toast.error('Nenhum recorte selecionado')
    if (!makeSprites && !makeAnim) return toast.error('Marque “Um asset por recorte” e/ou “Criar animação”')
    setBusy('na fila…')
    try {
      const rects = chosen.map(({ r, i }) => ({ x: r[0], y: r[1], w: r[2], h: r[3], name: names[i]?.trim() || `${prefix || asset.slug}-${String(i + 1).padStart(2, '0')}` }))
      const ref = await runJob(
        project.slug,
        '2d.slice',
        { input_asset: asset.ref, mode: 'rects', rects, skip_empty: false, trim, pad, sprites: makeSprites, animation: makeAnim, fps, loop, anchor, prefix: prefix || undefined },
        (j) => setBusy(`recortando ${Math.round((j.progress ?? 0) * 100)}%`),
      )
      qc.invalidateQueries({ queryKey: ['assets', project.slug] })
      qc.invalidateQueries({ queryKey: ['folders', project.slug] })
      const { slug, version } = splitRef(ref)
      toast.success(`${chosen.length} recorte(s) criados`, { action: { label: 'Abrir resultado', onClick: () => nav(`/p/${project.slug}/a/${slug}/${version}`) } })
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  const setG = (patch: Partial<GridParams>) => setGrid((g) => ({ ...g, ...patch }))
  const nOn = chosen.length
  const zoomBy = (f: number) => setZoom((z) => Math.min(32, Math.max(0.1, Math.round(z * f * 100) / 100)))
  const manualRect = active !== null && mode === 'manual' ? manual[active] : null

  return (
    <div className="flex h-full min-h-0 flex-col xl:flex-row">
      {/* canvas */}
      <div className="relative flex min-h-[45%] min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b px-2 py-1 text-xs">
          <Button size="icon-sm" variant="ghost" onClick={() => zoomBy(0.5)} title="Menos zoom"><Minus /></Button>
          <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <Button size="icon-sm" variant="ghost" onClick={() => zoomBy(2)} title="Mais zoom"><Plus /></Button>
          <span className="ml-2 truncate text-muted-foreground">
            {size.w}×{size.h} px · {cells.length} célula(s) · {nOn} selecionada(s)
            {mode === 'grid' && resolved ? ` · célula ${resolved.cw}×${resolved.ch}` : ''}
          </span>
          <span className="ml-auto hidden truncate text-muted-foreground 2xl:inline">
            {mode === 'manual' ? 'Arraste para desenhar um retângulo; clique para selecionar; Delete remove.' : 'Clique ou arraste para marcar/desmarcar células.'}
          </span>
        </div>
        <div ref={wrap} className={cn('min-h-0 flex-1 overflow-auto p-4', checker)}>
          <div className="relative" style={{ width: cssW, height: cssH }}>
            <canvas ref={base} className="absolute inset-0" style={{ width: cssW, height: cssH, imageRendering: 'pixelated' }} />
            <canvas
              ref={over}
              className="absolute inset-0 cursor-crosshair touch-none"
              style={{ width: cssW, height: cssH }}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerLeave={() => setHover(null)}
            />
          </div>
        </div>
      </div>

      {/* painel */}
      <aside className="max-h-[55%] w-full shrink-0 space-y-4 overflow-y-auto border-t p-3 text-sm xl:max-h-none xl:w-80 xl:border-t-0 xl:border-l">
        <div className="grid grid-cols-3 gap-1">
          {([['grid', 'Grade', Grid3x3], ['auto', 'Automático', ScanSearch], ['manual', 'Manual', SquareDashedMousePointer]] as const).map(([m, label, Icon]) => (
            <Button key={m} size="sm" variant={mode === m ? 'secondary' : 'outline'} onClick={() => switchMode(m)}>
              <Icon /> {label}
            </Button>
          ))}
        </div>

        {mode === 'grid' && (
          <section className="space-y-2">
            <div className="flex items-center gap-1 text-xs">
              <Button size="xs" variant={by === 'count' ? 'secondary' : 'ghost'} onClick={() => setBy('count')}>Por colunas × linhas</Button>
              <Button size="xs" variant={by === 'size' ? 'secondary' : 'ghost'} onClick={() => setBy('size')}>Por tamanho</Button>
            </div>
            {by === 'count' ? (
              <div className="grid grid-cols-2 gap-2">
                <NumField label="Colunas" min={1} value={grid.columns} onChange={(n) => setG({ columns: n })} />
                <NumField label="Linhas" min={1} value={grid.rows} onChange={(n) => setG({ rows: n })} />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <NumField label="Largura da célula" min={1} value={grid.cellW ?? resolved?.cw} onChange={(n) => setG({ cellW: n })} />
                <NumField label="Altura da célula" min={1} value={grid.cellH ?? resolved?.ch} onChange={(n) => setG({ cellH: n })} />
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <NumField label="Margem X" value={grid.marginX} onChange={(n) => setG({ marginX: n })} title="Da borda esquerda da imagem até a 1ª célula" />
              <NumField label="Margem Y" value={grid.marginY} onChange={(n) => setG({ marginY: n })} title="Da borda de cima até a 1ª célula" />
              <NumField label="Espaço X" value={grid.spacingX} onChange={(n) => setG({ spacingX: n })} title="Vão entre colunas" />
              <NumField label="Espaço Y" value={grid.spacingY} onChange={(n) => setG({ spacingY: n })} title="Vão entre linhas" />
            </div>
            <Button size="sm" variant="outline" className="w-full" onClick={guessGrid}><Wand2 /> Detectar grade</Button>
          </section>
        )}

        {mode === 'auto' && (
          <section className="space-y-2">
            <p className="text-xs text-muted-foreground">Cada ilha de pixels não transparentes vira um recorte (ordem de leitura).</p>
            <div className="grid grid-cols-2 gap-2">
              <NumField label="Área mínima (px)" min={1} value={minArea} onChange={setMinArea} />
              <NumField label="Juntar vão ≤ (px)" value={mergeGap} onChange={setMergeGap} title="Junta partes separadas por até N pixels (ex.: um olho solto)" />
            </div>
            <Button size="sm" variant="outline" className="w-full" onClick={detectAuto}><ScanSearch /> Detectar sprites</Button>
          </section>
        )}

        {mode === 'manual' && (
          <section className="space-y-2">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{manual.length} retângulo(s)</span>
              <Button size="xs" variant="ghost" disabled={!manual.length} onClick={() => { setManual([]); setOn({}); setNames({}); setActive(null) }}>Limpar tudo</Button>
            </div>
            {manualRect && (
              <div className="space-y-2 rounded-md border p-2">
                <div className="grid grid-cols-4 gap-1">
                  {(['X', 'Y', 'L', 'A'] as const).map((l, k) => (
                    <NumField key={l} label={l} min={k > 1 ? 1 : 0} value={manualRect[k]} onChange={(n) => setManual((m) => m.map((r, i) => (i === active ? (r.map((v, j) => (j === k ? n : v)) as Rect) : r)))} />
                  ))}
                </div>
                <Button size="xs" variant="outline" onClick={removeActive}><Trash2 /> Remover</Button>
              </div>
            )}
          </section>
        )}

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs">Recortes ({nOn}/{cells.length})</Label>
            <div className="flex gap-1">
              <Button size="xs" variant="ghost" onClick={() => setOn(Object.fromEntries(cells.map((_, i) => [i, true])))}>Todos</Button>
              <Button size="xs" variant="ghost" onClick={() => setOn(Object.fromEntries(cells.map((_, i) => [i, false])))}>Nenhum</Button>
              <Button size="xs" variant="ghost" onClick={() => setOn(Object.fromEntries(cells.map((_, i) => [i, !isOn(i)])))}>Inverter</Button>
              <Button size="xs" variant="ghost" onClick={() => setOn({})} title="Desmarca as células vazias, marca o resto">Auto</Button>
            </div>
          </div>
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-md border p-1">
            {cells.slice(0, 400).map((r, i) => (
              <div key={i} className={cn('flex items-center gap-1.5 rounded px-1 py-0.5', hover === i && 'bg-accent')} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <Checkbox checked={isOn(i)} onCheckedChange={(v) => setOn((s) => ({ ...s, [i]: Boolean(v) }))} />
                <span className="w-7 text-right text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
                <Input
                  className="h-6 flex-1 px-1.5 text-xs"
                  placeholder={`${prefix || asset.slug}-${String(i + 1).padStart(2, '0')}`}
                  value={names[i] ?? ''}
                  onChange={(e) => setNames((s) => ({ ...s, [i]: e.target.value }))}
                />
                <span className="w-14 text-right text-[10px] tabular-nums text-muted-foreground">{r[2]}×{r[3]}</span>
              </div>
            ))}
            {cells.length === 0 && <p className="p-2 text-xs text-muted-foreground">Nenhuma célula. Ajuste a grade, detecte os sprites ou desenhe retângulos.</p>}
            {cells.length > 400 && <p className="p-1 text-[11px] text-muted-foreground">+{cells.length - 400} células (use Todos/Nenhum/Inverter)</p>}
          </div>
        </section>

        <section className="space-y-2">
          <Label className="text-xs">Resultado</Label>
          <label className="flex items-center gap-2 text-xs"><Switch size="sm" checked={makeSprites} onCheckedChange={setMakeSprites} /> Um asset por recorte</label>
          <label className="flex items-center gap-2 text-xs"><Switch size="sm" checked={makeAnim} onCheckedChange={setMakeAnim} /> Criar animação com os recortes</label>
          {makeAnim && (
            <div className="grid grid-cols-[96px_1fr] items-start gap-2">
              <div className={cn('flex size-24 items-center justify-center overflow-hidden rounded border', checker)}>
                <canvas ref={preview} className="max-h-full max-w-full" style={{ imageRendering: 'pixelated' }} />
              </div>
              <div className="space-y-1.5">
                <NumField label="FPS" min={1} value={fps} onChange={(n) => setFps(Math.min(60, n))} />
                <label className="flex items-center gap-2 text-xs"><Switch size="sm" checked={loop} onCheckedChange={setLoop} /> Repetir</label>
                <select className="h-7 w-full rounded-md border bg-transparent px-1 text-xs" value={anchor} onChange={(e) => setAnchor(e.target.value as typeof anchor)} title="Alinhamento de recortes de tamanhos diferentes">
                  <option value="center">Alinhar ao centro</option>
                  <option value="bottom">Alinhar pelos pés</option>
                  <option value="none">Sem alinhamento</option>
                </select>
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <label className="flex items-center gap-2 text-xs"><Switch size="sm" checked={trim} onCheckedChange={setTrim} /> Aparar ao conteúdo</label>
            <NumField label="Margem (px)" value={pad} onChange={(n) => setPad(Math.min(64, n))} />
          </div>
          <label className="grid gap-0.5 text-[11px] text-muted-foreground">
            Prefixo dos nomes
            <Input className="h-7 px-2 text-xs" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
          </label>
        </section>

        <Button className="w-full" disabled={!!busy || !nOn} onClick={run}>
          {busy ? <Loader2 className="animate-spin" /> : <Scissors />} {busy ?? `Recortar ${nOn} sprite(s)`}
        </Button>
        <p className="text-[11px] text-muted-foreground">
          <Square className="mr-1 inline size-3" /> Os pixels não são reamostrados. Os novos assets ficam na pasta da folha, em uma subpasta com o nome do resultado.
        </p>
      </aside>
    </div>
  )
}

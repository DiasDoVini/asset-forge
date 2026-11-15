// Peças de exibição do editor: menu Exibir, réguas com guias, painel da imagem de referência e prévia flutuante.
import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Minus, Pause, Play, Plus, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { playSequence, renderFrame, type Grid, type SpriteDoc } from './doc'
import type { Guides, Reference, Tiled, ViewState } from './viewState'

const Label = ({ children }: { children: React.ReactNode }) => <div className="px-2 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">{children}</div>

export function ViewMenu({ view, setView, doc, setGrid }: {
  view: ViewState
  setView: <K extends keyof ViewState>(k: K, v: ViewState[K]) => void
  doc: SpriteDoc
  setGrid: (g: Grid) => void
}) {
  const [gridDlg, setGridDlg] = useState(false)
  const tiled: [Tiled, string][] = [['none', 'Desligado'], ['x', 'Horizontal'], ['y', 'Vertical'], ['both', 'Nos dois eixos']]
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button size="sm" variant="ghost">Exibir <ChevronDown /></Button>} />
        <DropdownMenuContent align="start" className="w-60">
          <Label>Modo tiled (repetição)</Label>
          {tiled.map(([k, t]) => (
            <DropdownMenuItem key={k} onClick={() => setView('tiled', k)}>{view.tiled === k && <Check />} {t}</DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <Label>Grade ({doc.grid.w}×{doc.grid.h})</Label>
          <DropdownMenuItem onClick={() => setView('showGrid', !view.showGrid)}>{view.showGrid && <Check />} Mostrar grade</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setView('snap', !view.snap)}>{view.snap && <Check />} Encaixar na grade</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setGridDlg(true)}>Configurar grade…</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setView('rulers', !view.rulers)}>{view.rulers && <Check />} Réguas e guias</DropdownMenuItem>
          <DropdownMenuItem disabled={!view.guides.x.length && !view.guides.y.length} onClick={() => setView('guides', { x: [], y: [] })}>Remover todas as guias</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setView('refPanel', !view.refPanel)}>{view.refPanel && <Check />} Imagem de referência…</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setView('preview', { ...view.preview, open: !view.preview.open })}>{view.preview.open && <Check />} Prévia flutuante</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {gridDlg && <GridDialog grid={doc.grid} onClose={() => setGridDlg(false)} onApply={(g) => { setGrid(g); setView('showGrid', true); setGridDlg(false) }} />}
    </>
  )
}

function GridDialog({ grid, onClose, onApply }: { grid: Grid; onClose: () => void; onApply: (g: Grid) => void }) {
  const [g, setG] = useState(grid)
  const n = (v: string, min: number) => Math.max(min, Math.round(Number(v) || 0))
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xs">
        <DialogHeader>
          <DialogTitle>Grade do sprite</DialogTitle>
          <DialogDescription>Salva no .aseprite. Também define o tamanho padrão dos tiles.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <label className="grid gap-1">Largura<Input type="number" min={1} value={g.w} onChange={(e) => setG({ ...g, w: n(e.target.value, 1) })} /></label>
          <label className="grid gap-1">Altura<Input type="number" min={1} value={g.h} onChange={(e) => setG({ ...g, h: n(e.target.value, 1) })} /></label>
          <label className="grid gap-1">Deslocamento X<Input type="number" value={g.x} onChange={(e) => setG({ ...g, x: Math.round(Number(e.target.value) || 0) })} /></label>
          <label className="grid gap-1">Deslocamento Y<Input type="number" value={g.y} onChange={(e) => setG({ ...g, y: Math.round(Number(e.target.value) || 0) })} /></label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => onApply(g)}>Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Réguas no topo e à esquerda: arraste para criar uma guia; arraste uma guia de volta para a régua para apagar. */
export function Rulers({ z, ox, oy, w, h, guides, setGuides }: {
  z: number; ox: number; oy: number; w: number; h: number; guides: Guides; setGuides: (g: Guides) => void
}) {
  const [drag, setDrag] = useState<{ axis: 'x' | 'y'; index: number; pos: number } | null>(null)
  const top = useRef<HTMLCanvasElement>(null)
  const left = useRef<HTMLCanvasElement>(null)
  const R = 16

  useEffect(() => {
    for (const [ref, axis] of [[top, 'x'], [left, 'y']] as const) {
      const c = ref.current
      if (!c) continue
      const r = c.getBoundingClientRect()
      c.width = Math.max(1, Math.round(r.width))
      c.height = Math.max(1, Math.round(r.height))
      const g = c.getContext('2d')!
      g.fillStyle = '#262626'
      g.fillRect(0, 0, c.width, c.height)
      g.strokeStyle = '#666'
      g.fillStyle = '#999'
      g.font = '9px ui-monospace, monospace'
      const step = z >= 8 ? 1 : z >= 4 ? 2 : z >= 2 ? 8 : 16
      const label = step * (z * step >= 40 ? 1 : Math.ceil(40 / (z * step)))
      const len = axis === 'x' ? w : h
      const off = axis === 'x' ? ox - R : oy - R
      g.beginPath()
      for (let i = 0; i <= len; i += step) {
        const p = Math.round(off + i * z) + 0.5
        const major = i % label === 0
        if (axis === 'x') {
          g.moveTo(p, R)
          g.lineTo(p, major ? 4 : 11)
          if (major) g.fillText(String(i), p + 2, 9)
        } else {
          g.moveTo(R, p)
          g.lineTo(major ? 4 : 11, p)
          if (major) {
            g.save()
            g.translate(9, p + 2)
            g.rotate(Math.PI / 2)
            g.fillText(String(i), 0, 0)
            g.restore()
          }
        }
      }
      g.stroke()
      g.fillStyle = '#4dd0ff'
      for (const v of guides[axis]) {
        const p = off + v * z
        if (axis === 'x') g.fillRect(p - 1, 0, 3, R)
        else g.fillRect(0, p - 1, R, 3)
      }
    }
  }, [z, ox, oy, w, h, guides])

  const begin = (axis: 'x' | 'y') => (e: React.PointerEvent) => {
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    const r = (e.target as HTMLElement).getBoundingClientRect()
    const local = axis === 'x' ? e.clientX - r.left + R : e.clientY - r.top + R
    const v = Math.round((local - (axis === 'x' ? ox : oy)) / z)
    const near = guides[axis].findIndex((g) => Math.abs(g - v) * z <= 4)
    setDrag({ axis, index: near, pos: v })
  }
  const move = (e: React.PointerEvent) => {
    if (!drag) return
    const host = (e.currentTarget as HTMLElement).parentElement!.getBoundingClientRect()
    const local = drag.axis === 'x' ? e.clientX - host.left : e.clientY - host.top
    setDrag({ ...drag, pos: Math.round((local - (drag.axis === 'x' ? ox : oy)) / z) })
  }
  const end = (e: React.PointerEvent) => {
    if (!drag) return
    const host = (e.currentTarget as HTMLElement).parentElement!.getBoundingClientRect()
    const across = drag.axis === 'x' ? e.clientY - host.top : e.clientX - host.left
    const list = [...guides[drag.axis]]
    if (drag.index >= 0) list.splice(drag.index, 1)
    if (across > R && drag.pos >= 0 && drag.pos <= (drag.axis === 'x' ? w : h)) list.push(drag.pos) // soltou no canvas: fica
    setGuides({ ...guides, [drag.axis]: list })
    setDrag(null)
  }
  return (
    <>
      <canvas ref={top} className="absolute top-0 right-0 left-4 z-10 h-4 cursor-s-resize" onPointerDown={begin('x')} onPointerMove={move} onPointerUp={end} title="Arraste para criar uma guia vertical" />
      <canvas ref={left} className="absolute top-4 bottom-0 left-0 z-10 w-4 cursor-e-resize" onPointerDown={begin('y')} onPointerMove={move} onPointerUp={end} title="Arraste para criar uma guia horizontal" />
      <div className="absolute top-0 left-0 z-10 size-4 bg-[#262626]" />
      {drag && (
        <div
          className="pointer-events-none absolute z-10 border-sky-400"
          style={drag.axis === 'x'
            ? { left: ox + drag.pos * z, top: 0, bottom: 0, borderLeftWidth: 1, borderStyle: 'dashed' }
            : { top: oy + drag.pos * z, left: 0, right: 0, borderTopWidth: 1, borderStyle: 'dashed' }}
        />
      )}
    </>
  )
}

/** Painel da imagem de referência (não entra no sprite). */
export function ReferencePanel({ refImg, setRef, onClose, projectImages }: {
  refImg: Reference | null
  setRef: (r: Reference | null) => void
  onClose: () => void
  projectImages: { name: string; url: string }[]
}) {
  const load = (src: string, name: string) => setRef({ src, name, x: 0, y: 0, scale: 1, opacity: 0.5, above: false, visible: true, ...(refImg ? { x: refImg.x, y: refImg.y, opacity: refImg.opacity, above: refImg.above } : {}) })
  const pick = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = 'image/*'
    inp.onchange = () => {
      const f = inp.files?.[0]
      if (!f) return
      const rd = new FileReader()
      rd.onload = () => {
        if (typeof rd.result === 'string') load(rd.result, f.name)
        if (f.size > 3_000_000) toast.info('Imagem grande: ela vale nesta sessão, mas pode não ser lembrada depois')
      }
      rd.readAsDataURL(f)
    }
    inp.click()
  }
  const num = (k: 'x' | 'y' | 'scale' | 'opacity', v: string) => refImg && setRef({ ...refImg, [k]: Number(v) || 0 })
  return (
    <div className="absolute top-2 left-2 z-20 w-64 space-y-2 rounded-md border bg-popover p-2 text-xs shadow-lg">
      <div className="flex items-center">
        <span className="font-medium">Imagem de referência</span>
        <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={onClose}><X /></Button>
      </div>
      <div className="flex flex-wrap gap-1">
        <Button size="xs" variant="outline" onClick={pick}>Abrir arquivo…</Button>
        {projectImages.length > 0 && (
          <select className="h-6 max-w-36 rounded border bg-transparent px-1" value="" onChange={(e) => {
            const im = projectImages.find((p) => p.url === e.target.value)
            if (im) load(im.url, im.name)
          }}>
            <option value="">Asset do projeto…</option>
            {projectImages.map((p) => <option key={p.url} value={p.url}>{p.name}</option>)}
          </select>
        )}
        {refImg && <Button size="xs" variant="ghost" className="text-destructive" onClick={() => setRef(null)}>Remover</Button>}
      </div>
      {refImg && (
        <>
          <div className="truncate text-muted-foreground" title={refImg.name}>{refImg.name}</div>
          <div className="grid grid-cols-2 gap-1.5">
            <label className="grid gap-0.5 text-muted-foreground">X<Input type="number" className="h-6 px-1 text-xs" value={refImg.x} onChange={(e) => num('x', e.target.value)} /></label>
            <label className="grid gap-0.5 text-muted-foreground">Y<Input type="number" className="h-6 px-1 text-xs" value={refImg.y} onChange={(e) => num('y', e.target.value)} /></label>
            <label className="grid gap-0.5 text-muted-foreground">Escala<Input type="number" step={0.05} min={0.05} className="h-6 px-1 text-xs" value={refImg.scale} onChange={(e) => num('scale', e.target.value)} /></label>
            <label className="grid gap-0.5 text-muted-foreground">Opacidade
              <input type="range" min={0.05} max={1} step={0.05} value={refImg.opacity} onChange={(e) => num('opacity', e.target.value)} />
            </label>
          </div>
          <div className="flex gap-3">
            <label className="flex items-center gap-1"><input type="checkbox" checked={refImg.visible} onChange={(e) => setRef({ ...refImg, visible: e.target.checked })} /> Visível</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={refImg.above} onChange={(e) => setRef({ ...refImg, above: e.target.checked })} /> Por cima do sprite</label>
          </div>
          <p className="text-[11px] text-muted-foreground">Alt+arrastar no canvas move a referência.</p>
        </>
      )}
    </div>
  )
}

/** Prévia flutuante: o quadro atual (ou a animação tocando) em zoom próprio, arrastável. */
export function PreviewWindow({ doc, frame, tiled, state, setState, loopTag }: {
  doc: SpriteDoc; frame: number; tiled: Tiled
  state: ViewState['preview']; setState: (p: ViewState['preview']) => void
  loopTag: number | null
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [f, setF] = useState(frame)
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  useEffect(() => {
    if (!state.playing) return
    const seq = playSequence(doc, loopTag !== null ? doc.tags[loopTag] ?? null : null)
    let i = 0
    let t: number
    const step = () => {
      i = (i + 1) % seq.length
      setF(seq[i])
      t = window.setTimeout(step, doc.frames[seq[i]]?.duration ?? 100)
    }
    t = window.setTimeout(step, doc.frames[seq[0]]?.duration ?? 100)
    return () => window.clearTimeout(t)
  }, [state.playing, doc, loopTag])
  const shownFrame = Math.min(state.playing ? f : frame, doc.frames.length - 1)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const reps = { none: [1, 1], x: [3, 1], y: [1, 3], both: [3, 3] }[tiled]
    c.width = doc.w * state.zoom * reps[0]
    c.height = doc.h * state.zoom * reps[1]
    const g = c.getContext('2d')!
    g.imageSmoothingEnabled = false
    const tmp = document.createElement('canvas')
    tmp.width = doc.w
    tmp.height = doc.h
    tmp.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(renderFrame(doc, shownFrame)), doc.w, doc.h), 0, 0)
    for (let y = 0; y < reps[1]; y++) for (let x = 0; x < reps[0]; x++) g.drawImage(tmp, x * doc.w * state.zoom, y * doc.h * state.zoom, doc.w * state.zoom, doc.h * state.zoom)
  }, [doc, shownFrame, state.zoom, tiled])
  return (
    <div className="absolute z-20 overflow-hidden rounded-md border bg-popover shadow-lg" style={{ left: state.x, top: state.y }}>
      <div
        className="flex cursor-move items-center gap-0.5 border-b px-1 py-0.5 text-[11px]"
        onPointerDown={(e) => {
          ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
          drag.current = { x: e.clientX, y: e.clientY, px: state.x, py: state.y }
        }}
        onPointerMove={(e) => drag.current && setState({ ...state, x: Math.max(0, drag.current.px + e.clientX - drag.current.x), y: Math.max(0, drag.current.py + e.clientY - drag.current.y) })}
        onPointerUp={() => (drag.current = null)}
      >
        <span className="mr-auto font-medium">Prévia</span>
        <Button size="icon-xs" variant="ghost" onClick={() => setState({ ...state, playing: !state.playing })}>{state.playing ? <Pause /> : <Play />}</Button>
        <Button size="icon-xs" variant="ghost" onClick={() => setState({ ...state, zoom: Math.max(1, state.zoom - 1) })}><Minus /></Button>
        <span className="w-6 text-center tabular-nums">{state.zoom}×</span>
        <Button size="icon-xs" variant="ghost" onClick={() => setState({ ...state, zoom: Math.min(16, state.zoom + 1) })}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" onClick={() => setState({ ...state, open: false })}><X /></Button>
      </div>
      <div className="max-h-[50vh] max-w-[40vw] overflow-auto bg-[repeating-conic-gradient(#2b2b2b_0_25%,#383838_0_50%)] bg-[length:16px_16px]">
        <canvas ref={ref} style={{ imageRendering: 'pixelated', display: 'block' }} />
      </div>
    </div>
  )
}

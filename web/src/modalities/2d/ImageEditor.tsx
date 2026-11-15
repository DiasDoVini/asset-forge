// Editor leve de imagem/sprite: lápis, borracha, conta-gotas, espelhar/girar e pintura de
// máscara para "editar região com IA" (2d.edit com mask). Salvar cria nova versão do asset.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { Brush, Eraser, FlipHorizontal2, FlipVertical2, Pipette, Redo2, RotateCw, Save, Sparkles, SquareDashedMousePointer, Undo2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, primaryFile } from '@/core/api'
import type { PanelProps } from '@/core/registry'
import { checker } from '@/core/components/ImageViewer'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'

type Tool = 'pencil' | 'eraser' | 'picker' | 'mask'

export type ImageEditorProps = PanelProps & {
  /** Edita outra imagem (ex.: um frame de animação) em vez do arquivo primário do asset. */
  sourceUrl?: string
  pixel?: boolean
  /** Substitui o "Salvar versão" padrão (upload como nova versão do asset). */
  onSaveBlob?: (blob: Blob) => Promise<void>
  hideAi?: boolean
}

export function ImageEditor({ asset, project, sourceUrl, pixel, onSaveBlob, hideAi }: ImageEditorProps) {
  const primary = asset.files.find((f) => f.role === 'primary' && f.mime.startsWith('image/')) ?? primaryFile(asset)!
  const file = sourceUrl ? { ...primary, url: sourceUrl } : primary
  const canvas = useRef<HTMLCanvasElement>(null)
  const mask = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [tool, setTool] = useState<Tool>('pencil')
  const [color, setColor] = useState('#ffffff')
  const [brush, setBrush] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [dirty, setDirty] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const undo = useRef<ImageData[]>([])
  const redo = useRef<ImageData[]>([])
  const drawing = useRef(false)
  const last = useRef<{ x: number; y: number } | null>(null)
  const qc = useQueryClient()
  const nav = useNavigate()
  const isPixel = pixel ?? (asset.kind === 'sprite' || Math.max(size.w, size.h) <= 256)

  // carrega a imagem
  useEffect(() => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const c = canvas.current!
      c.width = img.naturalWidth
      c.height = img.naturalHeight
      mask.current!.width = img.naturalWidth
      mask.current!.height = img.naturalHeight
      c.getContext('2d')!.drawImage(img, 0, 0)
      setSize({ w: img.naturalWidth, h: img.naturalHeight })
      undo.current = []
      redo.current = []
      setDirty(false)
      const box = wrap.current!.getBoundingClientRect()
      const k = Math.min((box.width - 48) / img.naturalWidth, (box.height - 48) / img.naturalHeight)
      const px = pixel ?? (asset.kind === 'sprite' || Math.max(img.naturalWidth, img.naturalHeight) <= 256)
      setZoom(px ? Math.max(1, Math.floor(k)) : Math.min(1, k))
      setBrush(px ? 1 : 24)
    }
    img.src = file.url
  }, [file.url, asset.kind, pixel])

  const ctx = () => canvas.current!.getContext('2d', { willReadFrequently: true })!
  const snapshot = () => {
    undo.current.push(ctx().getImageData(0, 0, size.w, size.h))
    if (undo.current.length > 30) undo.current.shift()
    redo.current = []
  }

  const toImage = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect()
    return { x: Math.floor(((e.clientX - r.left) / r.width) * size.w), y: Math.floor(((e.clientY - r.top) / r.height) * size.h) }
  }

  const stamp = useCallback(
    (x: number, y: number) => {
      if (tool === 'mask') {
        const m = mask.current!.getContext('2d')!
        m.fillStyle = 'rgba(255, 60, 90, 1)'
        m.beginPath()
        m.arc(x + 0.5, y + 0.5, Math.max(1, brush / 2), 0, Math.PI * 2)
        m.fill()
        return
      }
      const c = ctx()
      const half = Math.floor(brush / 2)
      if (tool === 'eraser') c.clearRect(x - half, y - half, brush, brush)
      else {
        c.fillStyle = color
        if (isPixel || brush <= 2) c.fillRect(x - half, y - half, brush, brush)
        else {
          c.beginPath()
          c.arc(x + 0.5, y + 0.5, brush / 2, 0, Math.PI * 2)
          c.fill()
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tool, brush, color, isPixel, size],
  )

  const line = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const steps = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y), 1)
    for (let i = 0; i <= steps; i++) stamp(Math.round(a.x + ((b.x - a.x) * i) / steps), Math.round(a.y + ((b.y - a.y) * i) / steps))
  }

  const onDown = (e: React.PointerEvent) => {
    const p = toImage(e)
    if (tool === 'picker') {
      const d = ctx().getImageData(p.x, p.y, 1, 1).data
      setColor('#' + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0')).join(''))
      setTool('pencil')
      return
    }
    if (tool !== 'mask') snapshot()
    ;(e.target as Element).setPointerCapture(e.pointerId)
    drawing.current = true
    last.current = p
    stamp(p.x, p.y)
    if (tool !== 'mask') setDirty(true)
  }
  const onMove = (e: React.PointerEvent) => {
    if (!drawing.current) return
    const p = toImage(e)
    line(last.current!, p)
    last.current = p
  }
  const onUp = () => {
    drawing.current = false
    last.current = null
  }

  const transform = (kind: 'flipH' | 'flipV' | 'rot') => {
    snapshot()
    const c = canvas.current!
    const src = document.createElement('canvas')
    src.width = c.width
    src.height = c.height
    src.getContext('2d')!.drawImage(c, 0, 0)
    if (kind === 'rot') {
      c.width = src.height
      c.height = src.width
      mask.current!.width = c.width
      mask.current!.height = c.height
      setSize({ w: c.width, h: c.height })
    }
    const g = c.getContext('2d')!
    g.save()
    g.clearRect(0, 0, c.width, c.height)
    if (kind === 'flipH') g.setTransform(-1, 0, 0, 1, c.width, 0)
    if (kind === 'flipV') g.setTransform(1, 0, 0, -1, 0, c.height)
    if (kind === 'rot') g.setTransform(0, 1, -1, 0, c.width, 0)
    g.drawImage(src, 0, 0)
    g.restore()
    setDirty(true)
  }

  const doUndo = (from: typeof undo, to: typeof redo) => {
    const d = from.current.pop()
    if (!d) return
    to.current.push(ctx().getImageData(0, 0, size.w, size.h))
    const c = canvas.current!
    if (c.width !== d.width || c.height !== d.height) {
      c.width = d.width
      c.height = d.height
      setSize({ w: d.width, h: d.height })
    }
    ctx().putImageData(d, 0, 0)
    setDirty(true)
  }

  const blob = (c: HTMLCanvasElement) => new Promise<Blob>((res) => c.toBlob((b) => res(b!), 'image/png'))

  const save = async () => {
    setBusy(true)
    try {
      if (onSaveBlob) {
        await onSaveBlob(await blob(canvas.current!))
        setDirty(false)
        return
      }
      const v = await api.uploadAsset(project.slug, await blob(canvas.current!), {
        asset: asset.slug,
        parent: asset.ref,
        modality: asset.modality,
        kind: asset.kind,
        name: asset.name,
        filename: file.path.split('/').pop(),
      })
      qc.invalidateQueries({ queryKey: ['versions', project.slug, asset.slug] })
      toast.success(`Salvo como ${v.ref}`)
      nav(`/p/${project.slug}/a/${v.slug}/${v.version}`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const hasMask = () => {
    const d = mask.current!.getContext('2d')!.getImageData(0, 0, size.w, size.h).data
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return true
    return false
  }

  const aiEdit = async () => {
    if (!instruction.trim()) return
    setBusy(true)
    try {
      let params: Record<string, unknown> = { input_asset: asset.ref, instruction }
      if (dirty) {
        // edições manuais pendentes viram uma versão antes da edição com IA
        const v = await api.uploadAsset(project.slug, await blob(canvas.current!), { asset: asset.slug, parent: asset.ref, modality: asset.modality, kind: asset.kind })
        params.input_asset = v.ref
      }
      if (hasMask()) {
        // máscara binária branco/preto na resolução da imagem
        const m = document.createElement('canvas')
        m.width = size.w
        m.height = size.h
        const g = m.getContext('2d')!
        g.fillStyle = '#000'
        g.fillRect(0, 0, size.w, size.h)
        const src = mask.current!.getContext('2d')!.getImageData(0, 0, size.w, size.h)
        const out = g.getImageData(0, 0, size.w, size.h)
        for (let i = 0; i < src.data.length; i += 4) if (src.data[i + 3] > 0) out.data[i] = out.data[i + 1] = out.data[i + 2] = 255
        g.putImageData(out, 0, 0)
        const up = await api.uploadTemp(project.slug, await blob(m), 'mask.png')
        params = { ...params, mask: up.path }
      }
      const job = await api.submitJob({ project: project.slug, pipeline: '2d.edit', params })
      toast.success(`Edição com IA na fila (${job.id})`, { description: 'A nova versão aparece no histórico ao terminar.' })
      setInstruction('')
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const tools: { id: Tool; icon: React.ReactNode; label: string }[] = [
    { id: 'pencil', icon: <Brush />, label: 'Lápis (B)' },
    { id: 'eraser', icon: <Eraser />, label: 'Borracha (E)' },
    { id: 'picker', icon: <Pipette />, label: 'Conta-gotas (I)' },
    { id: 'mask', icon: <SquareDashedMousePointer />, label: 'Máscara p/ IA (M)' },
  ]

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') return
      const map: Record<string, Tool> = { b: 'pencil', e: 'eraser', i: 'picker', m: 'mask' }
      if (map[e.key]) setTool(map[e.key])
      if (e.ctrlKey && e.key === 'z') doUndo(undo, redo)
      if (e.ctrlKey && e.key === 'y') doUndo(redo, undo)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5">
        {tools.map((t) => (
          <Button key={t.id} size="icon-sm" variant={tool === t.id ? 'secondary' : 'ghost'} title={t.label} onClick={() => setTool(t.id)}>{t.icon}</Button>
        ))}
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="mx-1 size-7 cursor-pointer rounded border bg-transparent" title="Cor" />
        <div className="flex w-36 items-center gap-2 px-2 text-xs text-muted-foreground">
          <span className="w-8 tabular-nums">{brush}px</span>
          <Slider min={1} max={isPixel ? 8 : 128} value={[brush]} onValueChange={(v) => setBrush(Array.isArray(v) ? v[0] : v)} />
        </div>
        <span className="mx-1 h-5 w-px bg-border" />
        <Button size="icon-sm" variant="ghost" title="Espelhar horizontal" onClick={() => transform('flipH')}><FlipHorizontal2 /></Button>
        <Button size="icon-sm" variant="ghost" title="Espelhar vertical" onClick={() => transform('flipV')}><FlipVertical2 /></Button>
        <Button size="icon-sm" variant="ghost" title="Girar 90°" onClick={() => transform('rot')}><RotateCw /></Button>
        <Button size="icon-sm" variant="ghost" title="Desfazer (Ctrl+Z)" onClick={() => doUndo(undo, redo)}><Undo2 /></Button>
        <Button size="icon-sm" variant="ghost" title="Refazer (Ctrl+Y)" onClick={() => doUndo(redo, undo)}><Redo2 /></Button>
        <Button size="sm" variant="ghost" onClick={() => mask.current!.getContext('2d')!.clearRect(0, 0, size.w, size.h)}>Limpar máscara</Button>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {size.w}×{size.h}
          <select className="rounded border bg-transparent px-1 py-0.5" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
            {[...new Set([zoom, 0.25, 0.5, 1, 2, 4, 8, 16, 24])].sort((a, b) => a - b).map((z) => <option key={z} value={z}>{Math.round(z * 100)}%</option>)}
          </select>
          <Button size="sm" onClick={save} disabled={!dirty || busy}><Save /> Salvar versão</Button>
        </div>
      </div>

      <div ref={wrap} className={cn('min-h-0 flex-1 overflow-auto', checker)}>
        <div className="flex min-h-full min-w-full items-center justify-center p-6">
          <div className="relative" style={{ width: size.w * zoom, height: size.h * zoom }}>
            <canvas ref={canvas} className="absolute inset-0 size-full" style={{ imageRendering: isPixel || zoom >= 2 ? 'pixelated' : 'auto' }} />
            <canvas
              ref={mask}
              className={cn('absolute inset-0 size-full opacity-45', tool === 'picker' ? 'cursor-crosshair' : 'cursor-cell')}
              style={{ imageRendering: 'pixelated' }}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerLeave={onUp}
            />
          </div>
        </div>
      </div>

      {!hideAi && <form
        className="flex items-center gap-2 border-t p-2"
        onSubmit={(e) => {
          e.preventDefault()
          aiEdit()
        }}
      >
        <Sparkles className="size-4 shrink-0 text-amber-500" />
        <Input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Editar com IA: “troque a lâmina por fogo”. Pinte uma máscara (M) para limitar a região." />
        <Button type="submit" size="sm" disabled={busy || !instruction.trim()}>Editar com IA</Button>
      </form>}
    </div>
  )
}

// Viewer do sprite.png (tamanho exato) ampliado, com overlay da spec (área segura e pivô) e grade.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Crosshair, Grid3x3, Minus, Plus, Scan } from 'lucide-react'
import type { PixelSpec } from '@/core/api'
import type { ViewerProps } from '@/core/registry'
import { Button } from '@/components/ui/button'
import { drawBorder, drawChecker, drawGridLines, drawImageSource, drawSpecOverlay, SLOT_DARK, SLOT_LIGHT } from './draw'
import { cn } from '@/lib/utils'

export function PixelSpriteViewer({ url, asset }: ViewerProps) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [img, setImg] = useState<HTMLImageElement | null>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const [zoom, setZoom] = useState<number | null>(null)
  const [grid, setGrid] = useState(true)
  const [overlay, setOverlay] = useState(true)
  const spec = asset.extra?.spec as PixelSpec | undefined

  useEffect(() => {
    const i = new Image()
    i.onload = () => setImg(i)
    i.src = url
    setZoom(null)
  }, [url])

  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const W = img?.naturalWidth ?? 0, H = img?.naturalHeight ?? 0
  const fit = W && box.w ? Math.max(1, Math.min(40, Math.floor(Math.min((box.w - 32) / W, (box.h - 32) / H)))) : 1
  const z = zoom ?? fit

  useEffect(() => {
    const c = canvas.current
    if (!c || !img || !box.w) return
    c.width = box.w
    c.height = box.h
    const ctx = c.getContext('2d')!
    const v = { z, ox: Math.round((box.w - W * z) / 2), oy: Math.round((box.h - H * z) / 2) }
    ctx.clearRect(0, 0, c.width, c.height)
    drawChecker(ctx, W, H, v)
    drawImageSource(ctx, img, W, H, v)
    if (grid && z >= 4) drawGridLines(ctx, W, H, v)
    drawBorder(ctx, W, H, v)
    if (overlay && spec) drawSpecOverlay(ctx, spec, H, v, { safe: true, pivot: true })
  }, [img, box, z, grid, overlay, spec, W, H])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={wrap} className="relative min-h-0 flex-1 overflow-hidden bg-[#141418]">
        <canvas ref={canvas} className="absolute inset-0" />
        {img && (
          <div className="absolute right-2 bottom-2 flex gap-1.5">
            {[SLOT_DARK, SLOT_LIGHT].map((bg) => (
              <div key={bg} className="flex items-end gap-1.5 rounded p-1.5" style={{ background: bg }}>
                <img src={url} alt="" style={{ width: W, height: H, imageRendering: 'pixelated' }} />
                <img src={url} alt="" style={{ width: W * 2, height: H * 2, imageRendering: 'pixelated' }} />
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 border-t px-2 py-1 text-xs text-muted-foreground">
        {img && <span>{W}×{H}px</span>}
        {spec && <span className="ml-2 font-mono">{spec.name} · pivô ({spec.pivot.join(', ')})</span>}
        <div className="ml-auto flex items-center gap-1">
          <Button size="icon-xs" variant={grid ? 'secondary' : 'ghost'} onClick={() => setGrid(!grid)} title="Grade"><Grid3x3 /></Button>
          <Button size="icon-xs" variant={overlay ? 'secondary' : 'ghost'} onClick={() => setOverlay(!overlay)} title="Pivô e área segura" disabled={!spec}><Crosshair /></Button>
          <span className={cn('w-8 text-center tabular-nums')}>{z}x</span>
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(Math.max(1, z - 1))}><Minus /></Button>
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(Math.min(40, z + 1))}><Plus /></Button>
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(null)} title="Ajustar"><Scan /></Button>
        </div>
      </div>
    </div>
  )
}

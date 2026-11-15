import { useEffect, useRef, useState } from 'react'
import { Grid3x3, Minus, Plus, Scan } from 'lucide-react'
import type { ViewerProps } from '../registry'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const checker =
  'bg-[length:16px_16px] bg-[linear-gradient(45deg,#2a2a2a_25%,transparent_25%),linear-gradient(-45deg,#2a2a2a_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#2a2a2a_75%),linear-gradient(-45deg,transparent_75%,#2a2a2a_75%)] bg-[position:0_0,0_8px,8px_-8px,-8px_0] bg-[#1f1f1f]'

/** Visualizador de imagem com zoom, fundo xadrez e modo pixelado automático para sprites. */
export function ImageViewer({ url, asset }: ViewerProps) {
  const box = useRef<HTMLDivElement>(null)
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null)
  const [zoom, setZoom] = useState<number | null>(null) // null = ajustar
  const [grid, setGrid] = useState(false)
  const pixel = asset.kind === 'sprite' || (nat !== null && Math.max(nat.w, nat.h) <= 256)

  useEffect(() => setZoom(null), [url])

  const fit = (() => {
    if (!nat || !box.current) return 1
    const r = box.current.getBoundingClientRect()
    const k = Math.min((r.width - 32) / nat.w, (r.height - 32) / nat.h)
    return pixel ? Math.max(1, Math.floor(k)) : Math.min(k, 1)
  })()
  const z = zoom ?? fit

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div ref={box} className={cn('relative min-h-0 flex-1 overflow-auto', checker)}>
        <div className="flex min-h-full min-w-full items-center justify-center p-4">
          <div className="relative" style={nat ? { width: nat.w * z, height: nat.h * z } : undefined}>
            <img
              src={url}
              alt={asset.name}
              draggable={false}
              onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
              className="size-full select-none"
              style={{ imageRendering: pixel ? 'pixelated' : 'auto' }}
            />
            {grid && pixel && z >= 4 && (
              <div
                className="pointer-events-none absolute inset-0"
                style={{
                  backgroundImage: 'linear-gradient(to right, rgba(255,255,255,.12) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,.12) 1px, transparent 1px)',
                  backgroundSize: `${z}px ${z}px`,
                }}
              />
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1 border-t px-2 py-1 text-xs text-muted-foreground">
        {nat && <span>{nat.w}×{nat.h}px</span>}
        <span className="ml-2">{Math.round(z * 100)}%</span>
        <div className="ml-auto flex items-center gap-1">
          {pixel && (
            <Button size="icon-xs" variant={grid ? 'secondary' : 'ghost'} onClick={() => setGrid(!grid)} title="Grade de pixels">
              <Grid3x3 />
            </Button>
          )}
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(Math.max(0.1, pixel ? Math.max(1, z - 1) : z / 1.25))}><Minus /></Button>
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(pixel ? Math.floor(z) + 1 : z * 1.25)}><Plus /></Button>
          <Button size="icon-xs" variant="ghost" onClick={() => setZoom(null)} title="Ajustar"><Scan /></Button>
        </div>
      </div>
    </div>
  )
}

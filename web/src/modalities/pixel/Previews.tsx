// Prévias em tamanho real (1x e 2x) sobre fundos escuro e claro de "slot".
import { useEffect, useRef } from 'react'
import { gridCanvas, SLOT_DARK, SLOT_LIGHT } from './draw'
import type { Grid } from './render'

export function SpritePreview({ grid, scale, bg }: { grid: Grid; scale: number; bg: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    c.width = grid.w * scale
    c.height = grid.h * scale
    const ctx = c.getContext('2d')!
    ctx.imageSmoothingEnabled = false
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.drawImage(gridCanvas(grid), 0, 0, c.width, c.height)
  }, [grid, scale])
  return (
    <div className="flex items-center justify-center rounded p-2" style={{ background: bg }}>
      <canvas ref={ref} style={{ width: grid.w * scale, height: grid.h * scale, imageRendering: 'pixelated' }} />
    </div>
  )
}

export function PreviewStrip({ grid }: { grid: Grid }) {
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {[SLOT_DARK, SLOT_LIGHT].map((bg) => (
        <div key={bg} className="flex flex-col gap-1.5">
          <SpritePreview grid={grid} scale={1} bg={bg} />
          <SpritePreview grid={grid} scale={2} bg={bg} />
        </div>
      ))}
    </div>
  )
}

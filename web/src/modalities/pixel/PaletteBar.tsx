// Paleta do Pixel Studio: só cores permitidas (core ∪ grupo). Cada amostra mostra seu caractere na legenda.
import type { PixelPalette } from '@/core/api'
import { cn } from '@/lib/utils'
import { intToHex, rgbOf, type Legend } from './render'

const lum = (v: number) => {
  const [r, g, b] = rgbOf(v)
  return 0.299 * r + 0.587 * g + 0.114 * b
}

export function PaletteBar({ palette, group, onGroup, allowed, legend, color, onColor, offPalette, coreCount }: {
  palette: PixelPalette
  group: string | null
  onGroup: (g: string | null) => void
  allowed: number[]
  legend: Legend
  color: number
  onColor: (c: number) => void
  offPalette: number[]
  coreCount: number
}) {
  const groups = Object.keys(palette.groups)
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">Grupo</span>
        <select className="h-7 min-w-0 flex-1 rounded-md border bg-transparent px-1 text-xs" value={group ?? ''} onChange={(e) => onGroup(e.target.value || null)}>
          <option value="">(só core)</option>
          {groups.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(30px,1fr))] gap-1">
        {allowed.map((c, i) => {
          const ch = legend.charOf.get(c) ?? '?'
          return (
            <button
              key={c}
              type="button"
              onClick={() => onColor(c)}
              title={`${intToHex(c)} · '${ch}'${i < 9 ? ` · tecla ${i + 1}` : ''}${i >= coreCount ? ' · grupo' : ' · core'}`}
              className={cn(
                'relative flex aspect-square items-center justify-center rounded-sm border font-mono text-[11px] font-bold',
                color === c ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : 'hover:scale-105',
                i === coreCount && coreCount > 0 && 'col-start-1',
              )}
              style={{ background: intToHex(c), color: lum(c) > 140 ? '#000' : '#fff' }}
            >
              {ch}
              {i < 9 && <span className="absolute top-0 left-0.5 text-[8px] font-normal opacity-60">{i + 1}</span>}
            </button>
          )
        })}
      </div>
      {offPalette.length > 0 && (
        <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-1.5 text-[11px] text-amber-500">
          Cores fora da paleta no desenho (não podem ser usadas para pintar):
          <div className="flex flex-wrap gap-1">
            {offPalette.map((c) => (
              <span key={c} className="flex size-6 items-center justify-center rounded-sm border font-mono text-[10px]" title={intToHex(c)} style={{ background: intToHex(c), color: lum(c) > 140 ? '#000' : '#fff' }}>
                {legend.charOf.get(c) ?? '?'}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

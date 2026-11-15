// Vista "Mapa": linhas + legenda em texto monoespaçado, editável nos dois sentidos.
// Texto inválido mostra o erro (linha/coluna) e não sobrescreve o desenho.
import { useEffect, useRef, useState } from 'react'
import { Textarea } from '@/components/ui/textarea'
import { gridsEqual, gridToText, intToHex, textToGrid, type Grid, type Legend } from './render'

export function MapTextView({ grid, legend, allowed, tolerated, onApply }: {
  grid: Grid
  legend: Legend
  allowed: number[]
  tolerated: Set<number>
  onApply: (g: Grid) => void
}) {
  const [text, setText] = useState(() => gridToText(grid, legend))
  const [error, setError] = useState<string | null>(null)
  const applied = useRef<Grid | null>(null)

  // desenho mudou por fora (canvas, desfazer...): regenera o texto
  useEffect(() => {
    if (applied.current === grid) return
    setText(gridToText(grid, legend))
    setError(null)
  }, [grid, legend])

  const onChange = (t: string) => {
    setText(t)
    const r = textToGrid(t, grid.w, grid.h, legend, new Set(allowed), tolerated)
    if (!r.ok) {
      setError(`Texto linha ${r.line}${r.row ? ` (linha do mapa ${r.row}${r.col ? `, coluna ${r.col}` : ''})` : ''}: ${r.error}`)
      return
    }
    setError(null)
    if (gridsEqual(r.grid, grid)) return
    applied.current = r.grid
    onApply(r.grid)
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <Textarea
        value={text}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        wrap="off"
        className="min-h-[240px] flex-1 resize-none font-mono text-sm leading-[1.15] tracking-[0.15em]"
      />
      {error ? (
        <p className="rounded bg-red-950/70 p-1.5 text-xs text-red-200">{error} — o desenho não foi alterado.</p>
      ) : (
        <p className="text-xs text-emerald-400">mapa válido ✓ ({grid.w}×{grid.h})</p>
      )}
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">Caracteres da paleta</summary>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono">
          {allowed.map((c) => (
            <span key={c} className="flex items-center gap-1">
              <span className="inline-block size-3 rounded-sm border" style={{ background: intToHex(c) }} />
              {legend.charOf.get(c)} {intToHex(c)}
            </span>
          ))}
        </div>
        <p className="mt-1">"." = transparente. Linhas com "c #rrggbb" definem a legenda; "//" inicia comentário. Caracteres da paleta valem mesmo sem estarem na legenda.</p>
      </details>
    </div>
  )
}

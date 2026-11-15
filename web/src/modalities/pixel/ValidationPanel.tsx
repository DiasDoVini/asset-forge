// Lista de checagens dos validadores (✓/✗), eliminatórias em destaque. Clicar destaca pixels no canvas.
import { Loader2 } from 'lucide-react'
import type { PixelCheck, PixelReport } from '@/core/api'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { extractHighlight } from './render'

export function ReportBadge({ report }: { report: PixelReport }) {
  return report.ok ? (
    <Badge className="bg-emerald-600 text-white">Passa nos validadores</Badge>
  ) : (
    <Badge variant="destructive">Reprovado</Badge>
  )
}

const hasLocation = (c: PixelCheck) => {
  const h = extractHighlight(c.data)
  return h.pixels.length > 0 || h.rects.length > 0
}

export function ValidationPanel({ report, error, loading, selected, onSelect, className }: {
  report: PixelReport | null | undefined
  error?: string | null
  loading?: boolean
  selected?: string | null
  onSelect?: (c: PixelCheck | null) => void
  className?: string
}) {
  const checks = report ? [...report.checks].sort((a, b) => Number(a.ok) - Number(b.ok) || Number(b.elim) - Number(a.elim)) : []
  const failedElim = checks.filter((c) => !c.ok && c.elim).length
  const failed = checks.filter((c) => !c.ok).length
  return (
    <div className={cn('space-y-2 text-xs', className)}>
      <div className="flex flex-wrap items-center gap-2">
        {report ? <ReportBadge report={report} /> : !error && <span className="text-muted-foreground">sem relatório</span>}
        {report && (
          <span className="text-muted-foreground">
            {checks.length - failed}/{checks.length} ok{failedElim > 0 && ` · ${failedElim} eliminatória(s)`}
          </span>
        )}
        {loading && <Loader2 className="ml-auto size-3.5 animate-spin text-muted-foreground" />}
      </div>
      {error && <p className="rounded bg-red-950/60 p-1.5 text-red-200">Validação indisponível: {error}</p>}
      <ul className="space-y-1">
        {checks.map((c) => {
          const loc = onSelect && hasLocation(c)
          const sel = selected === c.id
          return (
            <li key={c.id}>
              <button
                type="button"
                disabled={!onSelect}
                onClick={() => onSelect?.(sel ? null : c)}
                className={cn(
                  'flex w-full items-start gap-2 rounded-md border px-2 py-1 text-left',
                  c.ok ? 'border-transparent' : c.elim ? 'border-red-500/60 bg-red-500/10' : 'border-amber-500/40 bg-amber-500/5',
                  onSelect && 'hover:bg-muted/60',
                  sel && 'ring-1 ring-fuchsia-400',
                )}
                title={loc ? 'Clique para destacar no canvas' : undefined}
              >
                <span className={cn('mt-px w-3 shrink-0 font-bold', c.ok ? 'text-emerald-400' : 'text-red-400')}>{c.ok ? '✓' : '✗'}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 font-mono font-medium">
                    {c.id}
                    {c.elim && <span className={cn('rounded px-1 font-sans text-[9px] uppercase', c.ok ? 'bg-muted text-muted-foreground' : 'bg-red-500 text-white')}>eliminatória</span>}
                    {loc && <span className="ml-auto text-[10px] text-fuchsia-400">◎</span>}
                  </span>
                  {c.detail && <span className="block break-words text-muted-foreground">{c.detail}</span>}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

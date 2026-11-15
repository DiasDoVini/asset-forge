// Tarefas de instalação/download em segundo plano (servidor): progresso, cancelar e log. Usado pela tela de
// Instalação e pela aba Modelos das Opções.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, CircleX, Download, Loader2, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type SetupTask } from '../api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const fmtBytes = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(b >= 1e10 ? 0 : 1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.max(0, b / 1e3).toFixed(0)} KB`)

/** Lista de tarefas; enquanto houver alguma rodando, atualiza a cada segundo. */
export function useSetupTasks() {
  return useQuery({
    queryKey: ['setup-tasks'],
    queryFn: api.setupTasks,
    refetchInterval: (q) => ((q.state.data ?? []).some((t) => t.status === 'running' || t.status === 'queued') ? 1_000 : 10_000),
  })
}

/** Inicia a instalação de um item e avisa quando terminar (as consultas de estado são invalidadas). */
export function useInstall(after: string[][] = []) {
  const qc = useQueryClient()
  return async (item: string, title: string) => {
    try {
      await api.setupInstall(item)
      toast.success(`${title}: iniciado`, { description: 'Acompanhe o progresso na lista.' })
    } catch (e) {
      toast.error(`${title}: ${e instanceof Error ? e.message : e}`)
    }
    qc.invalidateQueries({ queryKey: ['setup-tasks'] })
    for (const k of after) qc.invalidateQueries({ queryKey: k })
  }
}

export function TaskProgress({ task, compact = false }: { task: SetupTask; compact?: boolean }) {
  const qc = useQueryClient()
  const [log, setLog] = useState(false)
  const full = useQuery({ queryKey: ['setup-task', task.id], queryFn: () => api.setupTask(task.id), enabled: log, refetchInterval: task.status === 'running' ? 1_500 : false })
  const pct = task.total ? Math.min(100, (task.done / task.total) * 100) : null
  const running = task.status === 'running' || task.status === 'queued'
  return (
    <div className={cn('text-xs', !compact && 'rounded-md border p-2')}>
      <div className="flex items-center gap-2">
        {running ? <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" /> : task.status === 'done' ? <CircleCheck className="size-3.5 shrink-0 text-emerald-500" /> : <CircleX className="size-3.5 shrink-0 text-red-400" />}
        {!compact && <span className="shrink-0 font-medium">{task.title}</span>}
        <span className="min-w-0 flex-1 truncate text-muted-foreground" title={task.message}>
          {task.status === 'queued' ? 'na fila…' : task.message}
        </span>
        {pct !== null && running && <span className="shrink-0 tabular-nums">{fmtBytes(task.done)} / {fmtBytes(task.total)} · {pct.toFixed(0)}%</span>}
        <button className="shrink-0 underline-offset-2 hover:underline" onClick={() => setLog(!log)}>{log ? 'ocultar log' : 'log'}</button>
        {running && (
          <Button size="icon-xs" variant="ghost" title="Cancelar" onClick={async () => { await api.setupCancel(task.id); qc.invalidateQueries({ queryKey: ['setup-tasks'] }) }}>
            <X />
          </Button>
        )}
      </div>
      {running && (
        <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
          <div className={cn('h-full bg-primary transition-[width]', pct === null && 'w-1/3 animate-pulse')} style={pct !== null ? { width: `${pct}%` } : undefined} />
        </div>
      )}
      {log && (
        <pre className="mt-1 max-h-56 overflow-auto rounded bg-black/40 p-2 font-mono text-[10px] leading-snug whitespace-pre-wrap">
          {(full.data?.log ?? []).join('\n') || '…'}
        </pre>
      )}
    </div>
  )
}

/** Botão "Baixar/Instalar" que vira a barra de progresso da tarefa ativa do item. */
export function InstallButton({ item, title, label = 'Baixar', tasks, onInstall, disabled }: {
  item: string
  title: string
  label?: string
  tasks: SetupTask[] | undefined
  onInstall: (item: string, title: string) => void
  disabled?: boolean
}) {
  const t = (tasks ?? []).find((x) => x.item === item)
  if (t && (t.status === 'running' || t.status === 'queued')) return <div className="w-full"><TaskProgress task={t} compact /></div>
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => onInstall(item, title)}><Download /> {label}</Button>
      {t && t.status !== 'done' && <span className="truncate text-xs text-red-400" title={t.message}>{t.status === 'cancelled' ? 'cancelado' : t.message}</span>}
    </div>
  )
}

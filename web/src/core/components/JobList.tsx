import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, ChevronRight, CircleX, Clock, Dices, Loader2, Pencil, RotateCcw, Square, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Job } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { DiagnoseButton } from './DiagnoseDialog'
import { pipelineTitle } from '../i18n/pipelines'

const ICON = {
  queued: <Clock className="size-4 text-muted-foreground" />,
  running: <Loader2 className="size-4 animate-spin text-sky-500" />,
  done: <CheckCircle2 className="size-4 text-emerald-500" />,
  error: <CircleX className="size-4 text-destructive" />,
  cancelled: <Square className="size-4 text-muted-foreground" />,
}

function summary(j: Job) {
  const p = j.request.params
  return String(p.prompt ?? p.instruction ?? p.input_asset ?? '')
}

const refLink = (project: string, ref: string) => `/p/${project}/a/${ref.split('@')[0]}/${parseInt(ref.split('@v')[1], 10)}`

export type JobFilter = 'all' | 'failed' | 'done' | 'active'

export function JobList({ project, compact, limit, filter = 'all' }: { project?: string; compact?: boolean; limit?: number; filter?: JobFilter }) {
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs({ limit: 300 }), refetchInterval: 15_000 })
  let list = (jobs.data ?? []).filter((j) => !project || j.request.project === project)
  if (filter === 'failed') list = list.filter((j) => j.status === 'error' || j.status === 'cancelled')
  if (filter === 'done') list = list.filter((j) => j.status === 'done')
  if (filter === 'active') list = list.filter((j) => j.status === 'running' || j.status === 'queued')
  if (limit) list = list.slice(0, limit)
  if (jobs.isLoading) return <p className="px-1 text-sm text-muted-foreground">Carregando…</p>
  if (!list.length) return <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">{filter === 'failed' ? 'Nenhuma falha.' : filter === 'active' ? 'Nada rodando agora.' : 'Nenhum job ainda.'}</p>

  return (
    <div className="space-y-2">
      {list.map((j) => <JobCard key={j.id} job={j} compact={compact} showProject={!project} />)}
    </div>
  )
}

function JobCard({ job: j, compact, showProject }: { job: Job; compact?: boolean; showProject?: boolean }) {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const nav = useNavigate()
  const detail = useQuery({ queryKey: ['job', j.id], queryFn: () => api.job(j.id), enabled: open })
  const active = j.status === 'running' || j.status === 'queued'
  const failed = j.status === 'error' || j.status === 'cancelled'

  const retry = async (newSeed = false) => {
    try {
      const n = await api.retryJob(j.id, { new_seed: newSeed })
      toast.success(newSeed ? 'Variação na fila' : 'Repetindo job', { description: `${n.request.pipeline} · ${n.id}` })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    } catch (e) {
      toast.error(String(e))
    }
  }
  const remove = async () => {
    await api.deleteJob(j.id)
    qc.setQueryData<Job[]>(['jobs'], (old) => (old ?? []).filter((x) => x.id !== j.id))
  }

  return (
    <div className={cn('rounded-lg border p-2.5 text-sm', j.status === 'error' && 'border-destructive/40')}>
      <div className="flex items-center gap-2">
        {ICON[j.status]}
        <button className="flex min-w-0 items-center gap-1 font-medium" onClick={() => setOpen(!open)} title="Detalhes" aria-expanded={open}>
          {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
          <span className="truncate" title={j.request.pipeline}>{pipelineTitle(j.request.pipeline)}</span>
        </button>
        {showProject && <span className="truncate text-xs text-muted-foreground">{j.request.project}</span>}
        {j.source && j.source !== 'server' && <Badge variant="outline" className="px-1 py-0 text-xs">{j.source}</Badge>}
        {j.retry_of && <Badge variant="outline" className="px-1 py-0 text-xs" title={`repetição de ${j.retry_of}`}>repetição</Badge>}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">{new Date(j.created).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</span>
        {active && (
          <Button size="icon-xs" variant="ghost" title="Cancelar" aria-label="Cancelar job" onClick={() => api.cancelJob(j.id)}>
            <Square />
          </Button>
        )}
      </div>
      {summary(j) && <p className={cn('mt-1 text-xs text-muted-foreground', compact && 'line-clamp-2')}>{summary(j)}</p>}
      {j.status === 'running' && (
        <div className="mt-2 flex items-center gap-2">
          <Progress value={j.progress * 100} className="flex-1" />
          <span className="w-10 text-right text-xs tabular-nums">{Math.round(j.progress * 100)}%</span>
        </div>
      )}
      {j.status === 'running' && j.message && <p className="mt-1 text-xs text-muted-foreground">{j.message}</p>}
      {j.error && <p className={cn('mt-1 text-xs break-words text-destructive', compact && !open && 'line-clamp-3')}>{j.error}</p>}
      {j.result && (
        <Link to={refLink(j.request.project, j.result)} className="mt-1 inline-block text-xs text-primary underline-offset-2 hover:underline">
          {j.result} →
        </Link>
      )}

      {!active && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {failed && (
            <Button size="xs" variant="secondary" onClick={() => retry(false)} title="Mesmos parâmetros"><RotateCcw /> Tentar de novo</Button>
          )}
          {j.status === 'error' && <DiagnoseButton job={j} />}
          {j.status === 'done' && (
            <Button size="xs" variant="ghost" onClick={() => retry(true)} title="Mesmos parâmetros, seed nova"><Dices /> Variação</Button>
          )}
          <Button size="xs" variant="ghost" onClick={() => nav(`/p/${j.request.project}/gerar?job=${j.id}`)} title="Abrir o formulário preenchido">
            <Pencil /> Editar e repetir
          </Button>
          <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={remove} title="Remover do histórico" aria-label="Remover do histórico"><Trash2 /></Button>
        </div>
      )}

      {open && (
        <div className="mt-2 space-y-2">
          <pre className="max-h-48 overflow-auto rounded bg-muted/50 p-2 text-xs">{JSON.stringify({ ...j.request.params, ...(j.request.asset ? { _asset: j.request.asset } : {}) }, null, 2)}</pre>
          {(detail.data?.logs?.length ?? 0) > 0 && (
            <pre className="max-h-48 overflow-auto rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap">{detail.data!.logs.join('\n')}</pre>
          )}
          <p className="text-xs text-muted-foreground">job {j.id}{j.retry_of ? ` · repete ${j.retry_of}` : ''}</p>
        </div>
      )}
      {!compact && !open && j.log_tail.length > 0 && j.status === 'running' && (
        <pre className="mt-2 max-h-32 overflow-auto rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap">{j.log_tail.join('\n')}</pre>
      )}
    </div>
  )
}

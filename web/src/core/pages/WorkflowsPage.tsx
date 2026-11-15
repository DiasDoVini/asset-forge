// Workflows que o Asset Forge mandou para o ComfyUI: o que está rodando, na fila, concluído ou rejeitado, em qual
// instância, de qual projeto/pipeline — com "Abrir no ComfyUI" (o grafo convertido para o editor de nós), baixar e cancelar.
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Download, ExternalLink, Loader2, Square, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { api, type WorkflowItem } from '../api'
import { pipelineTitle } from '../i18n/pipelines'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageBody, PageHeader } from '../components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

const STATUS: Record<WorkflowItem['status'], { label: string; tone: string }> = {
  running: { label: 'Rodando', tone: 'bg-sky-500/20 text-sky-300' },
  pending: { label: 'Na fila', tone: 'bg-amber-500/20 text-amber-300' },
  done: { label: 'Concluído', tone: 'bg-emerald-500/20 text-emerald-300' },
  error: { label: 'Erro', tone: 'bg-red-500/20 text-red-300' },
  rejected: { label: 'Rejeitado', tone: 'bg-red-500/20 text-red-300' },
  offline: { label: 'Instância parada', tone: 'bg-zinc-500/20 text-zinc-300' },
  unknown: { label: 'Fora do histórico', tone: 'bg-zinc-500/20 text-zinc-300' },
}

const FILTERS = [
  { id: 'all', title: 'Todos' },
  { id: 'active', title: 'Rodando e na fila' },
  { id: 'failed', title: 'Com erro' },
] as const

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—')
const took = (w: WorkflowItem) => (w.created && w.finished ? `${Math.max(0, w.finished - w.created).toFixed(0)} s` : null)

export default function WorkflowsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['id']>('all')
  const q = useQuery({
    queryKey: ['workflows'],
    queryFn: api.workflows,
    refetchInterval: (x) => ((x.state.data?.items ?? []).some((w) => w.status === 'running' || w.status === 'pending') ? 2_000 : 10_000),
  })
  const items = useMemo(() => {
    const all = q.data?.items ?? []
    if (filter === 'active') return all.filter((w) => w.status === 'running' || w.status === 'pending')
    if (filter === 'failed') return all.filter((w) => w.status === 'error' || w.status === 'rejected')
    return all
  }, [q.data, filter])
  const active = (q.data?.items ?? []).filter((w) => w.status === 'running' || w.status === 'pending').length
  const missingNode = Object.entries(q.data?.node_installed ?? {}).filter(([, ok]) => !ok).map(([k]) => k)

  return (
    <PageBody>
        <PageHeader
          title="Workflows no ComfyUI"
          description="Cada grafo que o Asset Forge enviou (pela interface, pelo MCP ou pelo CLI), com o estado ao vivo em cada instância."
          actions={active > 0 ? <Badge>{active} ativo(s)</Badge> : undefined}
        />

        <Tabs value={filter} onValueChange={(v) => setFilter(v as typeof filter)} className="mb-4">
          <TabsList>
            {FILTERS.map((f) => <TabsTrigger key={f.id} value={f.id}>{f.title}</TabsTrigger>)}
          </TabsList>
        </Tabs>

        {missingNode.length > 0 && <InstallNode missing={missingNode} />}
        {q.isLoading && <LoadingState />}
        {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
        {q.data && items.length === 0 && (
          <EmptyState icon={<Workflow />} title={filter === 'all' ? 'Nenhum workflow registrado ainda' : 'Nada neste filtro'}
            description={filter === 'all' ? 'Gere algo (aqui, pelo MCP ou pelo CLI) e o grafo enviado ao ComfyUI aparece nesta lista.' : undefined} />
        )}
        <div className="space-y-2">
          {items.map((w) => <Row key={w.id ?? w.prompt_id ?? Math.random()} w={w} />)}
        </div>
    </PageBody>
  )
}

function InstallNode({ missing }: { missing: string[] }) {
  const qc = useQueryClient()
  return (
    <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
      Para abrir os workflows direto no ComfyUI, as instâncias precisam do nó auxiliar <code>asset_forge_workflows</code> (falta em: {missing.join(', ')}).{' '}
      <Button
        size="xs"
        variant="outline"
        onClick={async () => {
          try {
            const r = await api.workflowInstallNode()
            toast.success(r.installed.length ? `Instalado em ${r.installed.join(', ')}: reinicie essas instâncias em Opções > ComfyUI` : 'Já instalado')
          } catch (e) {
            toast.error(String(e instanceof Error ? e.message : e))
          }
          qc.invalidateQueries({ queryKey: ['workflows'] })
        }}
      >
        Instalar
      </Button>
    </div>
  )
}

function Row({ w }: { w: WorkflowItem }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const st = STATUS[w.status]
  const live = w.status === 'running' || w.status === 'pending'

  const openInComfy = async () => {
    if (!w.id) return
    const win = window.open('about:blank', '_blank') // abre já (o bloqueador de pop-up barra janelas abertas depois do await)
    setBusy(true)
    try {
      const r = await api.workflowOpen(w.id)
      if (r.open_url) {
        if (win) win.location.href = r.open_url
        else window.open(r.open_url, '_blank')
      } else {
        win?.close()
        toast.warning(r.hint ?? 'Não deu para abrir direto', {
          action: { label: 'Baixar .json', onClick: () => window.open(`/api/workflows/${encodeURIComponent(w.id!)}/workflow.json`, '_blank') },
        })
      }
    } catch (e) {
      win?.close()
      toast.error(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn('rounded-lg border bg-card', live && 'border-sky-500/40')}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <button className="flex min-w-0 items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Detalhes do workflow">
          {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
          <span className={cn('inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', st.tone)}>
            {w.status === 'running' && <Loader2 className="size-3 animate-spin" />}
            {st.label}
          </span>
          <span className="truncate font-medium">{(w.meta.pipeline && pipelineTitle(w.meta.pipeline)) ?? (w.meta.external ? 'enviado por fora do Asset Forge' : 'workflow')}</span>
        </button>
        <span className="text-xs text-muted-foreground">
          {w.meta.project && <Link className="hover:underline" to={`/p/${w.meta.project}`}>{w.meta.project}</Link>}
          {w.meta.project && w.meta.asset && ' / '}
          {w.meta.project && w.meta.asset && <Link className="hover:underline" to={`/p/${w.meta.project}/a/${w.meta.asset}`}>{w.meta.asset}</Link>}
        </span>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
          <span title={w.url}>{w.instance} :{w.url.split(':').pop()}</span>
          <span>{when(w.created)}</span>
          {took(w) && <span>{took(w)}</span>}
          <span>{w.nodes} nós</span>
        </span>
        <div className="flex gap-1">
          {w.id && w.status !== 'offline' && (
            <Button size="sm" variant="outline" onClick={openInComfy} disabled={busy} aria-label="Abrir este workflow no ComfyUI">
              {busy ? <Loader2 className="animate-spin" /> : <ExternalLink />} Abrir no ComfyUI
            </Button>
          )}
          {live && w.id && (
            <Button
              size="sm"
              variant="ghost"
              aria-label="Cancelar"
              onClick={async () => {
                try {
                  await api.workflowCancel(w.id!)
                  toast.success('Cancelado no ComfyUI')
                } catch (e) {
                  toast.error(String(e instanceof Error ? e.message : e))
                }
                qc.invalidateQueries({ queryKey: ['workflows'] })
              }}
            >
              <Square /> Cancelar
            </Button>
          )}
        </div>
      </div>
      {w.error && !open && <p className="truncate px-3 pb-2 pl-9 text-xs text-red-300" title={w.error}>{w.error}</p>}
      {open && (
        <div className="space-y-2 border-t px-3 py-2 pl-9 text-xs">
          {w.error && <pre className="max-h-48 overflow-auto rounded bg-black/40 p-2 whitespace-pre-wrap text-red-300">{w.error}</pre>}
          <div>
            <span className="text-muted-foreground">Nós: </span>
            {w.summary.join(' → ')}
          </div>
          {w.models.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground">Modelos:</span>
              {w.models.map((m) => <Badge key={m} variant="secondary" className="font-mono">{m}</Badge>)}
            </div>
          )}
          <div className="text-muted-foreground">prompt_id: <code>{w.prompt_id ?? '—'}</code></div>
          {w.id && (
            <div className="flex flex-wrap gap-1">
              <a className={buttonVariants({ size: 'xs', variant: 'ghost' })} href={`/api/workflows/${encodeURIComponent(w.id)}/workflow.json`} download>
                <Download /> Workflow (.json para arrastar no ComfyUI)
              </a>
              <a className={buttonVariants({ size: 'xs', variant: 'ghost' })} href={`/api/workflows/${encodeURIComponent(w.id)}/api.json`} download>
                <Download /> Formato API
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

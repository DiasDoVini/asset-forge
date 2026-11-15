// Instalação e preparação: tudo o que o Asset Forge precisa (ferramentas, contas, ComfyUI, modelos por recurso, LLMs),
// com o estado de cada item, download/instalação em segundo plano e um "preparar o essencial".
import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleDashed, ExternalLink, FolderOpen, KeyRound, PackageCheck, RefreshCw, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { api, type SetupItem, type SetupOverview } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { fmtBytes, TaskProgress, useSetupTasks } from '../components/SetupTasks'
import { useConfirm } from '../components/ConfirmDialog'
import { PageBody, PageHeader } from '../components/PageHeader'
import { ErrorState, LoadingState } from '../components/PageState'

const STATE: Record<SetupItem['state'], { label: string; tone: string; icon: ReactNode }> = {
  ok: { label: 'Pronto', tone: 'text-emerald-300', icon: <CheckCircle2 className="size-4 text-emerald-500" /> },
  partial: { label: 'Incompleto', tone: 'text-amber-300', icon: <AlertTriangle className="size-4 text-amber-500" /> },
  missing: { label: 'Falta', tone: 'text-muted-foreground', icon: <CircleDashed className="size-4 text-muted-foreground" /> },
  manual: { label: '—', tone: 'text-muted-foreground', icon: <CircleDashed className="size-4 text-muted-foreground" /> },
}

export default function SetupPage() {
  const { confirm: ask } = useConfirm()
  const qc = useQueryClient()
  const tasks = useSetupTasks()
  const running = (tasks.data ?? []).some((t) => t.status === 'running' || t.status === 'queued')
  const setup = useQuery({ queryKey: ['setup'], queryFn: api.setup, refetchInterval: running ? 3_000 : 30_000 })
  const d = setup.data

  const install = async (it: SetupItem) => {
    const blocked = it.depends.map((id) => d?.items.find((x) => x.id === id)).filter((x) => x && x.state !== 'ok' && x.id !== 'huggingface')
    if (blocked.length && !(await ask({ title: `${it.title} depende de outros itens`, description: `Ainda não pronto: ${blocked.map((x) => x!.title).join(', ')}. Continuar mesmo assim?`, confirmLabel: 'Continuar' }))) return
    if (it.install_label === 'Instalar' && it.group === 'tools' && !(await ask({ title: `Instalar ${it.title} pelo winget?`, description: 'O instalador aceita a licença do pacote em seu nome.', confirmLabel: 'Instalar' }))) return
    try {
      await api.setupInstall(it.id)
      toast.success(`${it.title}: iniciado`)
    } catch (e) {
      toast.error(`${it.title}: ${e instanceof Error ? e.message : e}`)
    }
    qc.invalidateQueries({ queryKey: ['setup'] })
    qc.invalidateQueries({ queryKey: ['setup-tasks'] })
  }

  const essentials = useMemo(() => (d?.items ?? []).filter((i) => i.required && i.state !== 'ok' && i.can_install && !i.task), [d])
  const essentialBytes = essentials.reduce((s, i) => s + Math.max(0, i.size - (i.have ?? 0)), 0)
  const prepareAll = async () => {
    if (!(await ask({ title: `Preparar ${essentials.length} item(ns) essenciais?`, description: <>Cerca de {fmtBytes(essentialBytes)} para baixar/instalar:<ul className="mt-2 list-disc pl-5">{essentials.map((i) => <li key={i.id}>{i.title}</li>)}</ul></>, confirmLabel: 'Preparar' }))) return
    for (const it of essentials) {
      try {
        await api.setupInstall(it.id)
      } catch (e) {
        toast.error(`${it.title}: ${e instanceof Error ? e.message : e}`)
      }
    }
    qc.invalidateQueries({ queryKey: ['setup'] })
    qc.invalidateQueries({ queryKey: ['setup-tasks'] })
  }

  const total = d?.items.length ?? 0
  const ready = d?.items.filter((i) => i.state === 'ok').length ?? 0
  const reqMissing = d?.items.filter((i) => i.required && i.state !== 'ok') ?? []

  return (
    <PageBody>
        <PageHeader
          title="Instalação e downloads"
          description="Tudo o que o Asset Forge usa para funcionar, com o que já está pronto nesta máquina."
          actions={
            <Button variant="ghost" size="sm" onClick={() => { qc.invalidateQueries({ queryKey: ['setup'] }); qc.invalidateQueries({ queryKey: ['setup-tasks'] }) }}>
              <RefreshCw className={cn(setup.isFetching && 'animate-spin')} /> Verificar de novo
            </Button>
          }
        />
        {setup.isError && <ErrorState error={setup.error} retry={() => setup.refetch()} />}
        {!d && !setup.isError && <LoadingState label="Verificando o que está instalado…" />}
        {d && (
          <>
            <div className="mb-5 grid gap-3 rounded-lg border bg-card p-4 md:grid-cols-[1fr_auto]">
              <div className="space-y-1 text-sm">
                <div className="flex items-center gap-2">
                  {reqMissing.length ? <AlertTriangle className="size-4 text-amber-500" /> : <PackageCheck className="size-4 text-emerald-500" />}
                  <span className="font-medium">
                    {reqMissing.length ? `Faltam ${reqMissing.length} item(ns) essenciais` : 'O essencial está pronto'} · {ready} de {total} itens prontos
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <FolderOpen className="size-3.5" /> Modelos em <code>{d.models_dir}</code>
                  {!d.models_dir_exists && <span className="text-amber-300">(ainda não existe; será criada)</span>}
                  {d.disk_free !== null && <span>· {fmtBytes(d.disk_free)} livres</span>}
                </div>
                {!d.winget && <p className="text-xs text-amber-300">winget não encontrado: as ferramentas precisam ser instaladas pelos links.</p>}
              </div>
              <div className="flex items-center">
                <Button onClick={prepareAll} disabled={!essentials.length} title="Instala/baixa tudo o que está marcado como essencial e falta">
                  <Sparkles /> Preparar o essencial{essentials.length ? ` (~${fmtBytes(essentialBytes)})` : ''}
                </Button>
              </div>
            </div>

            {(tasks.data ?? []).length > 0 && (
              <section className="mb-5 space-y-1.5">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-medium">Tarefas</h2>
                  <Button size="xs" variant="ghost" className="ml-auto" onClick={async () => { await api.setupClear(); qc.invalidateQueries({ queryKey: ['setup-tasks'] }) }}>
                    Limpar concluídas
                  </Button>
                </div>
                {tasks.data!.slice(0, 8).map((t) => <TaskProgress key={t.id} task={t} />)}
              </section>
            )}

            {d.groups.map((g) => (
              <section key={g.id} className="mb-6">
                <h2 className="text-base font-medium">{g.title}</h2>
                <p className="mb-2 text-xs text-muted-foreground">{g.description}{g.id === 'llm' && <> · escolha o modelo ativo em <Link className="underline" to="/options?tab=models">Opções &gt; Modelos</Link></>}</p>
                <div className="divide-y rounded-lg border bg-card">
                  {d.items.filter((i) => i.group === g.id).map((it) => <ItemRow key={it.id} it={it} d={d} onInstall={install} tasks={tasks.data} />)}
                </div>
              </section>
            ))}
          </>
        )}
    </PageBody>
  )
}

function ItemRow({ it, d, onInstall, tasks }: { it: SetupItem; d: SetupOverview; onInstall: (i: SetupItem) => void; tasks: ReturnType<typeof useSetupTasks>['data'] }) {
  const [open, setOpen] = useState(false)
  const st = STATE[it.state]
  const task = (tasks ?? []).find((t) => t.item === it.id && (t.status === 'running' || t.status === 'queued'))
  const last = (tasks ?? []).find((t) => t.item === it.id)
  const remaining = Math.max(0, it.size - (it.have ?? 0))
  const deps = it.depends.map((id) => d.items.find((x) => x.id === id)).filter((x) => x && x.state !== 'ok')
  const expandable = !!it.files?.length || !!it.manual || it.id === 'huggingface'
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="mt-0.5">{st.icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button className={cn('flex items-center gap-1 font-medium', !expandable && 'cursor-default')} onClick={() => expandable && setOpen(!open)}>
              {expandable && (open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />)}
              {it.title}
            </button>
            {it.required && <Badge variant="secondary">essencial</Badge>}
            <span className={cn('text-xs', st.tone)}>{st.label}{it.detail ? ` · ${it.detail}` : ''}</span>
          </div>
          <p className="text-xs text-muted-foreground">{it.description}</p>
          {it.needed_for && <p className="text-xs text-muted-foreground/80">Usado em: {it.needed_for}</p>}
          {deps.length > 0 && it.state !== 'ok' && <p className="text-xs text-amber-300/90">Antes: {deps.map((x) => x!.title).join(', ')}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {it.size > 0 && it.state !== 'ok' && <span className="text-xs text-muted-foreground tabular-nums">{fmtBytes(remaining || it.size)}</span>}
          {it.link && (
            <a href={it.link} target="_blank" rel="noreferrer" className={buttonVariants({ size: 'sm', variant: 'ghost' })} title={it.link}><ExternalLink /></a>
          )}
          {it.can_install && it.state !== 'ok' && !task && (
            <Button size="sm" variant={it.required ? 'default' : 'outline'} onClick={() => onInstall(it)}>
              {it.state === 'partial' && it.files ? 'Completar' : it.install_label}
            </Button>
          )}
          {it.can_install && it.state === 'ok' && it.group === 'comfy' && !task && it.id !== 'comfy-desktop' && (
            <Button size="sm" variant="ghost" onClick={() => onInstall(it)} title="Roda o instalador de novo (atualiza/repara)">Reinstalar</Button>
          )}
        </div>
      </div>
      {task && <div className="mt-2 pl-7"><TaskProgress task={task} /></div>}
      {!task && last && last.status === 'error' && <div className="mt-2 pl-7"><TaskProgress task={last} /></div>}
      {open && (
        <div className="mt-2 space-y-2 pl-7 text-xs">
          {it.manual && <p className="text-muted-foreground">{it.manual}</p>}
          {it.id === 'huggingface' && <HfToken />}
          {it.files && (
            <table className="w-full">
              <tbody>
                {it.files.map((f) => (
                  <tr key={f.folder + f.name} className="border-t border-border/50">
                    <td className="py-1 pr-2"><code>{f.folder}/{f.name}</code></td>
                    <td className="py-1 pr-2 text-right tabular-nums text-muted-foreground">{fmtBytes(f.size)}</td>
                    <td className={cn('py-1 text-right', f.state === 'ok' ? 'text-emerald-400' : f.state === 'missing' ? 'text-muted-foreground' : 'text-amber-300')}>
                      {{ ok: 'ok', missing: 'falta', partial: 'parcial (.part)', mismatch: 'tamanho errado' }[f.state]}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

function HfToken() {
  const qc = useQueryClient()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="flex max-w-xl items-center gap-2"
      onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true)
        try {
          const r = await api.setupHfToken(token)
          toast.success(`Token salvo (conta ${r.user})`)
          setToken('')
          qc.invalidateQueries({ queryKey: ['setup'] })
        } catch (err) {
          toast.error(String(err instanceof Error ? err.message : err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <KeyRound className="size-4 shrink-0 text-muted-foreground" />
      <Input type="password" autoComplete="off" placeholder="hf_… (token de leitura)" value={token} onChange={(e) => setToken(e.target.value)} className="h-8" />
      <Button size="sm" type="submit" disabled={busy || token.trim().length < 8}>Salvar</Button>
    </form>
  )
}

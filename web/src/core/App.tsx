import { useEffect, useRef } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { CircleDot, Download, Loader2, FolderOpen, Hammer, ListChecks, Plus, RefreshCw, Settings, Sparkles, Workflow } from 'lucide-react'
import { SoulDetailPage, SoulsPage } from './pages/SoulsPage'
import { toast } from 'sonner'
import { api } from './api'
import { onForgeEvent, useForgeEvents } from './events'
import { pipelineTitle } from './i18n/pipelines'
import { Progress } from '@/components/ui/progress'
import { allRoutes } from './registry'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import HomePage from './pages/HomePage'
import ProjectPage from './pages/ProjectPage'
import AssetPage from './pages/AssetPage'
import JobsPage from './pages/JobsPage'
import OptionsPage from './pages/OptionsPage'
import SetupPage from './pages/SetupPage'
import WorkflowsPage from './pages/WorkflowsPage'
import { CreateProjectDialog } from './components/CreateProjectDialog'
import { useConfirm } from './components/ConfirmDialog'
import { ErrorState } from './components/PageState'
import { NativeSelect } from '@/components/ui/native-select'

/** Avisa quando um job da interface termina (com atalho para o resultado), em qualquer tela. */
function useJobToasts() {
  const nav = useNavigate()
  const loc = useLocation()
  const seen = useRef(new Set<string>())
  useEffect(
    () =>
      onForgeEvent((e) => {
        if (e.type !== 'job' || e.job.source === 'chat') return   // o chat mostra o resultado na conversa
        const j = e.job
        if (j.status === 'queued' || j.status === 'running') {
          seen.current.add(j.id)
          return
        }
        if (!seen.current.delete(j.id)) return
        const title = pipelineTitle(j.request.pipeline)
        if (j.status === 'done' && j.result) {
          const [slug, v] = j.result.split('@v')
          const to = `/p/${j.request.project}/a/${slug}/${parseInt(v, 10)}`
          if (loc.pathname.startsWith(`/p/${j.request.project}/a/${slug}`)) return
          toast.success(`Pronto: ${title}`, { description: j.result, action: { label: 'Abrir', onClick: () => nav(to) } })
        } else if (j.status === 'error') {
          toast.error(`Falhou: ${title}`, { description: (j.error ?? '').slice(0, 160), action: { label: 'Ver', onClick: () => nav('/jobs') } })
        }
      }),
    [nav, loc.pathname],
  )
}

export default function App() {
  useForgeEvents()
  useJobToasts()
  return (
    <div className="flex h-svh flex-col overflow-hidden md:flex-row">
      <Sidebar />
      <MobileBar />
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/options" element={<OptionsPage />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/souls" element={<SoulsPage />} />
          <Route path="/souls/:soul" element={<SoulDetailPage />} />
          <Route path="/p/:project" element={<ProjectPage />} />
          <Route path="/p/:project/:tab" element={<ProjectPage />} />
          <Route path="/p/:project/a/:asset" element={<AssetPage />} />
          <Route path="/p/:project/a/:asset/:version" element={<AssetPage />} />
          {allRoutes().map((r) => (
            <Route key={r.path} path={r.path} element={r.element} />
          ))}
          <Route path="*" element={<ErrorState error="Página não encontrada." back={{ to: '/', label: 'Projetos' }} />} />
        </Routes>
      </main>
    </div>
  )
}

/** Barra compacta para telas estreitas (a sidebar some abaixo de md). */
function MobileBar() {
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const project = useMatch('/p/:project/*')?.params.project
  const nav = useNavigate()
  return (
    <div className="flex items-center gap-2 border-b bg-sidebar px-3 py-2 md:hidden">
      <Link to="/" title="Asset Forge" aria-label="Início"><Hammer className="size-5 text-sidebar-primary" /></Link>
      <NativeSelect
        aria-label="Projeto"
        wrapperClassName="min-w-0 flex-1"
        value={project ?? ''}
        onChange={(e) => nav(e.target.value ? `/p/${e.target.value}` : '/')}
      >
        <option value="">Projetos…</option>
        {(projects.data ?? []).map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
      </NativeSelect>
      <Link to="/souls" title="Creator Souls"><Sparkles className="size-5" /></Link>
      <Link to="/jobs" title="Fila de jobs"><ListChecks className="size-5" /></Link>
      <Link to="/workflows" title="Workflows"><Workflow className="size-5" /></Link>
      <Link to="/setup" title="Instalação"><Download className="size-5" /></Link>
      <Link to="/options" title="Opções"><Settings className="size-5" /></Link>
    </div>
  )
}

function Sidebar() {
  const { confirm: ask } = useConfirm()
  const project = useMatch('/p/:project/*')?.params.project
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs() })
  const active = (jobs.data ?? []).filter((j) => j.status === 'running' || j.status === 'queued').length
  const running = (jobs.data ?? []).find((j) => j.status === 'running')
  const workflows = useQuery({ queryKey: ['workflows'], queryFn: api.workflows, refetchInterval: 10_000 })
  const wfActive = (workflows.data?.items ?? []).filter((w) => w.status === 'running' || w.status === 'pending').length
  const comfy = useQuery({ queryKey: ['comfy-instances'], queryFn: api.comfyInstances, refetchInterval: 20_000 })
  const installed = (comfy.data ?? []).filter((i) => i.installed)
  const up = installed.filter((i) => i.ready).length

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
      <Link to="/" className="flex items-center gap-2 px-4 py-4 font-semibold">
        <Hammer className="size-5 text-sidebar-primary" />
        Asset Forge
      </Link>
      <div className="flex items-center justify-between px-4 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Projetos
        <CreateProjectDialog trigger={<button className="rounded p-0.5 hover:bg-sidebar-accent" title="Novo projeto"><Plus className="size-4" /></button>} />
      </div>
      <ScrollArea className="min-h-0 flex-1 px-2">
        {(projects.data ?? []).map((p) => (
          <NavLink
            key={p.slug}
            to={`/p/${p.slug}`}
            className={cn(
              'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent',
              project === p.slug && 'bg-sidebar-accent font-medium',
            )}
          >
            <FolderOpen className="size-4 shrink-0 opacity-70" />
            <span className="truncate">{p.name}</span>
          </NavLink>
        ))}
        {projects.data?.length === 0 && <p className="px-2 py-1 text-sm text-muted-foreground">Nenhum projeto ainda.</p>}
      </ScrollArea>
      <div className="space-y-1 border-t p-2 text-sm">
        <NavLink to="/souls" className={({ isActive }) => cn('flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent', isActive && 'bg-sidebar-accent')}>
          <Sparkles className="size-4" /> Creator Souls
        </NavLink>
        <NavLink to="/jobs" className={({ isActive }) => cn('flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent', isActive && 'bg-sidebar-accent')}>
          <ListChecks className="size-4" /> Fila de jobs
          {active > 0 && <Badge className="ml-auto">{active}</Badge>}
        </NavLink>
        {running && (
          <Link to="/jobs" className="block rounded-md px-2 pb-1.5 text-xs text-muted-foreground hover:text-foreground" title={running.request.pipeline}>
            <div className="mb-1 flex items-center gap-1.5 truncate">
              <Loader2 className="size-3 shrink-0 animate-spin" />
              <span className="truncate">{pipelineTitle(running.request.pipeline)}{running.message ? ` · ${running.message}` : ''}</span>
            </div>
            <Progress value={running.progress * 100} className="h-1" />
          </Link>
        )}
        <NavLink to="/workflows" className={({ isActive }) => cn('flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent', isActive && 'bg-sidebar-accent')}>
          <Workflow className="size-4" /> Workflows
          {wfActive > 0 && <Badge variant="secondary" className="ml-auto">{wfActive}</Badge>}
        </NavLink>
        <NavLink to="/setup" className={({ isActive }) => cn('flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent', isActive && 'bg-sidebar-accent')}>
          <Download className="size-4" /> Instalação
        </NavLink>
        <NavLink to="/options" className={({ isActive }) => cn('flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-sidebar-accent', isActive && 'bg-sidebar-accent')}>
          <Settings className="size-4" /> Opções
        </NavLink>
        <Link
          to="/options?tab=comfy"
          className="flex items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          title={(comfy.data ?? []).map((i) => `${i.title} :${i.port} — ${i.ready ? 'online' : i.installed ? 'parada' : 'não instalada'}`).join('\n') || health.data?.comfy.url}
        >
          <CircleDot className={cn('size-3', !comfy.data ? 'text-muted-foreground' : up === installed.length && up > 0 ? 'text-emerald-500' : up > 0 ? 'text-amber-500' : 'text-red-500')} />
          ComfyUI {comfy.data ? `${up}/${installed.length} online` : '…'}
        </Link>
        {health.data?.supervised && (
          <button
            className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
            title={`Recarrega o servidor com o código atual (espera os jobs em andamento). Iniciado: ${health.data.started ?? '?'}`}
            onClick={async () => {
              if (!(await ask({ title: 'Recarregar o servidor?', description: 'Reinicia o servidor com o código atual. Jobs em andamento terminam antes.', confirmLabel: 'Recarregar' }))) return
              const t = toast.loading('Recarregando o servidor…')
              try {
                await api.reloadServer()
                toast.success('Servidor recarregado', { id: t })
              } catch (e) {
                toast.error(String(e), { id: t })
              }
            }}
          >
            <RefreshCw className="size-3" /> Recarregar servidor
            <span className="ml-auto tabular-nums">{health.data.pipelines} pipelines</span>
          </button>
        )}
      </div>
    </aside>
  )
}

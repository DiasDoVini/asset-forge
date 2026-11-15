// Opções da plataforma: instâncias do ComfyUI (status, iniciar/parar/reiniciar, VRAM, fila, logs, manter no ar),
// servidor do Asset Forge (endereço, caminhos, recarregar) e preferências do editor guardadas no navegador.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bone, Bot, Brain, Copy, Cpu, ExternalLink, FileText, Keyboard, Loader2, Play, Power, RefreshCw, RotateCcw, Server, Square, Trash2, Zap,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, type ComfyInstance, type SystemInfo } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { ModelsTab } from './ModelsTab'
import { AITab } from './AITab'
import { AnimLibraryTab } from './AnimLibraryTab'
import { useConfirm } from '../components/ConfirmDialog'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageBody, PageHeader } from '../components/PageHeader'
import { ErrorState, LoadingState } from '../components/PageState'

const TABS = [
  { id: 'comfy', title: 'ComfyUI', icon: <Cpu className="size-4" /> },
  { id: 'ai', title: 'Assistente de IA', icon: <Bot className="size-4" /> },
  { id: 'models', title: 'Modelos e LLMs', icon: <Brain className="size-4" /> },
  { id: 'anim', title: 'Animações 3D', icon: <Bone className="size-4" /> },
  { id: 'server', title: 'Servidor', icon: <Server className="size-4" /> },
  { id: 'editor', title: 'Editor', icon: <Keyboard className="size-4" /> },
] as const
type TabId = (typeof TABS)[number]['id']

/** O que roda em cada instância (para saber o que para junto). */
const USED_BY: Record<string, string> = {
  desktop: 'Imagens 2D (Z-Image), edição (Qwen Edit), vídeo (Wan 2.2), upscale (SeedVR2), remoção de fundo',
  '3d': 'Malhas 3D com Hunyuan3D 2.1 (forma + textura)',
  trellis: 'Malhas 3D com TRELLIS.2 (bordas nítidas, peças pequenas)',
}

const gb = (b?: number) => (b ? `${(b / 1024 ** 3).toFixed(1)} GB` : '?')

export default function OptionsPage() {
  const [params, setParams] = useSearchParams()
  const tab = (TABS.find((t) => t.id === params.get('tab'))?.id ?? 'comfy') as TabId
  return (
    <PageBody>
      <PageHeader title="Opções" description="Servidores de geração, modelos, o servidor do Asset Forge e preferências deste navegador." />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) }, { replace: true })} className="gap-0">
        <TabsList variant="line" className="mb-5 w-full justify-start border-b">
          {TABS.map((t) => (
            <TabsTrigger key={t.id} value={t.id} className="flex-none gap-1.5">{t.icon} {t.title}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {tab === 'comfy' && <ComfyTab />}
      {tab === 'ai' && <AITab />}
      {tab === 'models' && <ModelsTab />}
      {tab === 'anim' && <AnimLibraryTab />}
      {tab === 'server' && <ServerTab />}
      {tab === 'editor' && <EditorTab />}
    </PageBody>
  )
}

// ---------------------------------------------------------------- ComfyUI

function stateOf(i: ComfyInstance): { label: string; tone: string } {
  if (!i.installed) return { label: 'Não instalada', tone: 'bg-zinc-500/20 text-zinc-300' }
  if (i.ready) return { label: 'Online', tone: 'bg-emerald-500/20 text-emerald-300' }
  if (i.listening) return { label: 'Carregando…', tone: 'bg-amber-500/20 text-amber-300' }
  if (i.starting) return { label: 'Iniciando…', tone: 'bg-amber-500/20 text-amber-300' }
  return { label: 'Parada', tone: 'bg-red-500/20 text-red-300' }
}

function ComfyTab() {
  const { confirm: ask } = useConfirm()
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 10_000 })
  const inst = useQuery({
    queryKey: ['comfy-instances'],
    queryFn: api.comfyInstances,
    refetchInterval: (q) => ((q.state.data ?? []).some((i) => i.starting && !i.ready) ? 2_000 : 8_000),
  })
  const [busy, setBusy] = useState<string | null>(null)
  const list = inst.data ?? []
  const online = list.filter((i) => i.ready).length
  const installed = list.filter((i) => i.installed)

  const act = async (i: ComfyInstance, action: 'start' | 'stop' | 'restart' | 'free' | 'interrupt') => {
    const verbs = { start: 'Iniciando', stop: 'Parando', restart: 'Reiniciando', free: 'Liberando VRAM de', interrupt: 'Cancelando a fila de' }
    if (action === 'stop' && !(await ask({ title: `Parar ${i.title}?`, description: <>O supervisor não sobe de novo até você iniciar.<br />Usado por: {USED_BY[i.name] ?? '—'}</>, confirmLabel: 'Parar', destructive: true }))) return
    if (action === 'restart' && !(await ask({ title: `Reiniciar ${i.title}?`, description: 'Leva de 30 s a alguns minutos para voltar.', confirmLabel: 'Reiniciar' }))) return
    setBusy(`${i.name}:${action}`)
    const t = toast.loading(`${verbs[action]} ${i.title}…`)
    try {
      try {
        await api.comfyAction(i.name, action)
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409 && (action === 'stop' || action === 'restart'))) throw e
        if (!(await ask({ title: 'Há uma geração em andamento', description: `${e.message}. Interromper e continuar?`, confirmLabel: 'Interromper e continuar', destructive: true }))) {
          toast.dismiss(t)
          return
        }
        await api.comfyAction(i.name, action, true)
      }
      const done = { start: 'iniciando (acompanhe o status e o log)', stop: 'parada', restart: 'reiniciando', free: 'VRAM liberada', interrupt: 'fila cancelada' }
      toast.success(`${i.title}: ${done[action]}`, { id: t })
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e), { id: t })
    } finally {
      setBusy(null)
      qc.invalidateQueries({ queryKey: ['comfy-instances'] })
      qc.invalidateQueries({ queryKey: ['health'] })
    }
  }

  const startAll = async () => {
    for (const i of installed.filter((x) => !x.listening && !x.starting)) await act(i, 'start')
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3 text-sm">
        <span className="font-medium">{inst.isLoading ? '…' : `${online} de ${installed.length} instaladas online${list.length > installed.length ? ` · ${list.length - installed.length} não instalada(s)` : ''}`}</span>
        <SupervisorNote sys={sys.data} />
        <div className="ml-auto flex gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => qc.invalidateQueries({ queryKey: ['comfy-instances'] })} title="Atualizar agora">
            <RefreshCw className={cn(inst.isFetching && 'animate-spin')} /> Atualizar
          </Button>
          <Button size="sm" onClick={startAll} disabled={!!busy || !installed.some((x) => !x.listening && !x.starting)}>
            <Play /> Iniciar todas
          </Button>
        </div>
      </div>
      {inst.error && <p className="text-sm text-destructive">{String(inst.error)}</p>}
      {list.map((i) => <InstanceCard key={i.name} i={i} busy={busy} act={act} />)}
      <p className="text-xs text-muted-foreground">
        As instâncias rodam em processos separados (dependências incompatíveis entre si) e continuam no ar se o servidor do Asset Forge recarregar.
        Os pipelines usam <code>{sys.data?.comfy_url ?? 'http://127.0.0.1:8188'}</code> para 2D e as portas 8189/8190 para 3D.
      </p>
    </div>
  )
}

function SupervisorNote({ sys }: { sys?: SystemInfo }) {
  if (!sys) return null
  if (sys.comfy_supervisor)
    return (
      <span className="text-muted-foreground" title={`PID ${sys.comfy_supervisor.pid}`}>
        · Supervisor ativo: reinicia as instâncias com <b>Manter no ar</b> se elas caírem.
      </span>
    )
  return (
    <span className="text-amber-300/90">
      · Sem supervisor{sys.supervised ? ' atualizado (reinicie o Asset Forge pelo atalho para ativar)' : ' (servidor iniciado sem `forge up`)'}:
      as instâncias só sobem pelos botões aqui.
    </span>
  )
}

function InstanceCard({ i, busy, act }: {
  i: ComfyInstance
  busy: string | null
  act: (i: ComfyInstance, a: 'start' | 'stop' | 'restart' | 'free' | 'interrupt') => void
}) {
  const qc = useQueryClient()
  const [showLog, setShowLog] = useState(false)
  const st = stateOf(i)
  const dev = i.devices?.[0]
  const used = dev ? dev.vram_total - dev.vram_free : 0
  const working = (k: string) => busy === `${i.name}:${k}`
  const btn = (k: 'start' | 'stop' | 'restart' | 'free' | 'interrupt', icon: ReactNode, label: string, disabled: boolean, title: string) => (
    <Button size="sm" variant={k === 'start' ? 'default' : 'outline'} disabled={!!busy || disabled} onClick={() => act(i, k)} title={title}>
      {working(k) ? <Loader2 className="animate-spin" /> : icon} {label}
    </Button>
  )

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-start gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{i.title}</span>
            <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', st.tone)}>{st.label}</span>
            <code className="text-xs text-muted-foreground">:{i.port}</code>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{USED_BY[i.name]}</p>
          {!i.installed && i.install_hint && (
            <p className="mt-2 text-xs">
              Para instalar:{' '}
              {/^powershell /.test(i.install_hint) ? <code className="rounded bg-muted px-1">{i.install_hint}</code> : i.install_hint}
            </p>
          )}
        </div>
        {i.installed && (
          <label className="flex items-center gap-2 text-xs" title="Com o supervisor do `forge up`: se a instância cair, ela sobe de novo sozinha. Parar desliga isto.">
            <Switch
              size="sm"
              checked={i.autostart}
              onCheckedChange={async (v) => {
                await api.comfyAutostart(i.name, v)
                qc.invalidateQueries({ queryKey: ['comfy-instances'] })
              }}
            />
            Manter no ar
          </label>
        )}
      </div>

      {i.ready && (
        <div className="grid gap-x-6 gap-y-2 border-t px-4 py-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <Info label="ComfyUI">{i.system?.comfyui_version ?? '?'}</Info>
          <Info label="PyTorch">{i.system?.pytorch_version ?? '?'}</Info>
          <Info label="Fila">
            {i.queue ? `${i.queue.running} rodando · ${i.queue.pending} esperando` : '?'}
          </Info>
          <Info label="Processo">{i.pid ? `PID ${i.pid}` : '?'}</Info>
          {dev && (
            <div className="sm:col-span-2">
              <div className="mb-1 flex justify-between text-muted-foreground">
                <span className="truncate" title={dev.name}>{dev.name.replace(/ : .*$/, '').replace(/^cuda:\d+ /, '')}</span>
                <span className="tabular-nums">VRAM {gb(used)} / {gb(dev.vram_total)}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded bg-muted">
                <div className={cn('h-full', used / dev.vram_total > 0.85 ? 'bg-red-500' : 'bg-primary')} style={{ width: `${Math.round((used / dev.vram_total) * 100)}%` }} />
              </div>
            </div>
          )}
          {i.system?.ram_total && (
            <Info label="RAM do sistema">{gb(i.system.ram_total - (i.system.ram_free ?? 0))} / {gb(i.system.ram_total)}</Info>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t px-4 py-2.5">
        {i.installed && !i.listening && btn('start', <Play />, 'Iniciar', i.starting, 'Sobe a instância (o log mostra o progresso)')}
        {i.listening && btn('stop', <Square />, 'Parar', false, 'Encerra o processo; o supervisor não sobe de novo até você iniciar')}
        {i.installed && btn('restart', <RotateCcw />, 'Reiniciar', !i.listening && !i.starting, 'Para e sobe de novo (ex.: depois de instalar nós ou modelos)')}
        {i.ready && btn('free', <Zap />, 'Liberar VRAM', false, 'Descarrega os modelos da GPU (o próximo job carrega de novo)')}
        {i.ready && btn('interrupt', <Trash2 />, 'Cancelar fila', !i.queue?.running && !i.queue?.pending, 'Interrompe a geração atual e limpa a fila desta instância')}
        <div className="ml-auto flex gap-1.5">
          {i.ready && (
            <a href={i.url} target="_blank" rel="noreferrer" className={buttonVariants({ size: 'sm', variant: 'ghost' })} title="Abre a interface do ComfyUI desta instância">
              <ExternalLink /> Abrir ComfyUI
            </a>
          )}
          <Button size="sm" variant={showLog ? 'secondary' : 'ghost'} onClick={() => setShowLog(!showLog)}>
            <FileText /> Log
          </Button>
        </div>
      </div>
      {showLog && <LogView name={i.name} live={i.starting || (i.listening && !i.ready)} />}
    </div>
  )
}

function Info({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="truncate tabular-nums">{children}</div>
    </div>
  )
}

function LogView({ name, live }: { name: string; live: boolean }) {
  const log = useQuery({ queryKey: ['comfy-log', name], queryFn: () => api.comfyLog(name, 400), refetchInterval: live ? 2_000 : 10_000 })
  const pre = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const el = pre.current
    if (el) el.scrollTop = el.scrollHeight
  }, [log.data?.text])
  return (
    <div className="border-t">
      <div className="flex items-center gap-2 px-4 py-1.5 text-xs text-muted-foreground">
        <span className="truncate">{log.data?.path}</span>
        {log.data?.path && <CopyButton text={log.data.path} />}
      </div>
      <pre ref={pre} className="max-h-80 overflow-auto bg-black/40 px-4 py-2 font-mono text-[11px] leading-snug whitespace-pre-wrap">
        {log.isLoading ? 'Carregando…' : log.data?.text || '(log vazio — a instância não foi iniciada por aqui ou pelo supervisor)'}
      </pre>
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  return (
    <button
      className="shrink-0 rounded p-0.5 hover:bg-muted hover:text-foreground"
      title="Copiar"
      onClick={() => navigator.clipboard.writeText(text).then(() => toast.success('Copiado'), () => toast.error('Não deu para copiar'))}
    >
      <Copy className="size-3" />
    </button>
  )
}

// ---------------------------------------------------------------- servidor

function ServerTab() {
  const { confirm: ask } = useConfirm()
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 10_000 })
  const [reloading, setReloading] = useState(false)
  const s = sys.data
  if (sys.isError) return <ErrorState error={sys.error} retry={() => sys.refetch()} />
  if (!s) return <LoadingState />

  const reload = async () => {
    const busy = s.active_jobs.length ? `\n\nHá ${s.active_jobs.length} job(s) em andamento; o servidor espera eles terminarem.` : ''
    if (!(await ask({ title: 'Recarregar o servidor do Asset Forge?', description: `Reinicia com o código atual; os ComfyUI continuam no ar.${busy}`, confirmLabel: 'Recarregar' }))) return
    setReloading(true)
    const t = toast.loading('Recarregando o servidor…')
    try {
      const before = s.boot_id
      await api.reloadServer()
      for (let k = 0; k < 120; k++) {
        await new Promise((r) => setTimeout(r, 1000))
        try {
          if ((await api.system()).boot_id !== before) break
        } catch {
          /* ainda voltando */
        }
      }
      toast.success('Servidor recarregado', { id: t })
      qc.invalidateQueries()
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e), { id: t })
    } finally {
      setReloading(false)
    }
  }

  const errors = Object.entries(s.modality_errors)
  return (
    <div className="space-y-4">
      <Section title="Servidor do Asset Forge">
        <div className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <Info label="Endereço">http://{s.host}:{s.port}</Info>
          <Info label="No ar desde">{s.started.replace('T', ' ')}</Info>
          <Info label="Pipelines">{s.pipelines}</Info>
          <Info label="Python">{s.python}</Info>
          <Info label="Sistema">{s.platform}</Info>
          <Info label="Jobs em andamento">{s.active_jobs.length ? s.active_jobs.join(', ') : 'nenhum'}</Info>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={reload} disabled={reloading || !s.supervised} title={s.supervised ? 'Reinicia o processo do servidor (os ComfyUI continuam no ar)' : 'Servidor sem supervisor: reinicie manualmente'}>
            {reloading ? <Loader2 className="animate-spin" /> : <Power />} Recarregar servidor
          </Button>
          {!s.supervised && <span className="text-xs text-muted-foreground">Rodando sem supervisor (<code>--no-supervise</code>): reinicie pelo terminal.</span>}
        </div>
      </Section>

      {errors.length > 0 && (
        <Section title="Modalidades com erro">
          <ul className="space-y-1 text-sm text-destructive">
            {errors.map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}
          </ul>
        </Section>
      )}

      <Section title="Pastas">
        <div className="space-y-1.5 text-sm">
          {Object.entries({ Projetos: s.paths.projects, 'Creator Souls': s.paths.souls, 'Logs (servidor e ComfyUI)': s.paths.logs, 'Instalação': s.paths.root, 'Interface web': s.paths.web }).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2">
              <span className="w-48 shrink-0 text-muted-foreground">{k}</span>
              <code className="min-w-0 truncate text-xs" title={v ?? ''}>{v ?? '—'}</code>
              {v && <CopyButton text={v} />}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Configuração por variáveis de ambiente">
        <p className="mb-2 text-xs text-muted-foreground">
          Definidas antes de iniciar o Asset Forge (ex.: no atalho). <code>FORGE_PROJECTS_DIR</code>, <code>FORGE_COMFY_URL</code>, <code>FORGE_COMFY_URL_TRELLIS</code>,
          <code> FORGE_SOULS_DIR</code>, <code>FORGE_HOST</code>, <code>FORGE_PORT</code>.
        </p>
        <div className="space-y-1 font-mono text-xs">
          {Object.entries(s.env).map(([k, v]) => (
            <div key={k}><span className="text-muted-foreground">{k}=</span>{v}</div>
          ))}
          <div><span className="text-muted-foreground">URL do ComfyUI 2D:</span> {s.comfy_url}</div>
        </div>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <h2 className="mb-3 text-sm font-medium">{title}</h2>
      {children}
    </section>
  )
}

// ---------------------------------------------------------------- editor / navegador

type ShortcutsMod = typeof import('@/modalities/2d/sprite/shortcuts')
type DialogMod = typeof import('@/modalities/2d/sprite/ShortcutsDialog')

function EditorTab() {
  const { confirm: ask } = useConfirm()
  const [mods, setMods] = useState<{ s: ShortcutsMod; d: DialogMod } | null>(null)
  const [open, setOpen] = useState(false)
  const [, bump] = useState(0)
  useEffect(() => {
    Promise.all([import('@/modalities/2d/sprite/shortcuts'), import('@/modalities/2d/sprite/ShortcutsDialog')]).then(([s, d]) => setMods({ s, d }))
  }, [])
  const local = (() => {
    try {
      return Object.keys(localStorage).filter((k) => /^(asset-forge|forge3d\.|forge\.)/.test(k))
    } catch {
      return []
    }
  })()
  const changed = mods ? Object.entries(mods.s.loadBindings()).filter(([id, k]) => JSON.stringify(k) !== JSON.stringify(mods.s.defaults()[id])).length : 0

  return (
    <div className="space-y-4">
      <Section title="Editor de sprite: atalhos de teclado">
        <p className="mb-3 text-sm text-muted-foreground">
          {changed ? `${changed} ação(ões) com atalho diferente do padrão.` : 'Usando os atalhos padrão (como no Aseprite).'}
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setOpen(true)} disabled={!mods}><Keyboard /> Configurar atalhos</Button>
          <Button size="sm" variant="ghost" disabled={!mods || !changed} onClick={() => { mods!.s.saveBindings(mods!.s.defaults()); bump((n) => n + 1); toast.success('Atalhos restaurados') }}>
            <RotateCcw /> Restaurar padrões
          </Button>
        </div>
        {open && mods && (
          <mods.d.ShortcutsDialog
            bindings={mods.s.loadBindings()}
            onChange={(b) => { mods.s.saveBindings(b); bump((n) => n + 1) }}
            onClose={() => setOpen(false)}
          />
        )}
      </Section>
      <Section title="Preferências guardadas neste navegador">
        <p className="mb-2 text-sm text-muted-foreground">
          Atalhos, vista do editor e outras preferências locais ({local.length} {local.length === 1 ? 'item' : 'itens'}). Não afeta projetos nem assets.
        </p>
        {local.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-1">{local.map((k) => <Badge key={k} variant="secondary" className="font-mono text-xs">{k}</Badge>)}</div>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={!local.length}
          onClick={async () => {
            if (!(await ask({ title: 'Apagar as preferências locais?', description: 'Atalhos, vista do editor e outras preferências deste navegador. Projetos e assets não são afetados.', confirmLabel: 'Apagar', destructive: true }))) return
            try {
              local.forEach((k) => localStorage.removeItem(k))
            } catch {
              /* sem armazenamento */
            }
            bump((n) => n + 1)
            toast.success('Preferências locais apagadas')
          }}
        >
          <Trash2 /> Apagar preferências locais
        </Button>
      </Section>
    </div>
  )
}

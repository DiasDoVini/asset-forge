// Geração avançada: laço gerar → avaliar (Claude via API, ou você/um agente) → ajustar.
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bot, CheckCircle2, CircleX, Loader2, Play, Plus, Sparkles, Square, Trophy, User, Wand2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Project, type RefineAction, type RefineIteration, type RefinerInfo, type RefineSession } from '../api'
import { SchemaForm, type Values } from '../SchemaForm'
import { AssetPicker } from './AssetPicker'
import { checker } from './ImageViewer'
import { CategorySelect, FolderSelect } from './Organize'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

const STATUS: Record<string, { label: string; cls: string }> = {
  ready: { label: 'na fila', cls: 'bg-muted text-muted-foreground' },
  running: { label: 'gerando', cls: 'bg-sky-500/15 text-sky-400' },
  awaiting_review: { label: 'aguardando avaliação', cls: 'bg-amber-500/15 text-amber-400' },
  done: { label: 'concluído', cls: 'bg-emerald-500/15 text-emerald-400' },
  failed: { label: 'falhou', cls: 'bg-destructive/15 text-destructive' },
  cancelled: { label: 'cancelado', cls: 'bg-muted text-muted-foreground' },
}

const scoreCls = (s: number) => (s >= 8 ? 'bg-emerald-500/20 text-emerald-400' : s >= 5 ? 'bg-amber-500/20 text-amber-400' : 'bg-destructive/20 text-destructive')
const refLink = (project: string, ref: string) => `/p/${project}/a/${ref.split('@')[0]}/${parseInt(ref.split('@v')[1], 10)}`

export function RefinePanel({ project }: { project: Project }) {
  const [search, setSearch] = useSearchParams()
  const selected = search.get('s')
  const select = (id: string | null) => {
    const s = new URLSearchParams(search)
    if (id) s.set('s', id)
    else s.delete('s')
    setSearch(s, { replace: true })
  }
  const sessions = useQuery({
    queryKey: ['refine', project.slug],
    queryFn: () => api.refineSessions(project.slug),
    refetchInterval: (q) => ((q.state.data ?? []).some((s) => s.status === 'running' || s.status === 'ready') ? 3000 : false),
  })
  const meta = useQuery({ queryKey: ['refiners'], queryFn: api.refiners })

  return (
    <div className="grid h-full grid-cols-1 md:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col border-b md:border-r md:border-b-0">
        <div className="flex items-center justify-between p-3">
          <div>
            <h2 className="font-semibold">Geração avançada</h2>
            <p className="text-xs text-muted-foreground">gera → Claude avalia → ajusta, até ficar bom</p>
          </div>
          <Button size="sm" onClick={() => select('new')}><Plus /> Nova</Button>
        </div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-auto px-2 pb-3">
          {(sessions.data ?? []).map((s) => (
            <button key={s.id} onClick={() => select(s.id)}
              className={cn('flex w-full gap-2 rounded-lg border p-2 text-left hover:bg-muted/50', selected === s.id && 'border-primary bg-muted/40')}>
              <div className={cn('flex size-12 shrink-0 items-center justify-center overflow-hidden rounded', checker)}>
                {s.thumb && <img src={s.thumb} className="max-h-full max-w-full" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{s.title}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
                  <span className={cn('rounded px-1.5', STATUS[s.status].cls)}>{STATUS[s.status].label}</span>
                  <span className="text-muted-foreground">{s.iteration_count} it.</span>
                  {s.last_score != null && <span className={cn('rounded px-1 tabular-nums', scoreCls(s.last_score))}>{s.last_score}</span>}
                  {s.critic === 'api' ? <Bot className="size-3 text-muted-foreground" /> : <User className="size-3 text-muted-foreground" />}
                </div>
              </div>
            </button>
          ))}
          {sessions.data?.length === 0 && <p className="px-2 text-sm text-muted-foreground">Nenhuma sessão ainda.</p>}
        </div>
      </aside>
      <section className="min-h-0 overflow-auto">
        {selected === 'new' || (!selected && sessions.data?.length === 0) ? (
          meta.data && <NewRefine project={project} refiners={meta.data.refiners} apiOk={meta.data.api_critic.available}
            apiDetail={meta.data.api_critic.detail} onCreated={(id) => select(id)} />
        ) : selected ? (
          <SessionView project={project} id={selected} refiners={meta.data?.refiners ?? []} apiOk={meta.data?.api_critic.available ?? false} />
        ) : (
          <div className="flex h-full items-center justify-center p-8 text-center text-muted-foreground">
            <div className="space-y-2">
              <Wand2 className="mx-auto size-8" />
              <p>Escolha uma sessão ou comece uma nova geração avançada.</p>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}

// ------------------------------------------------------------------ nova sessão

function NewRefine({ project, refiners, apiOk, apiDetail, onCreated }: {
  project: Project; refiners: RefinerInfo[]; apiOk: boolean; apiDetail: string; onCreated: (id: string) => void
}) {
  const [refiner, setRefiner] = useState(refiners.find((r) => r.id === '3d.model')?.id ?? refiners[0]?.id ?? '')
  const [prompt, setPrompt] = useState('')
  const [refs, setRefs] = useState<string[]>([])
  const [criteria, setCriteria] = useState('')
  const [maxIt, setMaxIt] = useState(6)
  const [target, setTarget] = useState(8)
  const [critic, setCritic] = useState<'api' | 'manual'>(apiOk ? 'api' : 'manual')
  const [title, setTitle] = useState('')
  const [folder, setFolder] = useState<string | undefined>(undefined)
  const [category, setCategory] = useState<string | null | undefined>(undefined)
  const [options, setOptions] = useState('{}')
  const [busy, setBusy] = useState(false)
  const r = refiners.find((x) => x.id === refiner)

  const start = async () => {
    let params: Record<string, unknown> = {}
    try {
      params = JSON.parse(options || '{}')
    } catch {
      return toast.error('Opções do refinador: JSON inválido')
    }
    setBusy(true)
    try {
      const s = await api.refineStart(project.slug, {
        refiner, critic, title: title || undefined, folder, category: category ?? undefined,
        brief: { prompt, references: refs.filter(Boolean), criteria: criteria.split('\n').map((x) => x.trim()).filter(Boolean),
          max_iterations: maxIt, target_score: target, params },
      })
      toast.success('Geração avançada iniciada')
      onCreated(s.id)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5 p-6">
      <div>
        <h2 className="text-lg font-semibold">Nova geração avançada</h2>
        <p className="text-sm text-muted-foreground">
          O asset é gerado, avaliado (prévias de vários ângulos no 3D) e ajustado em iterações até atingir a nota-alvo ou o limite.
          Cada iteração vira uma versão do mesmo asset.
        </p>
      </div>
      <div className="grid gap-1.5">
        <Label>Tipo</Label>
        <div className="flex flex-wrap gap-2">
          {refiners.map((x) => (
            <button key={x.id} type="button" onClick={() => setRefiner(x.id)}
              className={cn('rounded-lg border px-3 py-2 text-left text-sm', refiner === x.id ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
              <div className="font-medium">{x.title}</div>
              <div className="max-w-64 text-xs text-muted-foreground">{x.description}</div>
            </button>
          ))}
          {!refiners.some((x) => x.modality === '3d') && (
            <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">Modelo 3D: em preparação pela modalidade 3D</div>
          )}
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label>O que você quer</Label>
        <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ex.: estátua de um cavaleiro ajoelhado com espada fincada no chão, estilo low poly estilizado, pronta para jogo" />
      </div>
      <div className="grid gap-1.5">
        <Label>Referências <span className="font-normal text-muted-foreground">(opcional; conceitos, fotos, vistas)</span></Label>
        {[...refs, ''].slice(0, 4).map((v, i) => (
          <AssetPicker key={i} project={project.slug} value={v || undefined} placeholder={`referência ${i + 1}`}
            onChange={(nv) => {
              const next = [...refs]
              if (nv) next[i] = nv
              else next.splice(i, 1)
              setRefs(next.filter(Boolean))
            }} />
        ))}
      </div>
      <div className="grid gap-1.5">
        <Label>Critérios de aceitação <span className="font-normal text-muted-foreground">(um por linha; somam-se aos padrões)</span></Label>
        <Textarea rows={3} value={criteria} onChange={(e) => setCriteria(e.target.value)} placeholder={'menos de 20k triângulos\nsilhueta reconhecível de longe\ncores fiéis ao conceito'} />
        {r && r.default_criteria.length > 0 && (
          <ul className="list-disc pl-5 text-xs text-muted-foreground">{r.default_criteria.map((c) => <li key={c}>{c}</li>)}</ul>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label className="mb-1.5">Máximo de iterações: {maxIt}</Label>
          <Slider min={1} max={15} value={[maxIt]} onValueChange={(v) => setMaxIt(Array.isArray(v) ? v[0] : v)} />
        </div>
        <div>
          <Label className="mb-1.5">Nota para aceitar: {target}</Label>
          <Slider min={5} max={10} step={0.5} value={[target]} onValueChange={(v) => setTarget(Array.isArray(v) ? v[0] : v)} />
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label>Quem avalia cada iteração</Label>
        <div className="grid gap-2 sm:grid-cols-2">
          <button type="button" disabled={!apiOk} onClick={() => setCritic('api')}
            className={cn('rounded-lg border p-3 text-left text-sm disabled:opacity-50', critic === 'api' ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
            <div className="flex items-center gap-1.5 font-medium"><Bot className="size-4" /> Claude (automático)</div>
            <div className="text-xs text-muted-foreground">Claude Opus 5.5 via API avalia as prévias e pede os ajustes sozinho. Cobra por uso.</div>
            {!apiOk && <div className="mt-1 text-xs text-amber-500">{apiDetail}</div>}
          </button>
          <button type="button" onClick={() => setCritic('manual')}
            className={cn('rounded-lg border p-3 text-left text-sm', critic === 'manual' ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
            <div className="flex items-center gap-1.5 font-medium"><User className="size-4" /> Manual / Claude Code</div>
            <div className="text-xs text-muted-foreground">Pausa a cada iteração: você avalia aqui, ou um agente via MCP (refine_review).</div>
          </button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5"><Label>Nome</Label><Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="opcional" /></div>
        <div className="grid gap-1.5">
          <Label>Organização</Label>
          <div className="flex gap-2">
            <FolderSelect project={project} value={folder} onChange={setFolder} allowKeep="Pasta: raiz" />
            <CategorySelect project={project} value={category} onChange={setCategory} allowKeep="Categoria" />
          </div>
        </div>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">Opções do refinador (JSON)</summary>
        <Textarea rows={3} className="mt-2 font-mono text-xs" value={options} onChange={(e) => setOptions(e.target.value)}
          placeholder='{"asset_type": "character"}' />
      </details>
      <Button onClick={start} disabled={busy || !prompt.trim() || !refiner}><Sparkles /> Iniciar geração avançada</Button>
    </div>
  )
}

// ------------------------------------------------------------------ sessão

function SessionView({ project, id, refiners, apiOk }: { project: Project; id: string; refiners: RefinerInfo[]; apiOk: boolean }) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['refine', project.slug, id],
    queryFn: () => api.refineSession(project.slug, id),
    refetchInterval: (qq) => (qq.state.data && ['running', 'ready'].includes(qq.state.data.status) ? 2000 : false),
  })
  const s = q.data
  const [zoom, setZoom] = useState<string | null>(null)
  if (!s) return <div className="p-6 text-muted-foreground">Carregando…</div>
  const refiner = refiners.find((r) => r.id === s.refiner)
  const refresh = (n?: RefineSession) => {
    if (n) qc.setQueryData(['refine', project.slug, id], n)
    qc.invalidateQueries({ queryKey: ['refine', project.slug] })
  }
  const active = s.status === 'running' || s.status === 'ready'

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold">{s.title}</h2>
          <p className="text-sm text-muted-foreground">{s.brief.prompt}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
            <span className={cn('rounded px-1.5 py-0.5', STATUS[s.status].cls)}>{STATUS[s.status].label}</span>
            <Badge variant="outline">{refiner?.title ?? s.refiner}</Badge>
            <Badge variant="outline">{s.critic === 'api' ? 'Claude automático' : 'manual / agente'}</Badge>
            <span className="text-muted-foreground">{s.iterations.length}/{s.brief.max_iterations} iterações · alvo {s.brief.target_score}</span>
            <Link className="text-primary hover:underline" to={`/p/${project.slug}/a/${s.asset}`}>asset {s.asset} →</Link>
          </div>
          {s.message && <p className="mt-1 text-xs text-muted-foreground">{active && <Loader2 className="mr-1 inline size-3 animate-spin" />}{s.message}</p>}
          {s.error && <p className="mt-1 text-xs text-destructive">{s.error}</p>}
        </div>
        <div className="flex gap-1.5">
          {s.critic === 'manual' && apiOk && s.status === 'awaiting_review' && (
            <Button size="sm" variant="outline" onClick={async () => refresh(await api.refineResume(project.slug, id, 'api'))}><Bot /> Deixar o Claude avaliar</Button>
          )}
          {(s.status === 'failed' || s.status === 'cancelled') && (
            <Button size="sm" variant="outline" onClick={async () => refresh(await api.refineResume(project.slug, id))}><Play /> Retomar</Button>
          )}
          {!['done', 'cancelled'].includes(s.status) && (
            <Button size="sm" variant="ghost" onClick={async () => refresh(await api.refineCancel(project.slug, id))}><Square /> Parar</Button>
          )}
        </div>
      </header>

      {(s.final || s.best) && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5 text-sm">
          <Trophy className="size-4 text-emerald-400" />
          {s.final ? 'Resultado final' : 'Melhor até agora'}:
          <Link className="font-medium text-primary hover:underline" to={refLink(project.slug, (s.final ?? s.best)!)}>{s.final ?? s.best}</Link>
        </div>
      )}

      {s.status === 'awaiting_review' && refiner && (
        <ReviewForm key={s.iterations.length} project={project} session={s} refiner={refiner} onDone={refresh} />
      )}

      <div className="space-y-3">
        {[...s.iterations].reverse().map((it) => <IterationCard key={it.n} it={it} project={project} onZoom={setZoom} />)}
      </div>

      <Dialog open={zoom !== null} onOpenChange={(o) => !o && setZoom(null)}>
        <DialogContent className="sm:max-w-4xl">
          <DialogTitle className="sr-only">Prévia</DialogTitle>
          {zoom && <div className={cn('flex items-center justify-center rounded', checker)}><img src={zoom} className="max-h-[75vh]" /></div>}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function IterationCard({ it, project, onZoom }: { it: RefineIteration; project: Project; onZoom: (u: string) => void }) {
  const r = it.review
  return (
    <div className={cn('rounded-lg border p-3', it.error && 'border-destructive/40')}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">#{it.n}</span>
        <Badge variant="outline" className="font-mono text-xs">{it.action.pipeline}</Badge>
        {it.action.note && <span className="text-xs text-muted-foreground">{it.action.note}</span>}
        {it.result && <Link className="ml-auto text-xs text-primary hover:underline" to={refLink(project.slug, it.result)}>{it.result}</Link>}
        {!it.finished && <Loader2 className="ml-auto size-4 animate-spin text-sky-500" />}
      </div>
      {it.error && <p className="mt-1.5 flex gap-1.5 text-xs break-words text-destructive"><AlertTriangle className="size-3.5 shrink-0" />{it.error}</p>}
      {it.previews.length > 0 && (
        <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
          {it.previews.map((pv) => (
            <button key={pv.path} onClick={() => pv.url && onZoom(pv.url)} className="overflow-hidden rounded border text-left">
              <div className={cn('flex aspect-square items-center justify-center', checker)}>
                {pv.url && <img src={pv.url} className="max-h-full max-w-full" loading="lazy" />}
              </div>
              <div className="truncate px-1.5 py-0.5 text-xs text-muted-foreground">{pv.label}</div>
            </button>
          ))}
        </div>
      )}
      <details className="mt-1.5 text-xs">
        <summary className="cursor-pointer text-muted-foreground">parâmetros</summary>
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted/50 p-2 text-[11px]">{JSON.stringify(it.resolved_params, null, 2)}</pre>
      </details>
      {r && (
        <div className="mt-2 rounded-md bg-muted/40 p-2.5 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn('rounded px-1.5 font-semibold tabular-nums', scoreCls(r.score))}>{r.score}</span>
            {r.verdict === 'accept' ? <CheckCircle2 className="size-4 text-emerald-400" /> : r.verdict === 'restart' ? <CircleX className="size-4 text-amber-400" /> : <Wand2 className="size-4 text-sky-400" />}
            <span className="text-xs">{{ accept: 'aceito', revise: 'ajustar', restart: 'recomeçar' }[r.verdict]}</span>
            <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
              {r.reviewer.startsWith('claude') ? <Bot className="size-3" /> : <User className="size-3" />}{r.reviewer}
            </span>
          </div>
          {r.summary && <p className="mt-1">{r.summary}</p>}
          {r.issues.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">{r.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          {r.next_action && (
            <p className="mt-1 text-xs">→ próxima: <code>{r.next_action.pipeline}</code> {r.next_action.note}</p>
          )}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ revisão manual

function ReviewForm({ project, session, refiner, onDone }: { project: Project; session: RefineSession; refiner: RefinerInfo; onDone: (s: RefineSession) => void }) {
  const [score, setScore] = useState(5)
  const [verdict, setVerdict] = useState<'accept' | 'revise' | 'restart'>('revise')
  const [summary, setSummary] = useState('')
  const [issues, setIssues] = useState('')
  const acts = refiner.actions.filter((a) => a.available)
  const [pipeline, setPipeline] = useState(acts[0]?.pipeline ?? '')
  const spec = acts.find((a) => a.pipeline === pipeline)
  const [params, setParams] = useState<Values>({})
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const last = session.iterations[session.iterations.length - 1]

  useEffect(() => {
    const props = spec?.params_schema?.properties ?? {}
    const v: Values = {}
    if ('input_asset' in props) v.input_asset = verdict === 'restart' && session.brief.references.length ? '$reference' : '$current'
    setParams(v)
  }, [pipeline, verdict]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setBusy(true)
    try {
      const next: RefineAction | null = verdict === 'accept' ? null : { pipeline, params, note }
      const s = await api.refineReview(project.slug, session.id, {
        score, verdict, summary, issues: issues.split('\n').map((x) => x.trim()).filter(Boolean), next_action: next,
      })
      toast.success(verdict === 'accept' ? 'Aceito' : 'Ajuste enviado; gerando próxima iteração')
      onDone(s)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const schemaNoInput = useMemo(() => {
    if (!spec?.params_schema) return null
    return spec.params_schema
  }, [spec])

  return (
    <div className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
      <div className="flex items-center gap-2 font-medium"><User className="size-4" /> Avaliar a iteração #{last?.n}</div>
      <p className="text-xs text-muted-foreground">
        Também dá para um agente avaliar: no Claude Code, peça para revisar a sessão <code>{session.id}</code> (ferramentas refine_status / refine_review).
      </p>
      <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
        <div>
          <Label className="mb-1.5">Nota: {score}</Label>
          <Slider min={0} max={10} step={0.5} value={[score]} onValueChange={(v) => setScore(Array.isArray(v) ? v[0] : v)} />
        </div>
        <div className="inline-flex self-end rounded-lg border p-0.5 text-sm">
          {(['accept', 'revise', 'restart'] as const).map((v) => (
            <button key={v} type="button" onClick={() => setVerdict(v)}
              className={cn('rounded-md px-3 py-1', verdict === v ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
              {{ accept: 'Aceitar', revise: 'Ajustar', restart: 'Recomeçar' }[v]}
            </button>
          ))}
        </div>
      </div>
      <Input value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="resumo (opcional)" />
      <Textarea rows={2} value={issues} onChange={(e) => setIssues(e.target.value)} placeholder="problemas, um por linha" />
      {verdict !== 'accept' && (
        <div className="space-y-3 rounded-md border bg-background/50 p-3">
          <div className="grid gap-1.5">
            <Label>Próxima ação</Label>
            <select className="h-8 rounded-md border bg-transparent px-2 text-sm" value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
              {acts.map((a) => <option key={a.pipeline} value={a.pipeline}>{a.title} — {a.when}</option>)}
            </select>
            {spec?.hints && <p className="text-xs text-muted-foreground">dica: {spec.hints}</p>}
            <p className="text-xs text-muted-foreground">Placeholders: <code>$current</code> (último resultado), <code>$best</code>, <code>$reference</code>, <code>$references</code>.</p>
          </div>
          {schemaNoInput && <SchemaForm key={pipeline + verdict} schema={schemaNoInput} value={params} onChange={setParams} />}
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="por que esta ação (opcional)" />
        </div>
      )}
      <Button onClick={submit} disabled={busy || (verdict !== 'accept' && !pipeline)}>
        {busy ? <Loader2 className="animate-spin" /> : verdict === 'accept' ? <CheckCircle2 /> : <Play />}
        {verdict === 'accept' ? 'Aceitar resultado' : 'Enviar avaliação e gerar'}
      </Button>
    </div>
  )
}

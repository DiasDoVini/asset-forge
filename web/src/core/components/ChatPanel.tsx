// Aba Chat do projeto: conversas com um modelo (local ou Claude) que controla o Asset Forge. Os assets gerados aparecem
// na conversa e ficam no projeto (com versão), como qualquer outro.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, Bot, ChevronDown, ChevronRight, Loader2, MessagesSquare, Plus, Square, Trash2, User, Wrench } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Chat, type ChatAsset, type ChatMessage, type Project } from '../api'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ModelPicker, type ModelChoice } from './ModelPicker'
import { EmptyState, LoadingState } from './PageState'
import { useConfirm } from './ConfirmDialog'
import { checker } from './ImageViewer'
import { pipelineTitle } from '../i18n/pipelines'

const SUGGESTIONS = [
  'Crie um ícone de poção de mana azul',
  'Gere um baú de tesouro e depois uma versão pixel art 32px dele',
  'Faça uma placa de loja escrita "ARMAS" (use o Qwen-Image)',
  'Liste os assets do projeto e me sugira o que falta',
]

export function ChatPanel({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [search, setSearch] = useSearchParams()
  const { confirm } = useConfirm()
  const list = useQuery({ queryKey: ['chats', project.slug], queryFn: () => api.chats(project.slug) })
  const cid = search.get('c') ?? list.data?.[0]?.id ?? null
  const select = (id: string | null) => {
    const s = new URLSearchParams(search)
    if (id) s.set('c', id)
    else s.delete('c')
    setSearch(s, { replace: true })
  }
  const create = async () => {
    const c = await api.newChat(project.slug)
    qc.invalidateQueries({ queryKey: ['chats', project.slug] })
    select(c.id)
    return c
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 md:grid-cols-[230px_minmax(0,1fr)]">
      <aside className="hidden min-h-0 flex-col border-r md:flex">
        <div className="p-2">
          <Button size="sm" variant="outline" className="w-full" onClick={create}><Plus /> Nova conversa</Button>
        </div>
        <nav aria-label="Conversas" className="min-h-0 flex-1 space-y-0.5 overflow-auto px-2 pb-2">
          {(list.data ?? []).map((c) => (
            <div key={c.id} className={cn('group flex items-center rounded-md hover:bg-muted', c.id === cid && 'bg-muted')}>
              <button className="min-w-0 flex-1 px-2 py-1.5 text-left" onClick={() => select(c.id)} aria-current={c.id === cid ? 'page' : undefined}>
                <div className="flex items-center gap-1.5 truncate text-sm">
                  {c.running && <Loader2 className="size-3 shrink-0 animate-spin text-primary" />}
                  <span className="truncate">{c.title}</span>
                </div>
                <div className="text-xs text-muted-foreground">{new Date(c.updated * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })} · {c.assets} asset(s)</div>
              </button>
              <button
                className="mr-1 rounded p-1 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-background"
                aria-label={`Apagar a conversa ${c.title}`}
                title="Apagar a conversa (os assets ficam no projeto)"
                onClick={async () => {
                  if (!(await confirm({ title: 'Apagar esta conversa?', description: 'Os assets gerados continuam no projeto.', confirmLabel: 'Apagar', destructive: true }))) return
                  try {
                    await api.deleteChat(project.slug, c.id)
                    if (c.id === cid) select(null)
                    qc.invalidateQueries({ queryKey: ['chats', project.slug] })
                  } catch (e) {
                    toast.error(String(e instanceof Error ? e.message : e))
                  }
                }}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          {list.data?.length === 0 && <p className="px-2 py-1 text-xs text-muted-foreground">Nenhuma conversa ainda.</p>}
        </nav>
      </aside>
      {list.isLoading ? <LoadingState /> : cid ? (
        <Conversation key={cid} project={project} cid={cid} onNew={create} />
      ) : (
        <div className="flex items-center justify-center p-6">
          <EmptyState
            icon={<MessagesSquare />}
            title="Gere assets conversando"
            description="Peça o que precisa em linguagem natural: o assistente escolhe o pipeline, gera, mostra o resultado aqui e grava no projeto. Dá para pedir ajustes em seguida."
            action={<Button onClick={create}><Plus /> Começar uma conversa</Button>}
          />
        </div>
      )}
    </div>
  )
}

function Conversation({ project, cid, onNew }: { project: Project; cid: string; onNew: () => Promise<Chat> }) {
  const qc = useQueryClient()
  const info = useQuery({ queryKey: ['ai'], queryFn: api.ai, staleTime: 60_000 })
  const q = useQuery({
    queryKey: ['chat', project.slug, cid],
    queryFn: () => api.chat(project.slug, cid),
    refetchInterval: (x) => (x.state.data?.running ? 2_000 : false),
  })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs({ limit: 300 }), enabled: !!q.data?.running })
  const [text, setText] = useState('')
  const [choice, setChoice] = useState<ModelChoice | null>(null)
  const end = useRef<HTMLDivElement>(null)
  const c = q.data
  const cur: ModelChoice = choice ?? { provider: c?.provider ?? 'local', model: c?.model ?? '', effort: c?.effort ?? 'medium' }
  const running = !!c?.running
  const activeJob = running ? (jobs.data ?? []).find((j) => j.source === 'chat' && j.request.project === project.slug && (j.status === 'running' || j.status === 'queued')) : undefined

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [c?.messages.length, running])

  const send = async (msg = text) => {
    if (!msg.trim() || running) return
    setText('')
    try {
      await api.sendChat(project.slug, cid, { text: msg, provider: cur.provider === 'auto' ? undefined : cur.provider, model: cur.model, effort: cur.effort })
      qc.invalidateQueries({ queryKey: ['chat', project.slug, cid] })
      qc.invalidateQueries({ queryKey: ['chats', project.slug] })
    } catch (e) {
      setText(msg)
      toast.error(String(e instanceof Error ? e.message : e))
    }
  }

  // agrupa resultados de ferramenta com a chamada que os pediu
  const results = useMemo(() => {
    const m = new Map<string, ChatMessage>()
    for (const x of c?.messages ?? []) if (x.role === 'tool' && x.tool_call_id) m.set(x.tool_call_id, x)
    return m
  }, [c?.messages])

  if (!c) return q.isError ? <p className="p-6 text-sm text-destructive">{String(q.error)}</p> : <LoadingState />

  return (
    <div className="flex min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <h2 className="mr-auto truncate text-sm font-medium">{c.title}</h2>
        <ModelPicker info={info.data} value={cur} onChange={setChoice} size="sm" />
        <Button size="sm" variant="ghost" className="md:hidden" onClick={() => onNew()} aria-label="Nova conversa"><Plus /></Button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-4" aria-live="polite">
        <div className="mx-auto max-w-3xl space-y-4">
          {c.messages.length === 0 && (
            <div className="space-y-3 py-8 text-center">
              <Bot className="mx-auto size-8 text-muted-foreground/70" />
              <p className="text-sm text-muted-foreground">O que vamos criar em <b>{project.name}</b>? O estilo do projeto é aplicado sozinho.</p>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="rounded-full border px-3 py-1 text-xs hover:bg-muted" onClick={() => send(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {c.messages.map((m, i) => <MessageView key={i} m={m} results={results} />)}
          {running && (
            <div className="flex items-start gap-2 text-sm text-muted-foreground">
              <Bot className="mt-0.5 size-4 shrink-0" />
              <div className="flex-1 space-y-1.5">
                <span className="inline-flex items-center gap-1.5"><Loader2 className="size-3.5 animate-spin" /> {activeJob ? `gerando (${pipelineTitle(activeJob.request.pipeline)})…` : 'pensando…'}</span>
                {activeJob && (
                  <div className="flex max-w-sm items-center gap-2">
                    <Progress value={activeJob.progress * 100} className="flex-1" />
                    <span className="text-xs tabular-nums">{Math.round(activeJob.progress * 100)}%</span>
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={end} />
        </div>
      </div>
      <form
        className="border-t p-3"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
            rows={2}
            placeholder={running ? 'Respondendo… (dá para escrever a próxima)' : 'Peça um asset, um ajuste, uma variação… (Enter envia, Shift+Enter quebra linha)'}
            className="max-h-40 min-h-12 resize-y"
            aria-label="Mensagem"
          />
          {running ? (
            <Button type="button" variant="outline" size="icon" aria-label="Parar" title="Parar" onClick={() => api.cancelChat(project.slug, cid)}>
              <Square />
            </Button>
          ) : (
            <Button type="submit" size="icon" aria-label="Enviar" title="Enviar" disabled={!text.trim()}>
              <ArrowUp />
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

function MessageView({ m, results }: { m: ChatMessage; results: Map<string, ChatMessage> }) {
  if (m.role === 'user') {
    return (
      <div className="flex justify-end gap-2">
        <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-primary/15 px-3 py-2 text-sm whitespace-pre-wrap">{m.text}</div>
        <User className="mt-1 size-4 shrink-0 text-muted-foreground" />
      </div>
    )
  }
  if (m.role === 'error') {
    return <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{m.text}</div>
  }
  if (m.role === 'tool') return null // mostrado junto da chamada
  return (
    <div className="flex gap-2">
      <Bot className="mt-1 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-2">
        {m.text && <div className="text-sm leading-relaxed whitespace-pre-wrap"><RichText text={m.text} /></div>}
        {(m.tool_calls ?? []).map((tc) => <ToolCall key={tc.id} call={tc} result={results.get(tc.id)} />)}
        {m.model && !m.tool_calls?.length && <div className="text-xs text-muted-foreground/70">{m.model}</div>}
      </div>
    </div>
  )
}

/** **negrito** e `código` (o suficiente para as respostas curtas do assistente). */
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return <>{parts.map((p, i) => p.startsWith('**') ? <b key={i}>{p.slice(2, -2)}</b> : p.startsWith('`') ? <code key={i} className="rounded bg-muted px-1 text-[0.85em]">{p.slice(1, -1)}</code> : p)}</>
}

const TOOL_LABEL: Record<string, string> = {
  run_pipeline: 'rodou', pipeline_params: 'consultou os parâmetros de', list_pipelines: 'listou os pipelines',
  list_assets: 'buscou assets', view_asset: 'olhou', project_info: 'leu o estilo do projeto',
}

function ToolCall({ call, result }: { call: { id: string; name: string; input: Record<string, unknown> }; result?: ChatMessage }) {
  const [open, setOpen] = useState(false)
  const asset = result?.data?.asset
  const target = call.name === 'run_pipeline' ? pipelineTitle(String(call.input.pipeline ?? '')) : call.name === 'pipeline_params' ? pipelineTitle(String(call.input.pipeline ?? ''))
    : call.name === 'view_asset' ? String(call.input.ref ?? '') : ''
  return (
    <div className="space-y-2">
      <button className={cn('flex items-center gap-1.5 text-xs', result?.is_error ? 'text-red-300' : 'text-muted-foreground hover:text-foreground')} onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        <Wrench className="size-3" />
        {TOOL_LABEL[call.name] ?? call.name} {target}
        {!result && <Loader2 className="size-3 animate-spin" />}
        {result?.is_error && ' — falhou'}
      </button>
      {open && (
        <div className="space-y-1 pl-5">
          <pre className="max-h-40 overflow-auto rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap">{JSON.stringify(call.input, null, 2)}</pre>
          {result?.result && <pre className={cn('max-h-48 overflow-auto rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap', result.is_error && 'text-red-300')}>{result.result}</pre>}
        </div>
      )}
      {!open && result?.is_error && result.result && <p className="line-clamp-2 pl-5 text-xs text-red-300/90">{result.result}</p>}
      {asset && <AssetCard a={asset} />}
    </div>
  )
}

function AssetCard({ a }: { a: ChatAsset }) {
  const src = a.preview ?? a.primary
  const mime = (a.preview ? a.preview_mime : a.primary_mime) ?? ''
  return (
    <div className="max-w-sm overflow-hidden rounded-lg border bg-card">
      <Link to={`/p/${a.project}/a/${a.slug}/${a.version}`} className={cn('flex aspect-square max-h-72 w-full items-center justify-center', checker)} aria-label={`Abrir ${a.name}`}>
        {src && mime.startsWith('image/') && <img src={src} alt={a.name} className="max-h-full max-w-full object-contain" style={{ imageRendering: a.kind === 'sprite' ? 'pixelated' : 'auto' }} />}
        {src && mime.startsWith('video/') && <video src={src} className="max-h-full max-w-full" autoPlay loop muted playsInline />}
        {(!src || (!mime.startsWith('image/') && !mime.startsWith('video/'))) && <span className="text-xs text-muted-foreground">{a.kind}</span>}
      </Link>
      {a.primary_mime?.startsWith('audio/') && <audio src={a.primary!} controls className="w-full" />}
      <div className="flex items-center gap-2 px-3 py-2 text-xs">
        <Link to={`/p/${a.project}/a/${a.slug}/${a.version}`} className="truncate font-medium hover:underline">{a.name}</Link>
        <span className="text-muted-foreground">v{a.version} · {a.kind}</span>
        {a.folder && <span className="ml-auto truncate text-muted-foreground">/{a.folder}</span>}
      </div>
    </div>
  )
}

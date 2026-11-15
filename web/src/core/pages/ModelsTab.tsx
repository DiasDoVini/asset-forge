// Aba Modelos das Opções: LLMs e outros modelos carregados fora do ComfyUI (Ollama para o assistente de prompt etc.).
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, Eye, HardDrive, MemoryStick, Trash2, Wrench, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/native-select'
import { cn } from '@/lib/utils'
import { fmtBytes, InstallButton, useInstall, useSetupTasks } from '../components/SetupTasks'
import { useConfirm } from '../components/ConfirmDialog'

const CAP: Record<string, { icon: ReactNode; label: string }> = {
  vision: { icon: <Eye className="size-3" />, label: 'visão' },
  tools: { icon: <Wrench className="size-3" />, label: 'ferramentas' },
  thinking: { icon: <Brain className="size-3" />, label: 'raciocínio' },
}

const same = (a: string, b: string) => a === b || a === `${b}:latest` || b === `${a}:latest`

export function ModelsTab({ extra }: { extra?: ReactNode }) {
  const { confirm: ask } = useConfirm()
  const qc = useQueryClient()
  const llm = useQuery({ queryKey: ['llm'], queryFn: api.llm, refetchInterval: 15_000 })
  const tasks = useSetupTasks()
  const install = useInstall([['llm'], ['local-llm']])
  const d = llm.data
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['llm'] })
    qc.invalidateQueries({ queryKey: ['local-llm'] })
  }
  // quando um download do Ollama termina, a lista de instalados muda
  const pulling = (tasks.data ?? []).some((t) => t.item.startsWith('ollama:') && t.status === 'running')

  const choose = async (model: string | null) => {
    try {
      qc.setQueryData(['llm'], await api.llmChoose(model))
      qc.invalidateQueries({ queryKey: ['local-llm'] })
      toast.success(model ? `Assistente de prompt usa ${model}` : 'Escolha automática do modelo')
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e))
    }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-medium">LLM local (Ollama)</h2>
          <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', d?.online ? 'bg-emerald-500/20 text-emerald-300' : 'bg-red-500/20 text-red-300')}>
            {!d ? '…' : d.online ? `Online · Ollama ${d.version ?? ''}` : 'Offline'}
          </span>
          <code className="text-xs text-muted-foreground">{d?.url}</code>
          {pulling && <span className="text-xs text-muted-foreground">baixando…</span>}
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          Usado pelo <b>assistente de prompt</b> (✨ nos campos de prompt) e pela tradução de buscas nas Creator Souls. Roda fora do ComfyUI, na mesma GPU:
          descarregue o modelo se precisar da VRAM para gerar.
        </p>
        {d && !d.online && (
          <p className="mb-3 text-sm text-amber-300">
            {d.detail} <Link to="/setup" className="underline underline-offset-2">Instalar o Ollama</Link>
          </p>
        )}

        {d?.online && (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Modelo do assistente:</span>
              <NativeSelect
                aria-label="Modelo do assistente"
                value={d.chosen ?? ''}
                disabled={!!d.env_model}
                onChange={(e) => choose(e.target.value || null)}
              >
                <option value="">Automático{d.active && !d.chosen ? ` (${d.active})` : ''}</option>
                {d.installed.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                {d.chosen && !d.installed.some((m) => same(m.name, d.chosen!)) && <option value={d.chosen}>{d.chosen} (não baixado)</option>}
              </NativeSelect>
              {d.env_model && <span className="text-xs text-muted-foreground">fixado por FORGE_LOCAL_LLM={d.env_model}</span>}
              {!d.chosen && !d.env_model && <span className="text-xs text-muted-foreground">projetos +18 usam um modelo "abliterated" se houver</span>}
              {d.detail && <span className="text-xs text-amber-300">{d.detail}</span>}
            </div>

            <h3 className="mb-2 text-xs font-medium text-muted-foreground uppercase">Baixados</h3>
            {d.installed.length === 0 && <p className="mb-3 text-sm text-muted-foreground">Nenhum modelo ainda: baixe um do catálogo abaixo.</p>}
            <div className="mb-4 divide-y rounded-md border">
              {d.installed.map((m) => {
                const run = d.running.find((r) => r.name === m.name)
                return (
                  <div key={m.name} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                    <span className="font-medium">{m.name}</span>
                    {d.active && same(d.active, m.name) && <Badge>em uso</Badge>}
                    <span className="text-xs text-muted-foreground">
                      <HardDrive className="mr-1 inline size-3" />{fmtBytes(m.size)}{m.params ? ` · ${m.params}` : ''}{m.quant ? ` · ${m.quant}` : ''}
                    </span>
                    {run && (
                      <span className="text-xs text-amber-300" title="Carregado na memória">
                        <MemoryStick className="mr-1 inline size-3" />na memória: {fmtBytes(run.vram)} de VRAM{run.size > run.vram ? ` + ${fmtBytes(run.size - run.vram)} RAM` : ''}
                      </span>
                    )}
                    <div className="ml-auto flex gap-1">
                      {run && (
                        <Button size="sm" variant="ghost" title="Tira da memória e libera a VRAM" onClick={async () => { await api.llmUnload(m.name); refresh() }}>
                          <Zap /> Descarregar
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        title="Apaga o modelo do disco"
                        onClick={async () => {
                          if (!(await ask({ title: `Apagar ${m.name}?`, description: `Libera ${fmtBytes(m.size)} no disco. Dá para baixar de novo depois.`, confirmLabel: 'Apagar', destructive: true }))) return
                          try {
                            await api.llmDelete(m.name)
                            if (d.chosen && same(d.chosen, m.name)) await api.llmChoose(null)
                            toast.success(`${m.name} apagado`)
                          } catch (e) {
                            toast.error(String(e instanceof Error ? e.message : e))
                          }
                          refresh()
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>

            <h3 className="mb-2 text-xs font-medium text-muted-foreground uppercase">Catálogo sugerido</h3>
            <div className="grid gap-2 md:grid-cols-3">
              {d.catalog.map((c) => {
                const have = d.installed.some((m) => same(m.name, c.id))
                return (
                  <div key={c.id} className={cn('flex flex-col rounded-md border p-3', c.recommended && 'border-primary/50')}>
                    <div className="mb-1 flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">{c.title}</span>
                      {c.recommended && <Badge>novo · recomendado</Badge>}
                      {c.nsfw && <Badge variant="secondary">+18</Badge>}
                    </div>
                    <code className="mb-1 text-xs text-muted-foreground">{c.id} · {c.size_gb} GB{c.released ? ` · ${c.released}` : ''}</code>
                    <div className="mb-2 flex flex-wrap gap-1">
                      {c.capabilities.map((k) => (
                        <span key={k} className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-xs">{CAP[k]?.icon}{CAP[k]?.label ?? k}</span>
                      ))}
                    </div>
                    <p className="mb-3 flex-1 text-xs text-muted-foreground">{c.description}</p>
                    {have ? (
                      <div className="flex items-center gap-2 text-xs">
                        <Badge variant="secondary">baixado</Badge>
                        {!(d.chosen && same(d.chosen, c.id)) && <Button size="xs" variant="ghost" onClick={() => choose(c.id)}>Usar no assistente</Button>}
                      </div>
                    ) : (
                      <InstallButton item={`ollama:${c.id}`} title={c.title} label={`Baixar (${c.size_gb} GB)`} tasks={tasks.data} onInstall={install} />
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </section>
      {d?.others && (
        <section className="rounded-lg border bg-card p-4">
          <h2 className="mb-1 text-sm font-medium">Outros modelos fora do ComfyUI</h2>
          <p className="mb-3 text-xs text-muted-foreground">
            O áudio (Stable Audio, ACE-Step) roda no ComfyUI 2D; o sfxr retrô e a edição de áudio rodam na CPU, sem modelo. Estes são os demais:
          </p>
          <div className="divide-y rounded-md border">
            {d.others.map((o) => (
              <div key={o.id} className="grid gap-1 px-3 py-2 text-sm md:grid-cols-[220px_1fr]">
                <div>
                  <div className="font-medium">{o.title}</div>
                  <span className={cn('text-xs', o.available ? 'text-emerald-300' : 'text-amber-300')}>{o.available ? 'disponível' : 'indisponível'}</span>
                </div>
                <div className="space-y-0.5 text-xs">
                  <div><span className="text-muted-foreground">Modelo:</span> <code>{o.model ?? '—'}</code> <span className="text-muted-foreground">({o.backend})</span></div>
                  <div className="text-muted-foreground">{o.detail}</div>
                  <div className="text-muted-foreground">Usado em: {o.used_by}</div>
                  <div className="text-muted-foreground">Configuração: <code>{o.env}</code></div>
                </div>
              </div>
            ))}
          </div>
          {d.others.some((o) => o.id === 'vision' && !o.available) && (
            <p className="mt-2 text-xs text-muted-foreground">Dica: o Qwen3.8 27B tem visão e serve também para a verificação visual.</p>
          )}
        </section>
      )}
      {extra}
    </div>
  )
}

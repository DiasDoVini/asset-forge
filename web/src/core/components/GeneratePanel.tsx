import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Cpu, Sparkles, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { api, type PipelineInfo, type Project } from '../api'
import { SchemaForm, type Values } from '../SchemaForm'
import { allFields } from '../registry'
import { PromptField } from './PromptField'
import { SoulRefsStrip } from './SoulAttach'
import { AssetPicker } from './AssetPicker'
import { CategorySelect, FolderSelect } from './Organize'
import { JobList } from './JobList'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { pipelineTitle } from '../i18n/pipelines'
import { NativeSelect } from '@/components/ui/native-select'

export function GeneratePanel({ project }: { project: Project }) {
  const [search, setSearch] = useSearchParams()
  const modalities = useQuery({ queryKey: ['modalities'], queryFn: api.modalities })
  const pipelines = useMemo(() => (modalities.data ?? []).flatMap((m) => m.pipelines), [modalities.data])
  const [pid, setPid] = useState<string>(search.get('pipeline') ?? '')
  const [values, setValues] = useState<Values>(() => (search.get('input') ? { input_asset: search.get('input')! } : {}))
  const [asset, setAsset] = useState('')
  const [folder, setFolder] = useState<string | undefined>(undefined) // undefined = automático (herda da entrada)
  const [category, setCategory] = useState<string | null | undefined>(undefined)
  const [formKey, setFormKey] = useState(0) // remonta campos não controlados após pré-preencher
  const [fromJob, setFromJob] = useState<{ id: string; error: string | null } | null>(null)
  const pipeline: PipelineInfo | undefined = pipelines.find((p) => p.id === pid) ?? pipelines[0]

  // "Editar e repetir": ?job=<id> preenche pipeline, parâmetros e asset de destino
  const jobId = search.get('job')
  useEffect(() => {
    if (!jobId) return
    api.job(jobId).then(
      (j) => {
        setPid(j.request.pipeline)
        setValues(j.request.params ?? {})
        setAsset(j.request.asset ?? '')
        setFolder((j.request as { folder?: string }).folder ?? undefined)
        setCategory((j.request as { category?: string }).category ?? undefined)
        setFormKey((k) => k + 1)
        setFromJob({ id: j.id, error: j.error })
        const s = new URLSearchParams(search)
        s.delete('job')
        s.set('pipeline', j.request.pipeline)
        setSearch(s, { replace: true })
      },
      (e) => toast.error(`Job ${jobId}: ${e}`),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  useEffect(() => {
    if (!pid && pipelines[0]) setPid(pipelines[0].id)
  }, [pid, pipelines])

  const choose = (id: string) => {
    setPid(id)
    setFromJob(null)
    setFormKey((k) => k + 1)
    // mantém entrada e prompt ao trocar de pipeline
    setValues((v) => Object.fromEntries(Object.entries(v).filter(([k]) => ['input_asset', 'prompt'].includes(k))))
    search.set('pipeline', id)
    setSearch(search, { replace: true })
  }

  const submit = useMutation({
    mutationFn: () => api.submitJob({ project: project.slug, pipeline: pipeline!.id, params: values, asset: asset || undefined,
      folder, category: category ?? undefined }),
    onSuccess: (j) => toast.success(`Job ${j.id} na fila`, { description: pipeline ? pipelineTitle(pipeline) : undefined }),
    onError: (e) => toast.error(String(e)),
  })

  const missing = (pipeline?.params_schema.required ?? []).filter((k) => values[k] === undefined || values[k] === '')
  const needsInput = pipeline?.requires_input && !values.input_asset
  const inputFields = Object.entries(allFields()).filter(([n, f]) => f.placement === 'input' && pipeline?.params_schema.properties?.[n])

  return (
    <div className="grid h-full grid-cols-1 overflow-auto md:grid-cols-[200px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_340px] xl:overflow-hidden">
      <nav aria-label="Pipelines" className="hidden border-b p-3 md:block md:overflow-auto md:border-r md:border-b-0">
        {(modalities.data ?? []).map((m) => (
          <div key={m.name} className="mb-4">
            <div className="px-2 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{m.title}</div>
            {m.pipelines.map((p) => (
              <button
                key={p.id}
                onClick={() => choose(p.id)}
                className={cn('flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted', pipeline?.id === p.id && 'bg-muted font-medium')}
              >
                {p.resources.includes('gpu') ? <Zap className="size-3.5 shrink-0 text-amber-500" /> : <Cpu className="size-3.5 shrink-0 text-sky-500" />}
                <span className="truncate">{pipelineTitle(p)}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>

      <section className="p-4 md:p-6 xl:overflow-auto">
        <div className="mb-4 md:hidden">
          <Label htmlFor="pipeline-select" className="mb-1.5">Pipeline</Label>
          <NativeSelect id="pipeline-select" wrapperClassName="w-full" value={pipeline?.id ?? ''} onChange={(e) => choose(e.target.value)}>
            {(modalities.data ?? []).map((m) => (
              <optgroup key={m.name} label={m.title}>
                {m.pipelines.map((p) => <option key={p.id} value={p.id}>{pipelineTitle(p)}</option>)}
              </optgroup>
            ))}
          </NativeSelect>
        </div>
        {/* a link asked for a pipeline this server does not know (e.g. added after it started): say so instead of
            silently showing another form */}
        {pid && modalities.data && !pipelines.some((p) => p.id === pid) && (
          <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
            O pipeline <code className="font-mono">{pid}</code> não está disponível neste servidor. Se ele foi adicionado
            agora há pouco, reinicie o servidor do Asset Forge (<code className="font-mono">forge serve</code>).
          </div>
        )}
        {pipeline && (
          <form
            className="mx-auto max-w-2xl space-y-5"
            onSubmit={(e) => {
              e.preventDefault()
              submit.mutate()
            }}
          >
            <div>
              <h2 className="text-lg font-semibold">{pipelineTitle(pipeline)}</h2>
              <p className="text-sm text-muted-foreground">{pipeline.description}</p>
            </div>

            {pipeline.accepts_input && (
              <div>
                <Label className="mb-1.5">Entrada{pipeline.requires_input && <span className="text-destructive">*</span>}</Label>
                <AssetPicker project={project.slug} value={values.input_asset as string | undefined} onChange={(v) => setValues({ ...values, input_asset: v })} />
              </div>
            )}

            {inputFields.map(([name, f]) => (
              <div key={name}>
                {f.render({
                  name,
                  schema: pipeline.params_schema.properties![name],
                  value: values[name],
                  set: (v) => {
                    const next = { ...values }
                    if (v === undefined) delete next[name]
                    else next[name] = v
                    setValues(next)
                  },
                  required: (pipeline.params_schema.required ?? []).includes(name),
                  project: project.slug,
                  root: pipeline.params_schema,
                  values,
                  setField: (field, v) => {
                    const next = { ...values }
                    if (v === undefined) delete next[field]
                    else next[field] = v
                    setValues(next)
                  },
                })}
              </div>
            ))}

            {fromJob && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs">
                Preenchido a partir do job <b>{fromJob.id}</b>. Ajuste o que quiser e execute de novo.
                {fromJob.error && <p className="mt-1 break-words text-destructive">Erro anterior: {fromJob.error}</p>}
              </div>
            )}

            <SchemaForm
              key={formKey}
              schema={pipeline.params_schema}
              value={values}
              onChange={setValues}
              hidden={['input_asset', ...inputFields.flatMap(([n, f]) => [n, ...f.hides])]}
              custom={{
                prompt: ({ value, set, schema, required }) => (
                  <div className="grid gap-2">
                    <PromptField project={project.slug} pipeline={pipeline.id} value={(value as string | undefined) ?? ''} set={set}
                      label="Prompt" description={schema.description} required={required} />
                    {(pipeline.id === '2d.soul' || pipeline.id === '2d.image2d') && (
                      <SoulRefsStrip project={project} prompt={String(value ?? '')}
                        picked={pipeline.id === '2d.soul' ? ((values.references as string[] | undefined) ?? []) : undefined}
                        onPick={pipeline.id === '2d.soul' ? (r) => {
                          const cur = (values.references as string[] | undefined) ?? []
                          const next = cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r].slice(-2)
                          setValues({ ...values, references: next })
                        } : undefined} />
                    )}
                  </div>
                ),
                ...Object.fromEntries(Object.entries(allFields()).map(([k, f]) => [k, (ctx: Parameters<typeof f.render>[0]) => f.render({ ...ctx, project: project.slug })])),
                mask: ({ value, set }) => (
                  <div>
                    <Label className="mb-1.5">Máscara <span className="text-xs font-normal text-muted-foreground">branco = área editável (pinte no editor do asset)</span></Label>
                    <AssetPicker project={project.slug} value={value as string | undefined} onChange={set} placeholder="sem máscara (imagem toda)" />
                  </div>
                ),
                reference_assets: ({ value, set }) => {
                  const refs = (value as string[] | undefined) ?? []
                  return (
                    <div>
                      <Label className="mb-1.5">Referências extras <span className="text-xs font-normal text-muted-foreground">até 2</span></Label>
                      <div className="grid gap-2">
                        {[0, 1].map((i) => (
                          <AssetPicker
                            key={i}
                            project={project.slug}
                            value={refs[i]}
                            placeholder={`referência ${i + 1}`}
                            onChange={(v) => {
                              const next = [...refs]
                              if (v) next[i] = v
                              else next.splice(i, 1)
                              set(next.filter(Boolean).length ? next.filter(Boolean) : undefined)
                            }}
                          />
                        ))}
                      </div>
                    </div>
                  )
                },
              }}
            />

            {pipeline.same_asset ? (
              <div className="grid gap-1.5">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={asset !== ''} onChange={(e) => setAsset(e.target.checked ? `${String(values.input_asset ?? 'asset').split('@')[0].split(/[\\/]/).pop()}-variacao` : '')} />
                  Salvar como <b>novo asset</b> (variação derivada)
                  <span className="text-xs text-muted-foreground">— desmarcado: nova versão do asset de entrada</span>
                </label>
                {asset !== '' && <Input value={asset} onChange={(e) => setAsset(e.target.value)} placeholder="nome do novo asset" />}
              </div>
            ) : (
              <div>
                <Label className="mb-1.5">Nome do asset <span className="text-xs font-normal text-muted-foreground">opcional; se existir, vira nova versão</span></Label>
                <Input value={asset} onChange={(e) => setAsset(e.target.value)} placeholder="gerado a partir do prompt" />
              </div>
            )}

            <div className="grid gap-1.5">
              <Label>Organização <span className="text-xs font-normal text-muted-foreground">assets derivados herdam pasta e categoria da entrada</span></Label>
              <div className="flex flex-wrap gap-2">
                <FolderSelect project={project} value={folder} onChange={setFolder} allowKeep="Pasta: automática" />
                <CategorySelect project={project} value={category} onChange={setCategory} allowKeep="Categoria: automática" />
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={submit.isPending || missing.length > 0 || Boolean(needsInput)}>
                <Sparkles /> Executar
              </Button>
              {(missing.length > 0 || needsInput) && (
                <span role="alert" className="text-xs text-destructive">Preencha: {[...(needsInput ? ['entrada'] : []), ...missing].join(', ')}</span>
              )}
              <span className="ml-auto text-xs text-muted-foreground">estilo do projeto aplicado automaticamente</span>
            </div>
          </form>
        )}
      </section>

      <aside className="border-t p-3 md:col-span-2 xl:col-span-1 xl:overflow-auto xl:border-t-0 xl:border-l">
        <div className="px-1 pb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Jobs recentes</div>
        <JobList project={project.slug} compact limit={12} />
      </aside>
    </div>
  )
}

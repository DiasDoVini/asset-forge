import { useState } from 'react'
import { useNsfwBlur } from '../components/Nsfw'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, PackageOpen, Play, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, previewFile, primaryFile, type AssetFile, type AssetVersion, type Project } from '../api'
import { findViewer, panelsFor } from '../registry'
import { checker } from '../components/ImageViewer'
import { ExportDialog } from '../components/ExportDialog'
import { useOrganize } from '../components/FolderTree'
import { CategorySelect, FolderSelect } from '../components/Organize'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { ErrorState, LoadingState } from '../components/PageState'
import { pipelineTitle } from '../i18n/pipelines'

export default function AssetPage() {
  const { project: slug = '', asset = '', version } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const project = useQuery({ queryKey: ['project', slug], queryFn: () => api.project(slug) })
  useNsfwBlur(project.data)
  const versions = useQuery({ queryKey: ['versions', slug, asset], queryFn: () => api.versions(slug, asset) })
  const modalities = useQuery({ queryKey: ['modalities'], queryFn: api.modalities })
  const list = versions.data ?? []
  const current = list.find((v) => v.version === Number(version)) ?? list[list.length - 1]
  const [fileIdx, setFileIdx] = useState<string | null>(null)
  const [search, setSearch] = useSearchParams()

  const del = useMutation({
    mutationFn: (v?: number) => api.deleteAsset(slug, asset, v),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ['assets', slug] })
      qc.invalidateQueries({ queryKey: ['versions', slug, asset] })
      if (!v || list.length <= 1) nav(`/p/${slug}`)
      else nav(`/p/${slug}/a/${asset}`)
      toast.success('Movido para a lixeira (.forge/trash)')
    },
  })

  if (versions.isError) return <ErrorState error={versions.error} back={{ to: `/p/${slug}`, label: 'Voltar ao projeto' }} retry={() => versions.refetch()} />
  if (!current || !project.data) return <LoadingState label="Abrindo o asset…" />

  const file = current.files.find((f) => f.path === fileIdx) ?? primaryFile(current)!
  const tabPanels = panelsFor(current, 'tab')
  const inspectorPanels = panelsFor(current, 'inspector')
  const actions = (modalities.data ?? [])
    .flatMap((m) => m.pipelines)
    .filter((p) => p.accepts_input && (p.modality === current.modality || (p.modality === '3d' && current.modality === '2d')))

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-2.5">
        <Button size="icon-sm" variant="ghost" aria-label="Voltar ao projeto" title="Voltar ao projeto" render={<Link to={`/p/${slug}`} />}><ArrowLeft /></Button>
        <div className="min-w-0">
          <h1 className="truncate font-semibold">{current.name || current.slug}</h1>
          <p className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            <Link className="truncate hover:text-foreground hover:underline" to={`/p/${slug}`}>{project.data.name}</Link>
            <span aria-hidden>›</span>
            {current.folder ? (
              <Link className="hover:text-foreground hover:underline" to={`/p/${slug}/assets?f=${encodeURIComponent(current.folder)}`}>/{current.folder}</Link>
            ) : <span>/</span>}
            <span>· {current.ref} · {current.modality}/{current.kind}</span>
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm"><Play /> Usar como entrada</Button>} />
            <DropdownMenuContent align="end" className="w-56">
              {actions.map((p) => (
                <DropdownMenuItem key={p.id} onClick={() => nav(`/p/${slug}/gerar?pipeline=${p.id}&input=${current.ref}`)}>
                  {pipelineTitle(p)}
                  <span className="ml-auto text-xs text-muted-foreground">{p.modality}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <ExportDialog asset={current} project={project.data} trigger={<Button size="sm" variant="outline"><PackageOpen /> Exportar</Button>} />
          <Button size="icon-sm" variant="ghost" aria-label={`Baixar ${file.path}`} title={`Baixar ${file.path}`} render={<a href={file.url} download={`${current.slug}_v${current.version}_${file.path.split('/').pop()}`} />}>
            <Download />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="icon-sm" variant="ghost" aria-label="Apagar" title="Apagar versão ou asset"><Trash2 /></Button>} />
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => del.mutate(current.version)}>Apagar versão v{current.version}</DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => del.mutate(undefined)}>Apagar asset inteiro</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto lg:grid-cols-[minmax(0,1fr)_320px] lg:overflow-hidden">
        <div className="flex h-[75svh] min-h-0 min-w-0 flex-col overflow-hidden lg:h-auto">
          <Tabs
            value={tabPanels.some((p) => p.id === search.get('tab')) ? search.get('tab')! : 'view'}
            onValueChange={(v) => {
              const s = new URLSearchParams(search)
              if (v === 'view') s.delete('tab')
              else s.set('tab', String(v))
              setSearch(s, { replace: true })
            }}
            className="flex min-h-0 flex-1 flex-col gap-0"
          >
            {tabPanels.length > 0 && (
              <TabsList variant="line" className="border-b px-3">
                <TabsTrigger value="view">Visualizar</TabsTrigger>
                {tabPanels.map((p) => <TabsTrigger key={p.id} value={p.id}>{p.title}</TabsTrigger>)}
              </TabsList>
            )}
            <TabsContent value="view" className="min-h-0 flex-1">
              <FileViewer file={file} asset={current} project={project.data} />
            </TabsContent>
            {tabPanels.map((p) => (
              <TabsContent key={p.id} value={p.id} className="min-h-0 flex-1">
                <p.component asset={current} project={project.data} />
              </TabsContent>
            ))}
          </Tabs>
          <VersionStrip versions={list} current={current} slug={slug} />
        </div>

        <aside className="min-h-0 space-y-4 border-t p-4 text-sm lg:overflow-auto lg:border-t-0 lg:border-l">
          <Files files={current.files} active={file.path} onPick={setFileIdx} />
          <Separator />
          <Meta key={current.ref} asset={current} project={project.data} />
          {inspectorPanels.map((p) => (
            <section key={p.id} className="border-t pt-4">
              <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{p.title}</h3>
              <p.component asset={current} project={project.data} />
            </section>
          ))}
        </aside>
      </div>
    </div>
  )
}

function FileViewer({ file, asset, project }: { file: AssetFile; asset: AssetVersion; project: Project }) {
  const viewer = findViewer(file)
  if (!viewer) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        Sem visualizador para {file.mime}. <a className="ml-1 underline" href={file.url}>Abrir arquivo</a>
      </div>
    )
  }
  const V = viewer.component
  return <V key={file.url} url={file.url} file={file} asset={asset} project={project} />
}

function VersionStrip({ versions, current, slug }: { versions: AssetVersion[]; current: AssetVersion; slug: string }) {
  return (
    <nav aria-label="Versões" className="flex items-center gap-2 overflow-x-auto border-t p-2">
      <span className="shrink-0 px-1 text-xs text-muted-foreground">{versions.length} {versions.length === 1 ? 'versão' : 'versões'}</span>
      {versions.map((v) => {
        const pv = previewFile(v)
        return (
          <Link
            key={v.version}
            to={`/p/${slug}/a/${v.slug}/${v.version}`}
            className={cn('w-20 shrink-0 overflow-hidden rounded-md border text-center text-xs', v.version === current.version ? 'border-primary' : 'opacity-70 hover:opacity-100')}
            title={`${v.pipeline ? pipelineTitle(v.pipeline) : ''} ${v.prompt ?? ''}`}
            aria-label={`Versão ${v.version}`}
            aria-current={v.version === current.version ? 'page' : undefined}
          >
            <div className={cn('flex aspect-square items-center justify-center', checker)}>
              {pv && <img src={pv.url} className="max-h-full max-w-full" style={{ imageRendering: v.kind === 'sprite' ? 'pixelated' : 'auto' }} />}
            </div>
            <div className="truncate px-1 py-0.5">v{v.version} <span className="text-muted-foreground">{v.pipeline?.split('.')[1]}</span></div>
          </Link>
        )
      })}
    </nav>
  )
}

function Files({ files, active, onPick }: { files: AssetFile[]; active: string; onPick: (p: string) => void }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Arquivos</h3>
      <div className="space-y-1">
        {files.map((f) => (
          <button key={f.path} onClick={() => onPick(f.path)} className={cn('flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-muted', f.path === active && 'bg-muted')}>
            <span className="truncate">{f.path}</span>
            <Badge variant="outline" className="ml-auto">{f.role}</Badge>
          </button>
        ))}
      </div>
    </section>
  )
}

function Meta({ asset, project }: { asset: AssetVersion; project: Project }) {
  const qc = useQueryClient()
  const [tags, setTags] = useState(asset.tags.join(', '))
  const [notes, setNotes] = useState(asset.notes)
  const [name, setName] = useState(asset.name)
  const dirty = name !== asset.name || notes !== asset.notes || tags !== asset.tags.join(', ')
  const save = useMutation({
    mutationFn: () => api.patchAsset(project.slug, asset.slug, asset.version, { name, notes, tags: tags.split(',').map((t) => t.trim()).filter(Boolean) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['versions', project.slug, asset.slug] })
      qc.invalidateQueries({ queryKey: ['assets', project.slug] })
      toast.success('Detalhes salvos')
    },
    onError: (e) => toast.error(String(e instanceof Error ? e.message : e)),
  })
  const extra = asset.extra ?? {}
  const organize = useOrganize(project.slug)
  const h3 = 'text-xs font-medium tracking-wide text-muted-foreground uppercase'
  return (
    <>
      <section className="space-y-2">
        <h3 className={h3}>Organização</h3>
        <div className="grid grid-cols-[70px_1fr] items-center gap-2 text-xs">
          <span className="text-muted-foreground">Pasta</span>
          <FolderSelect project={project} value={asset.folder ?? ''} className="h-7 w-full"
            onChange={(v) => v !== undefined && organize([asset.slug], { folder: v }).then(() => toast.success(`Movido para /${v}`))} />
          <span className="text-muted-foreground">Categoria</span>
          <CategorySelect project={project} value={asset.category ?? null} className="h-7 w-full"
            onChange={(v) => v !== undefined && organize([asset.slug], v === null ? { clear_category: true } : { category: v })} />
        </div>
      </section>
      <Separator />
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (dirty) save.mutate()
        }}
      >
        <h3 className={h3}>Detalhes</h3>
        <div className="grid gap-1">
          <Label htmlFor="asset-name" className="text-xs">Nome</Label>
          <Input id="asset-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={asset.slug} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="asset-tags" className="text-xs">Tags</Label>
          <Input id="asset-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="separadas, por, vírgula" />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="asset-notes" className="text-xs">Notas</Label>
          <Textarea id="asset-notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          <Button type="submit" size="sm" disabled={!dirty || save.isPending}>Salvar</Button>
          {dirty && (
            <Button type="button" size="sm" variant="ghost" onClick={() => { setName(asset.name); setNotes(asset.notes); setTags(asset.tags.join(', ')) }}>
              Descartar
            </Button>
          )}
          {!dirty && <span className="text-xs text-muted-foreground">sem alterações</span>}
        </div>
      </form>
      <Separator />
      <details open className="group space-y-2 text-xs">
        <summary className={cn(h3, 'cursor-pointer list-none select-none')}>Geração <span className="normal-case">▾</span></summary>
        <dl className="grid grid-cols-[80px_1fr] gap-x-2 gap-y-1">
          <dt className="text-muted-foreground">Pipeline</dt><dd title={asset.pipeline ?? ''}>{asset.pipeline ? pipelineTitle(asset.pipeline) : '—'}</dd>
          {asset.seed != null && (<><dt className="text-muted-foreground">Seed</dt><dd className="tabular-nums">{asset.seed}</dd></>)}
          {asset.parent && (
            <>
              <dt className="text-muted-foreground">Origem</dt>
              <dd><Link className="underline" to={`/p/${project.slug}/a/${asset.parent.split('@')[0]}/${parseInt(asset.parent.split('@v')[1], 10)}`}>{asset.parent}</Link></dd>
            </>
          )}
          <dt className="text-muted-foreground">Criado</dt><dd>{new Date(asset.created).toLocaleString()}</dd>
        </dl>
        {asset.prompt && <p className="rounded bg-muted/50 p-2">{asset.prompt}</p>}
        {typeof extra.full_prompt === 'string' && (
          <details><summary className="cursor-pointer text-muted-foreground">prompt completo</summary><p className="mt-1">{extra.full_prompt}</p></details>
        )}
        <details>
          <summary className="cursor-pointer text-muted-foreground">parâmetros</summary>
          <pre className="mt-1 overflow-auto rounded bg-muted/50 p-2 text-xs">{JSON.stringify({ ...asset.params, ...extra }, null, 2)}</pre>
        </details>
      </details>
    </>
  )
}

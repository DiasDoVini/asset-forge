import { useMemo, useState } from 'react'
import { NewPixelButton } from '@/modalities/pixel'
import { NewSpriteButton } from '@/modalities/2d/sprite/NewSprite'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, Folder, FolderOpen, Scissors, Search, SlidersHorizontal, Upload, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AssetRow, type Project } from '../api'
import { AssetThumb } from './AssetThumb'
import { ImportDialog } from './ImportDialog'
import { DRAG_MIME, FolderTree, useFolders, useOrganize, type FolderSel } from './FolderTree'
import { CategoryDot, CategorySelect, FolderSelect, categoryOf } from './Organize'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { NativeSelect } from '@/components/ui/native-select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { EmptyState, LoadingState } from './PageState'

export function AssetGallery({ project }: { project: Project }) {
  const [search, setSearch] = useSearchParams()
  const f = search.get('f')
  const folder: FolderSel = f === null ? undefined : f === '/' ? '' : f
  const setFolder = (v: FolderSel) => {
    const s = new URLSearchParams(search)
    if (v === undefined) s.delete('f')
    else s.set('f', v === '' ? '/' : v)
    setSearch(s, { replace: true })
    setSelected(new Set())
  }
  const [q, setQ] = useState('')
  const [modality, setModality] = useState<string>('')
  const [kind, setKind] = useState<string>('')
  const [category, setCategory] = useState<string>('')
  const [showFilters, setShowFilters] = useState(false)
  const [recursive, setRecursive] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [drag, setDrag] = useState(false)
  const [pending, setPending] = useState<{ files: File[]; importer?: string } | null>(null) // aguardando o diálogo de importação
  const qc = useQueryClient()
  const nav = useNavigate()
  const organize = useOrganize(project.slug)
  const folders = useFolders(project.slug)
  // em "Todos" mostra tudo; numa pasta mostra só ela (ou com subpastas, se marcado)
  const assets = useQuery({
    queryKey: ['assets', project.slug, q, modality, kind, category, folder ?? '*', recursive],
    queryFn: () => api.assets(project.slug, { q, modality, kind, category, folder, recursive: folder ? recursive : undefined }),
  })
  const modalities = useQuery({ queryKey: ['modalities'], queryFn: api.modalities })
  const subfolders = useMemo(() => {
    if (folder === undefined || q) return []
    const all = folders.data?.folders ?? []
    return all.filter((x) => (folder === '' ? x.depth === 0 : x.path.startsWith(folder + '/') && x.depth === folder.split('/').length))
  }, [folders.data, folder, q])

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const out = []
      const { layeredUpload } = await import('@/modalities/2d/sprite/formats/layered')
      for (const f of files) {
        const { file, note } = await layeredUpload(f) // .pxo/.psd/.kra: camadas preservadas via .aseprite
        if (note) toast.warning(note)
        out.push(await api.uploadAsset(project.slug, file, { folder: folder || undefined }))
      }
      return out
    },
    onSuccess: (avs) => {
      qc.invalidateQueries({ queryKey: ['assets', project.slug] })
      qc.invalidateQueries({ queryKey: ['folders', project.slug] })
      const conv = avs.filter((a) => a.extra?.import).map((a) => `${a.slug} (${a.kind})`)
      toast.success(`Importado: ${avs.length} arquivo(s)`, { description: conv.length ? `Convertidos: ${conv.join(', ')}` : undefined })
    },
    onError: (e) => toast.error(String(e)),
  })

  const pick = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.multiple = true
    inp.onchange = () => inp.files && inp.files.length && setPending({ files: [...inp.files] })
    inp.click()
  }

  const pickSheet = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = 'image/png,image/webp,image/gif,image/jpeg,image/bmp'
    inp.onchange = () => inp.files && inp.files.length && setPending({ files: [inp.files[0]], importer: 'slice-editor' })
    inp.click()
  }

  const toggle = (slug: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(slug)) n.delete(slug)
      else n.add(slug)
      return n
    })

  const bulk = async (body: { folder?: string; category?: string; clear_category?: boolean }) => {
    const slugs = [...selected]
    const r = await organize(slugs, body)
    if (r) {
      toast.success(`${r.updated.length} asset(s) organizados`)
      setSelected(new Set())
    }
  }

  const crumbs = folder ? folder.split('/').map((name, i, arr) => ({ name, path: arr.slice(0, i + 1).join('/') })) : []

  const activeFilters = (kind ? 1 : 0) + (category ? 1 : 0)
  return (
    <div className="grid h-full grid-cols-1 md:grid-cols-[230px_minmax(0,1fr)]">
      <aside className="hidden border-r py-3 md:block">
        <FolderTree project={project.slug} current={folder} onSelect={setFolder} />
      </aside>

      <div
        className={cn('flex min-h-0 flex-col', drag && 'ring-2 ring-primary ring-inset')}
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes('Files')) return
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          setDrag(false)
          if (!e.dataTransfer.files.length) return
          e.preventDefault()
          // Shift ao soltar: abre o diálogo com as opções de cada formato
          if (e.shiftKey) setPending({ files: [...e.dataTransfer.files] })
          else upload.mutate([...e.dataTransfer.files])
        }}
      >
        {pending && <ImportDialog project={project} folder={folder || undefined} files={pending.files} initialImporter={pending.importer} onClose={() => setPending(null)} />}
        {/* busca, modalidade, filtros e ações */}
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          <div className="relative w-full sm:w-60">
            <Search className="absolute top-2 left-2 size-4 text-muted-foreground" aria-hidden />
            <Input className="pl-8" placeholder="Buscar nome, prompt, tag, pasta…" aria-label="Buscar assets" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Tabs value={modality} onValueChange={(v) => setModality(String(v))}>
            <TabsList className="max-w-full overflow-x-auto">
              {[{ name: '', title: 'Todos' }, ...(modalities.data ?? [])].map((m) => (
                <TabsTrigger key={m.name || 'all'} value={m.name}>{m.title}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Button size="sm" variant={showFilters || activeFilters > 0 ? 'secondary' : 'ghost'} aria-expanded={showFilters || activeFilters > 0}
            onClick={() => setShowFilters(!showFilters)}>
            <SlidersHorizontal /> Filtros{activeFilters > 0 && <Badge className="ml-0.5 h-4 min-w-4 px-1">{activeFilters}</Badge>}
          </Button>
          {/* pastas no mobile */}
          <div className="md:hidden">
            <FolderSelect project={project} value={folder} onChange={(v) => setFolder(v)} allowKeep="Todas as pastas" className="h-7" />
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <NewSpriteButton project={project} folder={folder || undefined} />
            <NewPixelButton project={project.slug} folder={folder || undefined} />
            <Button size="sm" variant="ghost" onClick={pickSheet} title="Escolha um PNG com vários sprites em grade: ele é importado e abre no editor de recorte">
              <Scissors /> Recortar folha…
            </Button>
            <Button size="sm" onClick={pick} disabled={upload.isPending} title="Importar arquivos (ou arraste para cá; Shift ao soltar abre as opções)">
              <Upload /> Importar{folder ? ' aqui' : ''}…
            </Button>
          </div>
        </div>
        {(showFilters || activeFilters > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 border-b px-4 py-1.5 text-xs">
            <NativeSelect size="sm" aria-label="Tipo" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Todos os tipos</option>
              <option value="image">Imagens</option>
              <option value="sprite">Sprites (pixel)</option>
              <option value="animation">Animações</option>
              <option value="mesh">Malhas 3D</option>
              <option value="print">Impressão 3D</option>
            </NativeSelect>
            <span className="mx-1 h-4 border-l" aria-hidden />
            <button aria-pressed={!category} onClick={() => setCategory('')} className={cn('rounded-full border px-2 py-0.5', !category ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
              Todas as categorias
            </button>
            {(project.categories ?? []).map((c) => (
              <button key={c.id} aria-pressed={category === c.id} onClick={() => setCategory(category === c.id ? '' : c.id)}
                className={cn('flex items-center gap-1.5 rounded-full border px-2 py-0.5', category === c.id ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
                <CategoryDot category={c} /> {c.name}
              </button>
            ))}
            <button aria-pressed={category === '__none__'} onClick={() => setCategory(category === '__none__' ? '' : '__none__')}
              className={cn('rounded-full border px-2 py-0.5 text-muted-foreground', category === '__none__' ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
              Sem categoria
            </button>
            {activeFilters > 0 && (
              <Button size="xs" variant="ghost" className="ml-auto" onClick={() => { setKind(''); setCategory('') }}>Limpar filtros</Button>
            )}
          </div>
        )}

        {/* breadcrumb + seleção */}
        <div className="flex min-h-11 flex-wrap items-center gap-2 px-4 py-2 text-sm">
          {selected.size > 0 ? (
            <div className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-primary/5 px-2 py-1">
              <span className="font-medium">{selected.size} selecionado(s)</span>
              <span className="text-muted-foreground">Mover para</span>
              <FolderSelect project={project} value={undefined} allowKeep="escolher pasta…" className="h-7"
                onChange={(v) => v !== undefined && bulk({ folder: v })} />
              <span className="text-muted-foreground">Categoria</span>
              <CategorySelect project={project} value={undefined} allowKeep="escolher…" className="h-7"
                onChange={(v) => v !== undefined && bulk(v === null ? { clear_category: true } : { category: v })} />
              <Button size="xs" variant="ghost" onClick={() => setSelected(new Set((assets.data ?? []).map((a) => a.slug)))}>Selecionar todos</Button>
              <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())} title="Limpar seleção" aria-label="Limpar seleção"><X /></Button>
            </div>
          ) : (
            <>
              <button className="text-muted-foreground hover:text-foreground" onClick={() => setFolder(undefined)}>
                {folder === undefined ? <b className="text-foreground">Todos os assets</b> : 'Todos'}
              </button>
              {folder === '' && (<><ChevronRight className="size-3.5 text-muted-foreground" /><b>Sem pasta</b></>)}
              {crumbs.map((c, i) => (
                <span key={c.path} className="flex items-center gap-2">
                  <ChevronRight className="size-3.5 text-muted-foreground" />
                  {i === crumbs.length - 1 ? <b>{c.name}</b> : (
                    <button className="text-muted-foreground hover:text-foreground" onClick={() => setFolder(c.path)}>{c.name}</button>
                  )}
                </span>
              ))}
              {folder && (
                <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                  <Switch checked={recursive} onCheckedChange={setRecursive} /> incluir subpastas
                </label>
              )}
            </>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 pb-6">
          {assets.isLoading && <LoadingState />}
          {assets.data?.length === 0 && subfolders.length === 0 && (
            <EmptyState
              className="mt-6"
              icon={<FolderOpen />}
              title={activeFilters > 0 || q ? 'Nada com esses filtros' : folder !== undefined ? 'Pasta vazia' : 'Nenhum asset ainda'}
              description={activeFilters > 0 || q ? 'Tente outra busca ou limpe os filtros.' : <>Gere na aba <Link className="underline" to={`/p/${project.slug}/gerar`}>Gerar</Link>,
                converse na aba <Link className="underline" to={`/p/${project.slug}/chat`}>Chat</Link>, arraste arquivos aqui ou mova assets para esta pasta.</>}
            />
          )}
          <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
            {subfolders.map((sf) => (
              <button key={sf.path} onClick={() => setFolder(sf.path)}
                className="flex aspect-[4/3] flex-col items-center justify-center gap-1 rounded-lg border border-dashed bg-card/50 text-sm hover:border-primary/60"
                onDragOver={(e) => e.dataTransfer.types.includes(DRAG_MIME) && e.preventDefault()}
                onDrop={async (e) => {
                  e.preventDefault()
                  const slugs = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
                  if (slugs.length && (await organize(slugs, { folder: sf.path }))) toast.success(`${slugs.length} → /${sf.path}`)
                }}>
                <Folder className="size-10 text-amber-400/80" />
                <span className="max-w-full truncate px-2 font-medium">{sf.name}</span>
                <span className="text-xs text-muted-foreground">{sf.total} assets</span>
              </button>
            ))}
            {(assets.data ?? []).map((a) => (
              <AssetCard key={a.slug} a={a} project={project} selected={selected.has(a.slug)} selecting={selected.size > 0}
                showFolder={folder === undefined || recursive}
                onToggle={() => toggle(a.slug)} onOpen={() => nav(`/p/${project.slug}/a/${a.slug}`)}
                dragSlugs={selected.has(a.slug) ? [...selected] : [a.slug]} onFolder={(p) => setFolder(p)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function AssetCard({ a, project, selected, selecting, showFolder, onToggle, onOpen, dragSlugs, onFolder }: {
  a: AssetRow; project: Project; selected: boolean; selecting: boolean; showFolder: boolean
  onToggle: () => void; onOpen: () => void; dragSlugs: string[]; onFolder: (p: string) => void
}) {
  const cat = categoryOf(project, a.category)
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_MIME, JSON.stringify(dragSlugs))
        e.dataTransfer.setData('text/plain', dragSlugs.join(', '))
        e.dataTransfer.effectAllowed = 'move'
      }}
      onClick={(e) => (selecting || e.ctrlKey || e.metaKey || e.shiftKey ? onToggle() : onOpen())}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter') onOpen()
        if (e.key === ' ') {
          e.preventDefault()
          onToggle()
        }
      }}
      tabIndex={0}
      role="link"
      aria-label={`${a.name || a.slug} (${a.kind}, v${a.version})`}
      className={cn('group relative cursor-pointer overflow-hidden rounded-lg border bg-card transition-colors outline-none hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring',
        selected && 'border-primary ring-1 ring-primary')}
    >
      {cat && <div className="absolute inset-x-0 top-0 z-10 h-1" style={{ background: cat.color }} />}
      <button
        onClick={(e) => {
          e.stopPropagation()
          onToggle()
        }}
        className={cn('absolute top-2 left-2 z-10 flex size-5 items-center justify-center rounded border bg-background/80',
          selected ? 'border-primary bg-primary text-primary-foreground' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100', selecting && 'opacity-100')}
        title="Selecionar (Ctrl+clique ou Espaço)"
        aria-label={selected ? 'Desmarcar' : 'Selecionar'}
        aria-pressed={selected}
      >
        {selected && <Check className="size-3.5" />}
      </button>
      <AssetThumb asset={a} className="aspect-square" />
      <div className="space-y-1 p-2">
        <div className="flex items-center gap-1.5">
          <CategoryDot category={cat} />
          <span className="truncate text-sm font-medium" title={a.name}>{a.name || a.slug}</span>
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Badge variant="outline" className="px-1 py-0">{a.modality}</Badge>
          <span>{a.kind}</span>
          <span className="ml-auto">v{a.version}{a.version_count > 1 && ` · ${a.version_count}`}</span>
        </div>
        {showFolder && a.folder && (
          <button className="flex max-w-full items-center gap-1 truncate text-xs text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation()
              onFolder(a.folder)
            }} title={`/${a.folder}`}>
            <Folder className="size-3 shrink-0" /> <span className="truncate">{a.folder}</span>
          </button>
        )}
      </div>
    </div>
  )
}

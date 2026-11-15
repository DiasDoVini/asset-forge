// Árvore de pastas do projeto: navegação, contagens, criar/renomear/remover e destino de
// arrastar-e-soltar de assets (dataTransfer "application/x-forge-assets" = JSON com slugs).
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Folder, FolderInput, FolderOpen, FolderPlus, Inbox, Layers, Pencil, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type FolderInfo } from '../api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useConfirm } from './ConfirmDialog'

export const DRAG_MIME = 'application/x-forge-assets'

/** undefined = todos; '' = sem pasta (raiz); 'A/B' = pasta */
export type FolderSel = string | undefined

export function useFolders(project: string) {
  return useQuery({ queryKey: ['folders', project], queryFn: () => api.folders(project) })
}

export function useOrganize(project: string) {
  const qc = useQueryClient()
  return async (slugs: string[], body: { folder?: string; category?: string; clear_category?: boolean }) => {
    try {
      const r = await api.organize(project, { slugs, ...body })
      qc.invalidateQueries({ queryKey: ['assets', project] })
      qc.invalidateQueries({ queryKey: ['folders', project] })
      qc.invalidateQueries({ queryKey: ['versions', project] })
      qc.invalidateQueries({ queryKey: ['project', project] })
      return r
    } catch (e) {
      toast.error(String(e))
    }
  }
}

export function FolderTree({ project, current, onSelect }: { project: string; current: FolderSel; onSelect: (f: FolderSel) => void }) {
  const { confirm: ask, prompt: askText } = useConfirm()
  const folders = useFolders(project)
  const qc = useQueryClient()
  const organize = useOrganize(project)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [over, setOver] = useState<string | null>(null)
  const list = useMemo(() => folders.data?.folders ?? [], [folders.data])
  const hasChildren = (path: string) => list.some((f) => f.path.startsWith(path + '/'))
  const visible = list.filter((f) => {
    const parts = f.path.split('/')
    for (let i = 1; i < parts.length; i++) if (collapsed.has(parts.slice(0, i).join('/'))) return false
    return true
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['folders', project] })
    qc.invalidateQueries({ queryKey: ['assets', project] })
    qc.invalidateQueries({ queryKey: ['project', project] })
  }
  const create = async (parent = '') => {
    const name = await askText(parent ? { title: 'Nova subpasta', label: `Dentro de “${parent}”`, placeholder: 'Nome' }
      : { title: 'Nova pasta', label: 'Nome (use / para subpastas)', placeholder: 'Personagens/Cavaleiro' })
    if (!name?.trim()) return
    const r = await api.createFolder(project, parent ? `${parent}/${name}` : name)
    refresh()
    onSelect(r.path)
  }
  const rename = async (f: FolderInfo) => {
    const to = await askText({ title: 'Renomear pasta', label: 'Novo caminho', defaultValue: f.path, confirmLabel: 'Renomear' })
    if (!to?.trim() || to === f.path) return
    try {
      const r = await api.renameFolder(project, f.path, to)
      refresh()
      if (current === f.path || current?.startsWith(f.path + '/')) onSelect(r.path + (current ?? '').slice(f.path.length))
      toast.success(`Pasta renomeada (${r.moved} assets)`)
    } catch (e) {
      toast.error(String(e))
    }
  }
  const remove = async (f: FolderInfo) => {
    if (!(await ask({ title: `Remover a pasta “${f.path}”?`, description: `Os ${f.total} assets dela vão para a pasta acima. Nada é apagado.`, confirmLabel: 'Remover', destructive: true }))) return
    const r = await api.deleteFolder(project, f.path)
    refresh()
    if (current === f.path || current?.startsWith(f.path + '/')) onSelect(r.moved_to || '')
  }

  const dropProps = (target: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (e.dataTransfer.types.includes(DRAG_MIME)) {
        e.preventDefault()
        setOver(target)
      }
    },
    onDragLeave: () => setOver(null),
    onDrop: async (e: React.DragEvent) => {
      e.preventDefault()
      setOver(null)
      const slugs = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
      if (slugs.length && (await organize(slugs, { folder: target }))) toast.success(`${slugs.length} asset(s) → /${target}`)
    },
  })

  const item = (key: string, label: React.ReactNode, sel: FolderSel, count?: number, extra?: { depth?: number; f?: FolderInfo; icon?: React.ReactNode; drop?: string }) => {
    const active = current === sel
    return (
      <div
        key={key}
        className={cn('group flex items-center gap-1 rounded-md pr-1 text-sm',
          active ? 'bg-sidebar-accent font-medium' : 'hover:bg-muted/60', over === extra?.drop && 'ring-2 ring-primary')}
        style={{ paddingLeft: 6 + (extra?.depth ?? 0) * 14 }}
        {...(extra?.drop !== undefined ? dropProps(extra.drop) : {})}
      >
        {extra?.f && hasChildren(extra.f.path) ? (
          <button className="shrink-0 text-muted-foreground" aria-label={collapsed.has(extra.f.path) ? 'Expandir' : 'Recolher'} aria-expanded={!collapsed.has(extra.f.path)} onClick={() => setCollapsed((s) => {
            const n = new Set(s)
            if (n.has(extra.f!.path)) n.delete(extra.f!.path)
            else n.add(extra.f!.path)
            return n
          })}>
            {collapsed.has(extra.f.path) ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </button>
        ) : <span className="w-3.5 shrink-0" />}
        <button className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left" onClick={() => onSelect(sel)}>
          {extra?.icon ?? (active ? <FolderOpen className="size-4 shrink-0 text-amber-400" /> : <Folder className="size-4 shrink-0 text-amber-400/80" />)}
          <span className="truncate">{label}</span>
        </button>
        {extra?.f && (
          <span className="hidden shrink-0 items-center group-focus-within:flex group-hover:flex">
            <button className="rounded p-0.5 hover:bg-background" title="Nova subpasta" aria-label={`Nova subpasta em ${label}`} onClick={() => create(extra.f!.path)}><FolderPlus className="size-3.5" /></button>
            <button className="rounded p-0.5 hover:bg-background" title="Renomear/mover" aria-label={`Renomear ${label}`} onClick={() => rename(extra.f!)}><Pencil className="size-3.5" /></button>
            <button className="rounded p-0.5 hover:bg-background" title="Remover pasta" aria-label={`Remover a pasta ${label}`} onClick={() => remove(extra.f!)}><Trash2 className="size-3.5" /></button>
          </span>
        )}
        {count !== undefined && <span className="shrink-0 text-xs tabular-nums text-muted-foreground group-focus-within:hidden group-hover:hidden">{count}</span>}
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-2 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Pastas
        <Button size="icon-xs" variant="ghost" onClick={() => create('')} title="Nova pasta" aria-label="Nova pasta"><FolderPlus /></Button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-auto px-1">
        {item('all', 'Todos os assets', undefined, folders.data?.total, { icon: <Layers className="size-4 shrink-0 text-muted-foreground" /> })}
        {item('root', 'Sem pasta', '', folders.data?.root_count, { icon: <Inbox className="size-4 shrink-0 text-muted-foreground" />, drop: '' })}
        <div className="my-1 border-t" />
        {visible.map((f) => item(f.path, f.name, f.path, f.total, { depth: f.depth, f, drop: f.path }))}
        {list.length === 0 && (
          <p className="px-2 py-2 text-xs text-muted-foreground">
            <FolderInput className="mb-1 size-4" />
            Crie pastas (ex.: <i>Personagens/Cavaleiro</i>) e arraste assets para elas. Animações e vistas herdam a pasta do sprite.
          </p>
        )}
      </div>
    </div>
  )
}

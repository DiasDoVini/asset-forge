// Controles de organização reutilizáveis: seletor de pasta (com "nova pasta") e de categoria.
import type { Category, Project } from '../api'
import { useFolders } from './FolderTree'
import { cn } from '@/lib/utils'
import { useConfirm } from './ConfirmDialog'
import { NativeSelect } from '@/components/ui/native-select'

export const NEW_FOLDER = '__new__'

export function categoryOf(project: Project, id: string | null | undefined): Category | undefined {
  return id ? project.categories?.find((c) => c.id === id) : undefined
}

/** <select> de pasta: '' = sem pasta. Opção "Nova pasta…" pede o caminho. */
export function FolderSelect({ project, value, onChange, className, allowKeep }: {
  project: Project
  value: string | undefined
  onChange: (v: string | undefined) => void
  className?: string
  allowKeep?: string // rótulo para "não alterar"/"automático" (value undefined)
}) {
  const { prompt: askText } = useConfirm()
  const folders = useFolders(project.slug)
  const paths = folders.data?.folders.map((f) => f.path) ?? project.folders ?? []
  return (
    <NativeSelect
      aria-label="Pasta"
      size={className?.includes('h-7') ? 'sm' : 'default'}
      className={cn(className?.replace('h-7', ''))}
      wrapperClassName={className?.includes('w-full') ? 'w-full' : undefined}
      value={value === undefined ? '__keep__' : value}
      onChange={async (e) => {
        const v = e.target.value
        if (v === '__keep__') return onChange(undefined)
        if (v === NEW_FOLDER) {
          const p = await askText({ title: 'Nova pasta', label: 'Caminho', placeholder: 'Personagens/Cavaleiro', confirmLabel: 'Usar' })
          if (p?.trim()) onChange(p.trim())
          return
        }
        onChange(v)
      }}
    >
      {allowKeep && <option value="__keep__">{allowKeep}</option>}
      <option value="">/ (sem pasta)</option>
      {value && !paths.includes(value) && <option value={value}>/{value} (nova)</option>}
      {paths.map((p) => (
        <option key={p} value={p}>{'  '.repeat(p.split('/').length - 1)}/{p.split('/').pop()}</option>
      ))}
      <option value={NEW_FOLDER}>+ Nova pasta…</option>
    </NativeSelect>
  )
}

export function CategorySelect({ project, value, onChange, className, allowKeep }: {
  project: Project
  value: string | null | undefined // undefined = não alterar; null = sem categoria
  onChange: (v: string | null | undefined) => void
  className?: string
  allowKeep?: string
}) {
  return (
    <NativeSelect
      aria-label="Categoria"
      size={className?.includes('h-7') ? 'sm' : 'default'}
      className={cn(className?.replace('h-7', ''))}
      wrapperClassName={className?.includes('w-full') ? 'w-full' : undefined}
      value={value === undefined ? '__keep__' : value ?? '__none__'}
      onChange={(e) => onChange(e.target.value === '__keep__' ? undefined : e.target.value === '__none__' ? null : e.target.value)}
    >
      {allowKeep && <option value="__keep__">{allowKeep}</option>}
      <option value="__none__">Sem categoria</option>
      {(project.categories ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </NativeSelect>
  )
}

export function CategoryDot({ category, className }: { category?: Category; className?: string }) {
  if (!category) return null
  return <span className={cn('inline-block size-2 shrink-0 rounded-full', className)} style={{ background: category.color }} title={category.name} />
}

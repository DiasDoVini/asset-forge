// Ponto de extensão da UI por modalidade.
//
// Cada pasta src/modalities/<nome>/index.ts(x) chama registerModality(...) ao ser importada
// (carregadas automaticamente por loadModalities via import.meta.glob).
//
//   registerModality({
//     name: '3d', title: '3D',
//     viewers: [{ id: 'mesh', match: (f) => f.mime.startsWith('model/'), component: MeshViewer }],
//     thumbnail: MeshThumb,
//     panels: [{ id: 'parts', title: 'Peças', placement: 'inspector', component: PartsPanel }],
//   })
import type { ComponentType, ReactNode } from 'react'
import type { AssetFile, AssetRow, AssetVersion, JsonSchema, Project } from './api'
import type { FieldCtx } from './SchemaForm'

export type ViewerProps = { url: string; file: AssetFile; asset: AssetVersion; project: Project }
export type ThumbnailProps = { url: string; asset: AssetRow }
export type PanelProps = { asset: AssetVersion; project: Project }

export type ViewerDef = {
  id: string
  match: (file: AssetFile) => boolean
  component: ComponentType<ViewerProps>
  priority?: number // maior vence quando mais de um viewer casa
}

export type PanelDef = {
  id: string
  title: string
  // inspector: coluna lateral do detalhe do asset; tab: aba ao lado do viewer
  placement: 'inspector' | 'tab'
  component: ComponentType<PanelProps>
  // restringe a assets desta modalidade (padrão: a modalidade que registrou)
  when?: (asset: AssetVersion) => boolean
}

export type RouteDef = { path: string; element: ReactNode }

// campo de parâmetro de pipeline com renderização própria (por nome do campo, no formulário de geração).
// placement 'input': aparece logo abaixo do seletor de entrada (ex.: imagens extras de referência)
// input-placed fields also get the whole form (values/setField): e.g. a method selector that edits option blocks
export type FieldRenderer = (ctx: FieldCtx & { project: string; values?: Record<string, unknown>; setField?: (name: string, v: unknown) => void; root?: JsonSchema }) => ReactNode
// hides: other params this field takes over (they are not rendered by the generic form)
export type FieldDef = FieldRenderer | { render: FieldRenderer; placement?: 'params' | 'input'; hides?: string[] }
type ResolvedField = { render: FieldRenderer; placement: 'params' | 'input'; hides: string[] }

export type ModalityDef = {
  name: string
  title?: string
  icon?: ComponentType<{ className?: string }>
  viewers?: ViewerDef[]
  thumbnail?: ComponentType<ThumbnailProps>
  panels?: PanelDef[]
  routes?: RouteDef[]
  fields?: Record<string, FieldDef>
}

const modalities = new Map<string, ModalityDef>()

export function registerModality(def: ModalityDef) {
  modalities.set(def.name, def)
}

export function getModality(name: string): ModalityDef | undefined {
  return modalities.get(name)
}

export function allModalities(): ModalityDef[] {
  return [...modalities.values()]
}

export function findViewer(file: AssetFile): ViewerDef | undefined {
  const all = allModalities().flatMap((m) => m.viewers ?? [])
  return all.filter((v) => v.match(file)).sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))[0]
}

export function panelsFor(asset: AssetVersion, placement: PanelDef['placement']): PanelDef[] {
  return allModalities().flatMap((m) =>
    (m.panels ?? [])
      .filter((p) => p.placement === placement)
      .filter((p) => (p.when ? p.when(asset) : asset.modality === m.name)),
  )
}

export function allFields(): Record<string, ResolvedField> {
  const out: Record<string, ResolvedField> = {}
  for (const m of allModalities())
    for (const [name, def] of Object.entries(m.fields ?? {}))
      out[name] = typeof def === 'function'
        ? { render: def, placement: 'params', hides: [] }
        : { render: def.render, placement: def.placement ?? 'params', hides: def.hides ?? [] }
  return out
}

export function allRoutes(): RouteDef[] {
  return allModalities().flatMap((m) => m.routes ?? [])
}

export async function loadModalities() {
  const mods = import.meta.glob('../modalities/*/index.{ts,tsx}')
  await Promise.all(Object.values(mods).map((load) => load()))
}

// Utilidades compartilhadas da modalidade pixel: config do projeto, spec do asset e envio de job com navegação.
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { api, pixelApi, type AssetVersion, type PixelConfig, type PixelSpec, type Project } from '@/core/api'
import { onForgeEvent } from '@/core/events'
import { DEFAULT_OUTLINE } from './render'

export function usePixelConfig(slug: string) {
  return useQuery({ queryKey: ['pixel-config', slug], queryFn: () => pixelApi.config(slug), staleTime: 60_000 })
}

export function fallbackSpec(w: number, h: number, name = 'custom'): PixelSpec {
  return {
    name, kind: 'part', canvas: [w, h], pivot: [Math.floor(w / 2), 0], safe_area: null, outline: 'optional', outline_color: DEFAULT_OUTLINE,
    alpha: 'binary', palette_group: null, max_colors: null, tileable: false, coverage: null, min_island: 1, anchor: 'bottom',
  }
}

const isSpec = (v: unknown): v is PixelSpec => !!v && typeof v === 'object' && Array.isArray((v as PixelSpec).canvas)

/** Spec do asset (extra.spec) com fallbacks: params.spec no config, depois uma spec genérica. */
export function assetSpec(asset: AssetVersion, config: PixelConfig | undefined, size?: [number, number]): { spec: PixelSpec; ref: string | PixelSpec } {
  const ex = asset.extra?.spec
  if (isSpec(ex)) return { spec: ex, ref: ex }
  const name = typeof asset.params?.spec === 'string' ? asset.params.spec : typeof ex === 'string' ? ex : null
  if (name && config?.specs[name]) return { spec: config.specs[name], ref: name }
  const s = fallbackSpec(size?.[0] ?? 16, size?.[1] ?? 16)
  return { spec: s, ref: s }
}

export function assetPaletteGroup(asset: AssetVersion, spec: PixelSpec): string | null {
  const p = asset.params?.palette_group
  return typeof p === 'string' && p ? p : spec.palette_group
}

/** Como useJobNav, mas aceita o corpo completo do job (nome, pasta...). */
export function useSubmitNav(project: Project) {
  const nav = useNavigate()
  const [pending, setPending] = useState<string | null>(null)
  useEffect(
    () =>
      onForgeEvent((e) => {
        if (e.type !== 'job' || e.job.id !== pending) return
        if (e.job.status === 'done' && e.job.result) {
          setPending(null)
          const [slug, v] = e.job.result.split('@v')
          nav(`/p/${project.slug}/a/${slug}/${parseInt(v, 10)}`)
          toast.success(`Pronto: ${e.job.result}`)
        } else if (e.job.status === 'error' || e.job.status === 'cancelled') {
          setPending(null)
          toast.error(e.job.error ?? 'job cancelado')
        }
      }),
    [pending, nav, project.slug],
  )
  const submit = async (body: Omit<Parameters<typeof api.submitJob>[0], 'project'>, msg = 'Salvando pixel art…') => {
    try {
      const j = await api.submitJob({ project: project.slug, ...body })
      setPending(j.id)
      toast.message(msg, { description: `job ${j.id}` })
    } catch (e) {
      toast.error(String(e))
    }
  }
  return { submit, busy: pending !== null }
}

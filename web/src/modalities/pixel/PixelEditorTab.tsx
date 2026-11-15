// Aba "Editar pixels" (carrega pixelmap.json do asset) e painel "Validadores" do inspector.
import { useQuery } from '@tanstack/react-query'
import type { PixelMap, PixelReport } from '@/core/api'
import type { PanelProps } from '@/core/registry'
import { useJobNav } from '@/core/useJobNav'
import { allowedColors, blankMap, buildLegend, gridToMap, normalizeMap, normHex } from './render'
import { imageToGrid, loadImage } from './importImage'
import { PixelEditor, type PixelSide } from './PixelEditor'
import { assetPaletteGroup, assetSpec, usePixelConfig } from './shared'
import { ValidationPanel } from './ValidationPanel'

export function PixelEditorTab({ asset, project }: PanelProps) {
  const cfg = usePixelConfig(project.slug)
  const src = asset.files.find((f) => f.role === 'source' && f.path.endsWith('.json')) ?? asset.files.find((f) => f.path === 'pixelmap.json')
  const sprite = asset.files.find((f) => f.role === 'primary' && f.mime.startsWith('image/'))
  const { submit, busy } = useJobNav(project, 'Salvando pixel art…')

  const mapQ = useQuery({
    queryKey: ['pixelmap', project.slug, asset.ref, cfg.data ? 1 : 0],
    enabled: !!cfg.data,
    queryFn: async (): Promise<{ map: PixelMap; note: string | null }> => {
      if (src) {
        const r = await fetch(src.url)
        if (!r.ok) throw new Error(`pixelmap.json: HTTP ${r.status}`)
        return { map: normalizeMap(await r.json()), note: null }
      }
      // sem mapa: converte o sprite.png (tamanho exato) encaixando na paleta
      const { spec } = assetSpec(asset, cfg.data)
      if (!sprite) return { map: blankMap(spec.canvas[0], spec.canvas[1], spec.outline_color), note: 'Asset sem pixelmap nem sprite: começando em branco.' }
      const img = await loadImage(sprite.url)
      const group = assetPaletteGroup(asset, spec)
      const g = imageToGrid(img, img.naturalWidth, img.naturalHeight, allowedColors(cfg.data!.palette, group), spec.anchor)
      const legend = buildLegend(cfg.data!.palette, undefined, spec.outline_color)
      return {
        map: gridToMap(g, legend, { outline: { auto: false, color: normHex(spec.outline_color) }, side: null, shade_L: 0.78 }),
        note: 'Asset sem pixelmap.json: o sprite foi convertido para a paleta (contorno automático desligado).',
      }
    },
  })

  if (cfg.isError) return <p className="p-4 text-sm text-destructive">Config de pixel indisponível: {String(cfg.error)}</p>
  if (mapQ.isError) return <p className="p-4 text-sm text-destructive">{String(mapQ.error)}</p>
  if (!cfg.data || !mapQ.data) return <p className="p-4 text-sm text-muted-foreground">carregando…</p>

  const { map, note } = mapQ.data
  const { spec, ref } = assetSpec(asset, cfg.data, [map.width, map.height])
  const ps = asset.params?.side
  const side: PixelSide = ps === 'L' || ps === 'both' ? ps : 'R'
  return (
    <div className="flex h-full min-h-0 flex-col">
      {note && <p className="border-b bg-amber-500/10 px-3 py-1 text-xs text-amber-500">{note}</p>}
      <div className="min-h-0 flex-1">
        <PixelEditor
          key={asset.ref}
          project={project}
          config={cfg.data}
          spec={spec}
          specRef={ref}
          initial={map}
          initialGroup={assetPaletteGroup(asset, spec)}
          initialSide={side}
          saving={busy}
          allowClean={!src}
          onSave={({ map: m, side: s, paletteGroup }) =>
            submit('pixel.map', { map: m, spec: ref, palette_group: paletteGroup, side: s, input_asset: asset.ref })}
        />
      </div>
    </div>
  )
}

export function ReportPanel({ asset }: PanelProps) {
  const rep = asset.extra?.validation as PixelReport | undefined
  const spec = asset.extra?.spec as { name?: string; canvas?: number[]; kind?: string } | undefined
  return (
    <div className="space-y-2">
      {spec?.canvas && (
        <p className="text-xs text-muted-foreground">
          spec <span className="font-mono text-foreground">{spec.name}</span> · {spec.kind} · {spec.canvas[0]}×{spec.canvas[1]} px
        </p>
      )}
      {rep && Array.isArray(rep.checks) ? <ValidationPanel report={rep} /> : <p className="text-xs text-muted-foreground">Sem relatório de validação.</p>}
    </div>
  )
}

// Página "Nova pixel art": escolhe spec, nome, pasta e grupo de paleta; opcionalmente importa uma imagem
// (convertida no navegador para o canvas e a paleta) e abre o editor. Salvar cria um asset novo.
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, FileUp, Grid2x2Plus } from 'lucide-react'
import { toast } from 'sonner'
import { api, type PixelMap, type PixelSpec, type Project } from '@/core/api'
import { AssetPicker } from '@/core/components/AssetPicker'
import { FolderSelect } from '@/core/components/Organize'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { imageToGrid, loadImage } from './importImage'
import { PixelEditor } from './PixelEditor'
import { allowedColors, blankMap, buildLegend, gridToMap, normHex, safeAreaTopLeft } from './render'
import { usePixelConfig, useSubmitNav } from './shared'

const parseRef = (v: string) => {
  const m = /^([a-z0-9][a-z0-9-]*)(?:@v?(\d+))?$/.exec(v)
  return m ? { slug: m[1], version: m[2] ? parseInt(m[2], 10) : undefined } : null
}

export function NewPixelPage() {
  const { project = '' } = useParams()
  const q = useQuery({ queryKey: ['project', project], queryFn: () => api.project(project) })
  if (q.isError) return <div className="p-8 text-destructive">{String(q.error)}</div>
  if (!q.data) return <div className="p-8 text-muted-foreground">Carregando…</div>
  return <NewPixelInner project={q.data} />
}

function NewPixelInner({ project }: { project: Project }) {
  const [search] = useSearchParams()
  const cfg = usePixelConfig(project.slug)
  const specNames = useMemo(() => Object.keys(cfg.data?.specs ?? {}), [cfg.data])
  const [specKey, setSpecKey] = useState<string | null>(search.get('spec'))
  const [name, setName] = useState('')
  const [folder, setFolder] = useState<string | undefined>(search.get('folder') ?? '')
  const [group, setGroup] = useState<string | null | undefined>(undefined) // undefined = o da spec
  const [importRef, setImportRef] = useState<string | undefined>()
  const [start, setStart] = useState<{ map: PixelMap; n: number } | null>(null)
  const [importing, setImporting] = useState(false)
  const { submit, busy } = useSubmitNav(project)

  const key = specKey && cfg.data?.specs[specKey] ? specKey : specNames[0]
  const spec: PixelSpec | undefined = key ? cfg.data?.specs[key] : undefined
  const blank = useMemo(() => (spec ? blankMap(spec.canvas[0], spec.canvas[1], spec.outline_color) : null), [spec])

  if (cfg.isError) return <div className="p-8 text-destructive">Config de pixel indisponível: {String(cfg.error)}</div>
  if (!cfg.data) return <div className="p-8 text-muted-foreground">Carregando config…</div>
  const config = cfg.data
  const grp = group === undefined ? (spec?.palette_group ?? null) : group
  const [cw, ch] = spec?.canvas ?? [16, 16]
  const initial = start?.map ?? blank

  const pickSpec = (k: string) => {
    if (start && !window.confirm('Trocar a spec descarta o desenho atual. Continuar?')) return
    setSpecKey(k)
    setGroup(undefined)
    setStart(null)
  }

  const importUrl = async (url: string) => {
    if (!spec) return
    if (start && !window.confirm('Importar substitui o desenho atual. Continuar?')) return
    setImporting(true)
    try {
      const img = await loadImage(url)
      // cabe na área segura; sem ela, deixa 1px de margem para o contorno automático
      const area: [number, number, number, number] = spec.safe_area
        ? safeAreaTopLeft(spec.safe_area, ch)
        : spec.outline !== 'none' && cw > 2 && ch > 2 ? [1, 1, cw - 2, ch - 2] : [0, 0, cw, ch]
      const g = imageToGrid(img, cw, ch, allowedColors(config.palette, grp), spec.anchor, area)
      const legend = buildLegend(config.palette, undefined, spec.outline_color)
      const map = gridToMap(g, legend, { outline: { auto: spec.outline !== 'none', color: normHex(spec.outline_color) }, side: null, shade_L: 0.78 })
      setStart((s) => ({ map, n: (s?.n ?? 0) + 1 }))
      toast.success(`Importado: ${img.naturalWidth}×${img.naturalHeight} → ${cw}×${ch}, cores encaixadas na paleta`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setImporting(false)
    }
  }

  const importAsset = async (v: string | undefined) => {
    setImportRef(v)
    if (!v) return
    const r = parseRef(v)
    if (!r) return toast.error('Use "Arquivo local" para importar uma imagem do computador.')
    try {
      const versions = await api.versions(project.slug, r.slug)
      const ver = (r.version ? versions.find((x) => x.version === r.version) : undefined) ?? versions[versions.length - 1]
      const f = ver?.files.find((x) => x.role === 'primary' && x.mime.startsWith('image/')) ?? ver?.files.find((x) => x.role === 'preview' && x.mime.startsWith('image/'))
      if (!f) return toast.error('Esse asset não tem imagem')
      await importUrl(f.url)
    } catch (e) {
      toast.error(String(e))
    }
  }

  const importLocal = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = 'image/*'
    inp.onchange = async () => {
      const f = inp.files?.[0]
      if (!f) return
      const url = URL.createObjectURL(f)
      await importUrl(url)
      URL.revokeObjectURL(url)
    }
    inp.click()
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2.5">
        <Link to={`/p/${project.slug}`} className="rounded p-1 hover:bg-muted" title="Voltar ao projeto"><ArrowLeft className="size-4" /></Link>
        <h1 className="flex items-center gap-2 font-semibold"><Grid2x2Plus className="size-4" /> Nova pixel art</h1>
        <label className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Spec</span>
          <select className="h-8 rounded-md border bg-transparent px-2 text-sm" value={key ?? ''} onChange={(e) => pickSpec(e.target.value)}>
            {specNames.map((k) => {
              const s = config.specs[k]
              return <option key={k} value={k}>{k} · {s.kind} · {s.canvas[0]}×{s.canvas[1]}</option>
            })}
          </select>
        </label>
        {spec && (
          <span className="text-xs text-muted-foreground">
            contorno {spec.outline} · alpha {spec.alpha}{spec.max_colors ? ` · máx. ${spec.max_colors} cores` : ''}{spec.tileable ? ' · tileável' : ''} · âncora {spec.anchor}
          </span>
        )}
      </header>
      {!spec || !initial ? (
        <p className="p-8 text-sm text-muted-foreground">Nenhuma spec definida no config de pixel do projeto.</p>
      ) : (
        <div className="min-h-0 flex-1">
          <PixelEditor
            key={`${key}-${start?.n ?? 0}`}
            project={project}
            config={config}
            spec={spec}
            specRef={key!}
            initial={initial}
            initialGroup={grp}
            onGroupChange={setGroup}
            saving={busy}
            allowClean
            saveLabel="Criar asset"
            onSave={({ map, side, paletteGroup }) => {
              if (!name.trim()) return toast.error('Dê um nome ao asset')
              submit({ pipeline: 'pixel.map', params: { map, spec: key, palette_group: paletteGroup, side }, name: name.trim(), folder: folder || undefined })
            }}
            sideExtra={
              <section className="space-y-2">
                <h4 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Asset</h4>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome (ex.: braço do cavaleiro)" />
                <FolderSelect project={project} value={folder} onChange={setFolder} className="w-full" />
                <div className="space-y-1.5 rounded-md border p-2">
                  <p className="text-xs text-muted-foreground">Ponto de partida (opcional): a imagem é amostrada no canvas {cw}×{ch} e cada pixel vai para a cor permitida mais próxima (encaixada na área segura).</p>
                  <AssetPicker project={project.slug} value={importRef} onChange={importAsset} placeholder="Importar de um asset" />
                  <Button size="sm" variant="outline" className="w-full" disabled={importing} onClick={importLocal}><FileUp /> Arquivo local</Button>
                </div>
              </section>
            }
          />
        </div>
      )}
    </div>
  )
}

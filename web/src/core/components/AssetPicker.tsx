import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Check, ImagePlus, Upload, X } from 'lucide-react'
import { api, previewFile, type AssetRow, type AssetVersion } from '../api'
import { AssetThumb } from './AssetThumb'
import { checker } from './ImageViewer'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

const parseRef = (v: string) => {
  const m = /^([a-z0-9][a-z0-9-]*)(?:@v?(\d+))?$/.exec(v)
  return m ? { slug: m[1], version: m[2] ? parseInt(m[2], 10) : undefined } : null
}

function VersionThumb({ v, className }: { v: AssetVersion; className?: string }) {
  const pv = previewFile(v)
  return (
    <div className={cn('flex items-center justify-center overflow-hidden', checker, className)}>
      {pv && <img src={pv.url} alt={v.ref} loading="lazy" className="max-h-full max-w-full object-contain" style={{ imageRendering: v.kind === 'sprite' ? 'pixelated' : 'auto' }} />}
    </div>
  )
}

/** Valor = ref "slug@v003" (asset do projeto, versão específica) ou caminho absoluto (arquivo enviado). */
export function AssetPicker({ project, value, onChange, modality, placeholder = 'Escolher asset' }: {
  project: string
  value?: string
  onChange: (v: string | undefined) => void
  modality?: string
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [browsing, setBrowsing] = useState<AssetRow | null>(null) // asset cujas versões estão abertas
  const assets = useQuery({ queryKey: ['assets', project, q, modality ?? ''], queryFn: () => api.assets(project, { q, modality }), enabled: open })
  const all = useQuery({ queryKey: ['assets', project, '', ''], queryFn: () => api.assets(project) })
  const parsed = value ? parseRef(value) : null
  const selected: AssetRow | undefined = parsed ? all.data?.find((a) => a.slug === parsed.slug) : undefined
  const isFile = value && !selected
  const versions = useQuery({
    queryKey: ['versions', project, browsing?.slug ?? selected?.slug],
    queryFn: () => api.versions(project, (browsing?.slug ?? selected?.slug)!),
    enabled: Boolean(browsing || (selected && parsed?.version && parsed.version !== selected.version)),
  })
  const selectedVersion = selected && parsed?.version ? versions.data?.find((v) => v.version === parsed.version) : undefined

  const close = () => {
    setOpen(false)
    setBrowsing(null)
  }

  const upload = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = 'image/*,.glb,.gltf,.obj,.stl,.ply,.3mf'
    inp.onchange = async () => {
      const f = inp.files?.[0]
      if (!f) return
      const r = await api.uploadTemp(project, f, f.name)
      onChange(r.path)
      close()
    }
    inp.click()
  }

  const pickAsset = (a: AssetRow) => {
    if (a.version_count > 1) setBrowsing(a)
    else {
      onChange(a.ref)
      close()
    }
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setOpen(true)} className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border p-1.5 text-left text-sm hover:bg-muted/50">
          {selected ? (
            <>
              {selectedVersion ? <VersionThumb v={selectedVersion} className="size-10 shrink-0 rounded" /> : <AssetThumb asset={selected} className="size-10 shrink-0 rounded" />}
              <span className="truncate">{value}</span>
              {parsed?.version && parsed.version !== selected.version && (
                <span className="ml-auto shrink-0 rounded bg-amber-500/15 px-1.5 text-xs text-amber-500">versão antiga</span>
              )}
            </>
          ) : isFile ? (
            <span className="truncate px-1">{value!.split(/[\\/]/).pop()}</span>
          ) : (
            <span className="flex items-center gap-2 px-1 text-muted-foreground"><ImagePlus className="size-4" />{placeholder}</span>
          )}
        </button>
        {value && <Button size="icon-sm" variant="ghost" onClick={() => onChange(undefined)}><X /></Button>}
      </div>
      <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : close())}>
        <DialogContent className="sm:max-w-3xl">
          {browsing ? (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  <Button size="icon-sm" variant="ghost" onClick={() => setBrowsing(null)} title="Voltar"><ArrowLeft /></Button>
                  {browsing.name || browsing.slug}
                </DialogTitle>
                <DialogDescription>{browsing.version_count} versões. Escolha qual usar como entrada.</DialogDescription>
              </DialogHeader>
              <div className="grid max-h-[60vh] grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 overflow-auto">
                {[...(versions.data ?? [])].reverse().map((v) => {
                  const isCur = value === v.ref
                  return (
                    <button
                      key={v.version}
                      type="button"
                      className={cn('overflow-hidden rounded-lg border text-left hover:border-primary', isCur && 'border-primary ring-1 ring-primary')}
                      title={v.prompt ?? ''}
                      onClick={() => {
                        onChange(v.ref)
                        close()
                      }}
                    >
                      <VersionThumb v={v} className="aspect-square" />
                      <div className="space-y-0.5 p-1.5 text-xs">
                        <div className="flex items-center gap-1 font-medium">
                          v{v.version}
                          {v.version === browsing.version && <span className="rounded bg-primary/15 px-1 text-xs text-primary">última</span>}
                          {isCur && <Check className="ml-auto size-3.5 text-primary" />}
                        </div>
                        <div className="truncate text-muted-foreground">{v.pipeline ?? 'importado'} · {v.kind}</div>
                        <div className="text-xs text-muted-foreground">{new Date(v.created).toLocaleString()}</div>
                      </div>
                    </button>
                  )
                })}
                {versions.isLoading && <p className="text-sm text-muted-foreground">Carregando versões…</p>}
              </div>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Escolher entrada</DialogTitle>
                <DialogDescription>Assets com várias versões abrem a lista de versões ao clicar.</DialogDescription>
              </DialogHeader>
              <div className="flex gap-2">
                <Input placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} />
                <Button variant="outline" onClick={upload}><Upload /> Enviar arquivo</Button>
              </div>
              <div className="grid max-h-[60vh] grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-3 overflow-auto">
                {(assets.data ?? []).map((a) => (
                  <button
                    key={a.slug}
                    type="button"
                    className={cn('overflow-hidden rounded-lg border text-left hover:border-primary', selected?.slug === a.slug && 'border-primary')}
                    onClick={() => pickAsset(a)}
                  >
                    <AssetThumb asset={a} className="aspect-square" />
                    <div className="flex items-center gap-1 p-1.5 text-xs">
                      <span className="truncate">{a.name || a.slug}</span>
                      <span className="ml-auto shrink-0 text-muted-foreground">
                        {a.version_count > 1 ? `${a.version_count} versões` : `v${a.version}`}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

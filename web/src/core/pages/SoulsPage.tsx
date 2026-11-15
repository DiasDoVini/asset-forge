import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, Loader2, Pin, PinOff, Plus, RefreshCw, Search, Sparkles, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Soul, type SoulRef } from '../api'
import { checker } from '../components/ImageViewer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useConfirm } from '../components/ConfirmDialog'
import { PageHeader } from '../components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

export function SoulStatus({ soul }: { soul: Soul }) {
  if (soul.progress)
    return (
      <div className="grid gap-1">
        <Progress value={soul.progress.value * 100} />
        <span className="text-xs text-muted-foreground">{soul.progress.message}</span>
      </div>
    )
  if (soul.status === 'error') return <span className="text-xs text-destructive">erro: {soul.error}</span>
  return <span className="text-xs text-muted-foreground">{soul.count} referências · {Object.keys(soul.categories).length} categorias</span>
}

function CreateSoulDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [name, setName] = useState('')
  const [folder, setFolder] = useState('')
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [license, setLicense] = useState('')
  const [describe, setDescribe] = useState(true)
  const m = useMutation({
    mutationFn: () => api.createSoul({
      name, folder, license, describe,
      include: include.split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
      exclude: exclude.split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ['souls'] })
      onOpenChange(false)
      nav(`/souls/${s.id}`)
    },
    onError: (e) => toast.error(String(e)),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Nova Creator Soul</DialogTitle>
          <DialogDescription>
            Aponte uma pasta de imagens de referência. Um catálogo (CATALOG.md/CSV com nomes) na pasta é detectado sozinho
            e melhora muito a busca. A construção roda em segundo plano.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5"><Label>Nome</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Terraria" /></div>
          <div className="grid gap-1.5"><Label>Pasta</Label><Input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="C:\…\VanillaImages" /></div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5"><Label>Incluir <span className="font-normal text-muted-foreground">(globs)</span></Label><Input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="Item_*, NPC_*" /></div>
            <div className="grid gap-1.5"><Label>Excluir</Label><Input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="UI/*, Gore_*" /></div>
          </div>
          <div className="grid gap-1.5"><Label>Origem / licença</Label><Input value={license} onChange={(e) => setLicense(e.target.value)} placeholder="ex.: Terraria © Re-Logic — uso pessoal/mods" /></div>
          <Label className="flex items-center gap-2 font-normal"><Switch checked={describe} onCheckedChange={setDescribe} /> Descrever o estilo com o modelo de visão local (Ollama)</Label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button disabled={!name.trim() || !folder.trim() || m.isPending} onClick={() => m.mutate()}>
            {m.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Criar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function SoulsPage() {
  const q = useQuery({ queryKey: ['souls'], queryFn: api.souls })
  const qc = useQueryClient()
  const [creating, setCreating] = useState(false)
  const importSoul = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = '.soul,.zip'
    inp.onchange = async () => {
      const f = inp.files?.[0]
      if (!f) return
      try {
        const s = await api.importSoul(f)
        toast.success(`Soul importada: ${s.name}`)
        qc.invalidateQueries({ queryKey: ['souls'] })
      } catch (e) {
        toast.error(String(e))
      }
    }
    inp.click()
  }
  return (
    <div className="h-full overflow-auto">
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-6">
      <PageHeader
        className="mb-0"
        title="Creator Souls"
        description="Coleções de referência que dão a um projeto o estilo de outra obra: paleta, tamanho de pixel, contorno e referências buscadas automaticamente para cada prompt. Anexe na aba Estilo do projeto."
        actions={<>
          <Button variant="outline" onClick={importSoul}><Upload /> Importar .soul</Button>
          <Button onClick={() => setCreating(true)}><Plus /> Nova Soul</Button>
        </>}
      />
      {q.isLoading && <LoadingState />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {q.data?.length === 0 && (
        <EmptyState icon={<Sparkles />} title="Nenhuma Soul ainda" description="Crie uma a partir de uma pasta de referências ou importe um arquivo .soul."
          action={<Button onClick={() => setCreating(true)}><Plus /> Nova Soul</Button>} />
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {(q.data ?? []).map((s) => (
          <Link key={s.id} to={`/souls/${s.id}`} className="overflow-hidden rounded-lg border hover:border-primary">
            <div className={cn('flex aspect-video items-center justify-center bg-[#181a22]')}>
              {s.has_cover ? <img src={`/api/souls/${s.id}/cover.png?v=${s.version}`} alt={s.name} className="max-h-full max-w-full" style={{ imageRendering: 'pixelated' }} />
                : <Sparkles className="size-8 text-muted-foreground" />}
            </div>
            <div className="space-y-1 p-3">
              <div className="flex items-center gap-2 font-medium">{s.name} {s.nsfw && <Badge variant="destructive">+18</Badge>}</div>
              <SoulStatus soul={s} />
            </div>
          </Link>
        ))}
        {q.data?.length === 0 && <p className="text-sm text-muted-foreground">Nenhuma Soul ainda — crie a partir de uma pasta de referências.</p>}
      </div>
      <CreateSoulDialog open={creating} onOpenChange={setCreating} />
    </div>
    </div>
  )
}

function RefCard({ soul, r, pinned, onPin }: { soul: string; r: SoulRef; pinned: boolean; onPin: () => void }) {
  return (
    <div className="group relative overflow-hidden rounded-md border" title={`${r.name || r.stem} · ${r.category} · ${r.source}`}>
      <div className={cn('flex aspect-square items-center justify-center', checker)}>
        <img src={`/api/souls/${soul}/refs/${r.id}.png`} alt={r.name} loading="lazy" className="max-h-[85%] max-w-[85%]" style={{ imageRendering: 'pixelated' }} />
      </div>
      <div className="truncate px-1.5 py-1 text-xs">{r.name || r.stem}<span className="text-muted-foreground"> · {r.category}</span></div>
      <button onClick={onPin} className={cn('absolute top-1 right-1 rounded bg-black/70 p-1', pinned ? 'text-amber-400' : 'hidden group-hover:block')} title={pinned ? 'desafixar' : 'fixar como referência de estilo'}>
        {pinned ? <Pin className="size-3.5" /> : <PinOff className="size-3.5" />}
      </button>
    </div>
  )
}

export function SoulDetailPage() {
  const { confirm: ask } = useConfirm()
  const { soul: sid = '' } = useParams()
  const qc = useQueryClient()
  const nav = useNavigate()
  const s = useQuery({ queryKey: ['soul', sid], queryFn: () => api.soul(sid), refetchInterval: (q) => (q.state.data?.progress ? 1000 : false) })
  const [query, setQuery] = useState('')
  const [cat, setCat] = useState('')
  const refs = useQuery({ queryKey: ['soul-search', sid, query, cat, s.data?.version], queryFn: () => api.soulSearch(sid, query, cat, 60), enabled: s.data?.status === 'ready' })
  const [style, setStyle] = useState<string | null>(null)
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof api.patchSoul>[1]) => api.patchSoul(sid, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['soul', sid] }); qc.invalidateQueries({ queryKey: ['souls'] }) },
    onError: (e) => toast.error(String(e)),
  })
  const d = s.data
  if (s.isError) return <ErrorState error={s.error} back={{ to: '/souls', label: 'Creator Souls' }} retry={() => s.refetch()} />
  if (!d) return <LoadingState />
  const pinned = new Set(d.pinned)
  const togglePin = (id: string) => patch.mutate({ pinned: pinned.has(id) ? d.pinned.filter((x) => x !== id) : [...d.pinned, id] })
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex flex-wrap items-start gap-3">
        <Button size="icon-sm" variant="ghost" aria-label="Voltar às Souls" title="Voltar às Souls" onClick={() => nav('/souls')}><ArrowLeft /></Button>
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold">{d.name} {d.nsfw && <Badge variant="destructive">+18</Badge>}</h1>
          <p className="text-sm text-muted-foreground">{d.description || d.source?.folder}</p>
          {d.license && <p className="text-xs text-muted-foreground">Origem: {d.license}</p>}
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" size="sm" disabled={!!d.progress} onClick={() => api.rebuildSoul(sid).then(() => qc.invalidateQueries({ queryKey: ['soul', sid] }))}><RefreshCw /> Reconstruir</Button>
          <Button variant="outline" size="sm" disabled={!!d.progress} title="legenda com o modelo de visão as referências sem nome no catálogo"
            onClick={() => api.captionSoul(sid).then(() => qc.invalidateQueries({ queryKey: ['soul', sid] }))}><Sparkles /> Legendar sem nome</Button>
          <Button variant="outline" size="sm" render={<a href={`/api/souls/${sid}/export`} download />}><Download /> Exportar .soul</Button>
          <Button variant="outline" size="sm" onClick={async () => {
            if (!(await ask({ title: `Mover “${d.name}” para a lixeira?`, description: 'A Soul será desanexada dos projetos.', confirmLabel: 'Mover para a lixeira', destructive: true }))) return
            await api.deleteSoul(sid)
            qc.invalidateQueries({ queryKey: ['souls'] })
            nav('/souls')
          }}><Trash2 /></Button>
        </div>
      </div>
      <SoulStatus soul={d} />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <div className="relative min-w-60 flex-1">
              <Search className="absolute top-2 left-2 size-4 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="teste a busca: “espada de fogo”, “slime azul”, “chefe olho”…" className="pl-8" />
            </div>
            <select value={cat} onChange={(e) => setCat(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm">
              <option value="">Todas as categorias</option>
              {Object.entries(d.categories).map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
            </select>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(110px,1fr))] gap-2">
            {(refs.data ?? []).map((r) => <RefCard key={r.id} soul={sid} r={r} pinned={pinned.has(r.id)} onPin={() => togglePin(r.id)} />)}
          </div>
          {refs.data?.length === 0 && <p className="text-sm text-muted-foreground">Nada encontrado.</p>}
        </div>

        <aside className="space-y-5">
          {d.has_cover && <img src={`/api/souls/${sid}/cover.png?v=${d.version}`} alt="capa" className="w-full rounded-md" style={{ imageRendering: 'pixelated' }} />}
          <section className="grid gap-1.5">
            <Label>Prompt de estilo <span className="font-normal text-muted-foreground">(anexado aos prompts dos projetos)</span></Label>
            <Textarea rows={5} value={style ?? d.style.prompt} onChange={(e) => setStyle(e.target.value)} />
            {style !== null && style !== d.style.prompt && (
              <Button size="sm" onClick={() => patch.mutate({ style: { prompt: style } }, { onSuccess: () => setStyle(null) })}>Salvar estilo</Button>
            )}
          </section>
          {d.loras.length > 0 && (
            <section className="space-y-1.5 text-xs">
              <h3 className="text-sm font-medium">LoRAs treinadas</h3>
              {d.loras.map((l) => (
                <div key={l.name} className="rounded border p-2">
                  <div className="font-medium">{l.name} <span className="font-normal text-muted-foreground">· {l.categories.join(', ')}</span></div>
                  <div className="truncate font-mono text-xs text-muted-foreground" title={l.file}>{l.file}</div>
                  <div className="text-muted-foreground">{l.base} · grade {l.cell_px}px{l.guide ? ' · guia de silhueta' : ''}</div>
                </div>
              ))}
              <p className="text-muted-foreground">“Gerar com Soul” usa a LoRA da categoria automaticamente (estratégia auto).</p>
            </section>
          )}
          <section className="space-y-1.5 text-xs">
            <h3 className="text-sm font-medium">DNA visual</h3>
            {d.pixel.is_pixel_art ? (
              <p>Pixel art · escala {d.pixel.scale}x · ~{d.pixel.art_size}px · ~{d.pixel.colors} cores/sprite{d.pixel.outline && <> · contorno <span className="inline-block size-3 rounded-sm border align-middle" style={{ background: d.pixel.outline }} /> {d.pixel.outline}</>}</p>
            ) : <p>Não é pixel art.</p>}
            <div className="flex flex-wrap gap-0.5">
              {d.palette.map((c) => <span key={c} className="size-4 rounded-sm border" style={{ background: c }} title={c} />)}
            </div>
            <table className="mt-2 w-full">
              <tbody>
                {Object.entries(d.pixel.sizes).slice(0, 12).map(([c, v]) => (
                  <tr key={c}><td className="text-muted-foreground">{c}</td><td className="text-right font-mono">{v.p10}–{v.median}–{v.p90}px</td></tr>
                ))}
              </tbody>
            </table>
          </section>
        </aside>
      </div>
    </div>
  )
}

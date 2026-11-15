// Animações de sprite: player/editor (viewer de animation.json), painel de animações de um
// sprite base e diálogo "Nova animação". Operações viram jobs (2d.anim_edit / 2d.anim_frame / 2d.animate)
// que criam novas versões; ao terminar, a UI navega para a versão nova.
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeftRight, ChevronLeft, ChevronRight, Copy, Film, FlipHorizontal2, Layers, Loader2, PackageOpen, Pause, Pencil, Play,
  Plus, Repeat, Sparkles, Trash2, Undo2,
} from 'lucide-react'
import { api, type AssetVersion, type Project } from '@/core/api'
import { useJobNav as useCoreJobNav } from '@/core/useJobNav'
import type { PanelProps, ViewerProps } from '@/core/registry'
import { checker } from '@/core/components/ImageViewer'
import { ExportDialog } from '@/core/components/ExportDialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ImageEditor } from './ImageEditor'

type AnimData = {
  base?: string | null
  action?: string
  preset?: string | null
  method?: string
  fps: number
  loop: boolean
  size: [number, number]
  pixel?: { cell: number } | null
  frames: { file: string; duration: number }[]
}

export const PRESETS: { id: string; title: string; frames: number; fps: number; hint: string }[] = [
  { id: 'idle', title: 'Parado', frames: 4, fps: 6, hint: 'respirando, piscando' },
  { id: 'walk', title: 'Andar', frames: 8, fps: 10, hint: 'ciclo de caminhada' },
  { id: 'run', title: 'Correr', frames: 8, fps: 12, hint: 'ciclo de corrida' },
  { id: 'attack', title: 'Ataque', frames: 6, fps: 12, hint: 'golpe com arma' },
  { id: 'jump', title: 'Pulo', frames: 6, fps: 10, hint: 'agacha, sobe, cai' },
  { id: 'hurt', title: 'Dano', frames: 4, fps: 10, hint: 'recebe golpe' },
  { id: 'death', title: 'Morte', frames: 6, fps: 8, hint: 'cai no chão' },
  { id: 'cast', title: 'Magia', frames: 6, fps: 10, hint: 'conjura feitiço' },
  { id: 'spin', title: 'Girar (item)', frames: 8, fps: 12, hint: 'rotação 360°' },
  { id: 'float', title: 'Flutuar (item)', frames: 6, fps: 8, hint: 'sobe e desce' },
]

/** Envia um job e navega para a versão criada quando ele termina. */
function useJobNav(project: Project) {
  const { submit: send, busy } = useCoreJobNav(project, 'Gerando com IA…')
  const submit = (pipeline: string, params: Record<string, unknown>, asset?: string) =>
    send(pipeline, params, asset, pipeline === '2d.anim_edit' ? 'Aplicando…' : undefined)
  return { submit, busy }
}

// ------------------------------------------------------------------ player / editor

export function AnimationViewer({ url, asset, project }: ViewerProps) {
  const dir = url.slice(0, url.lastIndexOf('/') + 1)
  const anim = useQuery({ queryKey: ['animation', url], queryFn: () => fetch(url).then((r) => r.json() as Promise<AnimData>) })
  const d = anim.data
  const [cur, setCur] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [onion, setOnion] = useState(false)
  const [zoom, setZoom] = useState<number | null>(null)
  const [aiText, setAiText] = useState('')
  const [dur, setDur] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const { submit, busy } = useJobNav(project)
  const n = d?.frames.length ?? 0
  const pixel = asset.extra?.pixel != null || (d ? Math.max(...d.size) <= 256 : false)

  // preload
  const urls = useMemo(() => (d ? d.frames.map((f) => dir + f.file) : []), [d, dir])
  useEffect(() => {
    urls.forEach((u) => {
      const i = new Image()
      i.src = u
    })
  }, [urls])

  useEffect(() => {
    if (!d || !playing || n < 2) return
    const t = window.setTimeout(() => setCur((c) => (c + 1 < n ? c + 1 : d.loop ? 0 : (setPlaying(false), c))), d.frames[cur].duration / speed)
    return () => window.clearTimeout(t)
  }, [d, cur, playing, speed, n])

  useEffect(() => setDur(d ? String(d.frames[cur]?.duration ?? '') : ''), [d, cur])

  if (!d) return <div className="p-6 text-muted-foreground">{anim.isError ? String(anim.error) : 'Carregando animação…'}</div>

  const fit = (() => {
    if (!box.current) return 2
    const r = box.current.getBoundingClientRect()
    const k = Math.min((r.width - 48) / d.size[0], (r.height - 48) / d.size[1])
    return pixel ? Math.max(1, Math.floor(k)) : Math.min(k, 2)
  })()
  const z = zoom ?? fit
  const sel = cur
  const edit = (ops: Record<string, unknown>[]) => submit('2d.anim_edit', { input_asset: asset.ref, ops })
  const select = (i: number) => {
    setPlaying(false)
    setCur(i)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* barra superior */}
      <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5 text-xs">
        <Button size="icon-sm" variant="ghost" onClick={() => setPlaying(!playing)} title="Play/Pause (espaço)">{playing ? <Pause /> : <Play />}</Button>
        <Button size="icon-sm" variant="ghost" onClick={() => select((cur - 1 + n) % n)} title="Frame anterior"><ChevronLeft /></Button>
        <Button size="icon-sm" variant="ghost" onClick={() => select((cur + 1) % n)} title="Próximo frame"><ChevronRight /></Button>
        <span className="w-14 text-center tabular-nums text-muted-foreground">{cur + 1}/{n}</span>
        <div className="flex w-32 items-center gap-2 px-1 text-muted-foreground" title="Velocidade de prévia">
          <span className="w-9 tabular-nums">{Math.round(d.fps * speed)}fps</span>
          <Slider min={0.25} max={3} step={0.25} value={[speed]} onValueChange={(v) => setSpeed(Array.isArray(v) ? v[0] : v)} />
        </div>
        <Button size="icon-sm" variant={onion ? 'secondary' : 'ghost'} onClick={() => setOnion(!onion)} title="Onion skin (frame anterior translúcido)"><Layers /></Button>
        <span className="mx-1 h-5 w-px bg-border" />
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => edit([{ op: 'flip_h' }])} title="Espelhar todos (ex.: andar para a esquerda)"><FlipHorizontal2 /> Espelhar</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => edit([{ op: 'reverse' }])} title="Inverter ordem"><Undo2 /> Inverter</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => edit([{ op: 'pingpong' }])} title="Vai e volta"><ArrowLeftRight /> Ping-pong</Button>
        <Button size="sm" variant="ghost" disabled={busy || speed === 1} onClick={() => edit([{ op: 'fps', value: Math.round(d.fps * speed) }])} title="Salvar a velocidade da prévia">
          Salvar {Math.round(d.fps * speed)}fps
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => edit([{ op: 'loop', value: !d.loop }])}><Repeat /> {d.loop ? 'Loop' : 'Uma vez'}</Button>
        <div className="ml-auto flex items-center gap-1">
          {busy && <Loader2 className="size-4 animate-spin text-sky-500" />}
          <select className="rounded border bg-transparent px-1 py-0.5" value={z} onChange={(e) => setZoom(Number(e.target.value))}>
            {[...new Set([z, 0.5, 1, 2, 3, 4, 6, 8, 12])].sort((a, b) => a - b).map((v) => <option key={v} value={v}>{Math.round(v * 100)}%</option>)}
          </select>
          <ExportDialog asset={asset} project={project} initial="2d.gif" trigger={<Button size="sm"><PackageOpen /> Exportar</Button>} />
        </div>
      </div>

      {/* palco */}
      <div
        ref={box}
        tabIndex={0}
        className={cn('relative min-h-0 flex-1 overflow-auto outline-none', checker)}
        onKeyDown={(e) => {
          if (e.key === ' ') { e.preventDefault(); setPlaying(!playing) }
          if (e.key === 'ArrowRight') select((cur + 1) % n)
          if (e.key === 'ArrowLeft') select((cur - 1 + n) % n)
        }}
      >
        <div className="flex min-h-full min-w-full items-center justify-center p-6">
          <div className="relative" style={{ width: d.size[0] * z, height: d.size[1] * z }}>
            {onion && n > 1 && (
              <img src={urls[(cur - 1 + n) % n]} className="absolute inset-0 size-full opacity-30" style={{ imageRendering: pixel ? 'pixelated' : 'auto', filter: 'hue-rotate(180deg)' }} />
            )}
            <img src={urls[cur]} className="absolute inset-0 size-full" style={{ imageRendering: pixel ? 'pixelated' : 'auto' }} draggable={false} />
          </div>
        </div>
        <div className="absolute bottom-2 left-2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-white/80">
          {d.size[0]}×{d.size[1]} · {n} frames · {d.loop ? 'loop' : 'uma vez'}{d.method ? ` · ${d.method}` : ''}{d.base ? ` · base ${d.base}` : ''}
        </div>
      </div>

      {/* tira de frames + ações do frame selecionado */}
      <div className="border-t">
        <div className="flex gap-1.5 overflow-x-auto p-2">
          {urls.map((u, i) => (
            <button
              key={u}
              onClick={() => select(i)}
              className={cn('shrink-0 overflow-hidden rounded border text-[10px]', i === cur ? 'border-primary ring-1 ring-primary' : 'opacity-75 hover:opacity-100')}
            >
              <div className={cn('flex size-16 items-center justify-center', checker)}>
                <img src={u} className="max-h-full max-w-full" style={{ imageRendering: pixel ? 'pixelated' : 'auto' }} />
              </div>
              <div className="px-1 tabular-nums">{i + 1} · {d.frames[i].duration}ms</div>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1 border-t px-2 py-1.5 text-xs">
          <span className="mr-1 font-medium">Frame {sel + 1}</span>
          <Button size="icon-sm" variant="ghost" disabled={busy || sel === 0} title="Mover para a esquerda" onClick={() => edit([{ op: 'move', frame: sel, to: sel - 1 }])}><ChevronLeft /></Button>
          <Button size="icon-sm" variant="ghost" disabled={busy || sel === n - 1} title="Mover para a direita" onClick={() => edit([{ op: 'move', frame: sel, to: sel + 1 }])}><ChevronRight /></Button>
          <Button size="icon-sm" variant="ghost" disabled={busy} title="Duplicar" onClick={() => edit([{ op: 'duplicate', frame: sel }])}><Copy /></Button>
          <Button size="icon-sm" variant="ghost" disabled={busy || n < 2} title="Apagar" onClick={() => edit([{ op: 'delete', frames: [sel] }])}><Trash2 /></Button>
          <Input className="h-7 w-20" type="number" min={10} step={10} value={dur} onChange={(e) => setDur(e.target.value)} title="Duração (ms)" />
          <Button size="sm" variant="ghost" disabled={busy || !dur || Number(dur) === d.frames[sel].duration} onClick={() => edit([{ op: 'duration', frames: [sel], ms: Number(dur) }])}>ms</Button>
          <FrameEditDialog asset={asset} project={project} url={urls[sel]} pixel={pixel} frame={sel} onSaved={(path) => edit([{ op: 'replace', frame: sel, image: path }])}
            trigger={<Button size="sm" variant="ghost" disabled={busy}><Pencil /> Pixels</Button>} />
          <form className="ml-auto flex min-w-60 flex-1 items-center gap-1" onSubmit={(e) => { e.preventDefault(); submit('2d.anim_frame', { input_asset: asset.ref, frame: sel, instruction: aiText }) }}>
            <Sparkles className="size-4 shrink-0 text-amber-500" />
            <Input className="h-7" value={aiText} onChange={(e) => setAiText(e.target.value)} placeholder="Refazer este frame com IA (vazio = só corrigir), ex.: “perna esquerda mais alta”" />
            <Button size="sm" type="submit" disabled={busy}>Refazer</Button>
          </form>
        </div>
      </div>
    </div>
  )
}

function FrameEditDialog({ asset, project, url, pixel, frame, onSaved, trigger }: {
  asset: AssetVersion; project: Project; url: string; pixel: boolean; frame: number
  onSaved: (path: string) => void; trigger: ReactElement
}) {
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent className="flex h-[85vh] flex-col sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Editar frame {frame + 1}</DialogTitle>
          <DialogDescription>Salvar substitui o frame e cria uma nova versão da animação.</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-hidden rounded border">
          {open && (
            <ImageEditor asset={asset} project={project} sourceUrl={url} pixel={pixel} hideAi
              onSaveBlob={async (blob) => {
                const up = await api.uploadTemp(project.slug, blob, `frame_${frame}.png`)
                onSaved(up.path)
                setOpen(false)
              }} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ------------------------------------------------------------------ direções (2d.view)

const VIEWS: { id: string; title: string }[] = [
  { id: 'front', title: 'Frente' },
  { id: 'back', title: 'Costas' },
  { id: 'right', title: 'Direita' },
  { id: 'left', title: 'Esquerda' },
  { id: 'front_right', title: '3/4 dir.' },
  { id: 'front_left', title: '3/4 esq.' },
  { id: 'back_right', title: 'Costas 3/4 dir.' },
  { id: 'back_left', title: 'Costas 3/4 esq.' },
]

export function ViewsPanel({ asset, project }: PanelProps) {
  const view = asset.extra?.view as string | undefined
  const base = asset.extra?.base as string | undefined
  const derived = useQuery({
    queryKey: ['assets', project.slug, 'views', asset.slug],
    queryFn: () => api.assets(project.slug, { base: asset.slug }),
    enabled: !view,
  })
  const [busyView, setBusyView] = useState<string | null>(null)
  const { submit } = useJobNav(project)

  if (view && base) {
    const [bslug, bv] = base.split('@v')
    return (
      <p className="text-xs text-muted-foreground">
        Vista <b>{VIEWS.find((v) => v.id === view)?.title ?? view}</b> de{' '}
        <Link className="underline" to={`/p/${project.slug}/a/${bslug}/${parseInt(bv, 10)}`}>{bslug}</Link>. Animações criadas aqui entram no sprite set do personagem.
      </p>
    )
  }
  const views = (derived.data ?? []).filter((r) => r.kind !== 'animation')
  const bySlug = (id: string) => views.find((r) => r.slug === `${asset.slug}-${id.replace('_', '-')}`)
  const gen = async (id: string) => {
    setBusyView(id)
    const mirror = id === 'left' ? bySlug('right') : id === 'right' ? bySlug('left') : id.endsWith('_left') ? bySlug(id.replace('_left', '_right')) : undefined
    await submit('2d.view', { input_asset: asset.ref, view: id, ...(mirror ? { mirror_from: mirror.ref } : {}) })
    setBusyView(null)
  }
  return (
    <div className="grid grid-cols-4 gap-1.5">
      {VIEWS.map((v) => {
        const r = bySlug(v.id)
        return r ? (
          <Link key={v.id} to={`/p/${project.slug}/a/${r.slug}`} className="overflow-hidden rounded border text-[10px] hover:border-primary">
            <div className={cn('flex aspect-square items-center justify-center', checker)}>
              {r.preview_url && <img src={r.preview_url} className="max-h-full max-w-full" style={{ imageRendering: r.kind === 'sprite' ? 'pixelated' : 'auto' }} />}
            </div>
            <div className="truncate px-1 py-0.5">{v.title}</div>
          </Link>
        ) : (
          <button key={v.id} onClick={() => gen(v.id)} disabled={busyView !== null}
            className="flex aspect-square flex-col items-center justify-center gap-1 rounded border border-dashed text-[10px] text-muted-foreground hover:border-primary hover:text-foreground"
            title={`Gerar vista ${v.title}`}>
            {busyView === v.id ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            {v.title}
          </button>
        )
      })}
    </div>
  )
}

// ------------------------------------------------------------------ painel do sprite base

export function AnimationsPanel({ asset, project }: PanelProps) {
  const anims = useQuery({
    queryKey: ['assets', project.slug, 'anims', asset.slug],
    queryFn: () => api.assets(project.slug, { modality: '2d', kind: 'animation', base: asset.slug }),
  })
  const list = anims.data ?? []
  return (
    <div className="space-y-2">
      {list.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma animação. Crie a partir deste sprite: andar, atacar, pular…</p>}
      <div className="grid grid-cols-3 gap-1.5">
        {list.map((a) => (
          <Link key={a.slug} to={`/p/${project.slug}/a/${a.slug}`} className="overflow-hidden rounded border text-[10px] hover:border-primary" title={a.prompt}>
            <div className={cn('flex aspect-square items-center justify-center', checker)}>
              {a.preview_url && <img src={a.preview_url} className="max-h-full max-w-full" style={{ imageRendering: 'pixelated' }} />}
            </div>
            <div className="truncate px-1 py-0.5">{a.slug.replace(asset.slug + '-', '')}</div>
          </Link>
        ))}
      </div>
      <div className="flex gap-1.5">
        <NewAnimationDialog asset={asset} project={project} trigger={<Button size="sm" className="flex-1"><Plus /> Nova animação</Button>} />
        {list.length > 0 && (
          <ExportDialog asset={asset} project={project} initial="2d.spriteset" trigger={<Button size="sm" variant="outline" title="Exportar sprite set (todas as animações)"><Film /></Button>} />
        )}
      </div>
    </div>
  )
}

export function NewAnimationDialog({ asset, project, trigger }: { asset: AssetVersion; project: Project; trigger: ReactElement }) {
  const [open, setOpen] = useState(false)
  const [preset, setPreset] = useState<string | null>('walk')
  const [prompt, setPrompt] = useState('')
  const [frames, setFrames] = useState(8)
  const [fps, setFps] = useState(10)
  const [loop, setLoop] = useState(true)
  const [method, setMethod] = useState('auto')
  const [name, setName] = useState('')
  const { submit, busy } = useJobNav(project)

  const pick = (id: string | null) => {
    setPreset(id)
    const p = PRESETS.find((x) => x.id === id)
    if (p) {
      setFrames(p.frames)
      setFps(p.fps)
      setLoop(!['attack', 'jump', 'hurt', 'death', 'cast'].includes(p.id))
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Nova animação de {asset.name || asset.slug}</DialogTitle>
          <DialogDescription>O sprite atual ({asset.ref}) é o estado inicial; a IA muda pose, ângulo e gestos mantendo o personagem.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={async (e) => {
            e.preventDefault()
            await submit('2d.animate', { input_asset: asset.ref, prompt, preset, frames, fps, loop, method }, name || undefined)
            setOpen(false)
          }}
        >
          <div>
            <Label className="mb-1.5">Movimento</Label>
            <div className="grid grid-cols-5 gap-1.5">
              {PRESETS.map((p) => (
                <button key={p.id} type="button" onClick={() => pick(preset === p.id ? null : p.id)} title={p.hint}
                  className={cn('rounded-md border px-2 py-1.5 text-left text-xs', preset === p.id ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
                  <div className="font-medium">{p.title}</div>
                  <div className="truncate text-muted-foreground">{p.hint}</div>
                </button>
              ))}
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label>Descreva a animação {preset ? <span className="font-normal text-muted-foreground">(opcional, complementa o preset)</span> : null}</Label>
            <Textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)}
              placeholder="andando para a frente, de perfil, olhando para a direita" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="mb-1.5">Sprites (frames): {frames}</Label>
              <Slider min={2} max={24} value={[frames]} onValueChange={(v) => setFrames(Array.isArray(v) ? v[0] : v)} />
            </div>
            <div>
              <Label className="mb-1.5">Velocidade: {fps} fps</Label>
              <Slider min={2} max={30} value={[fps]} onValueChange={(v) => setFps(Array.isArray(v) ? v[0] : v)} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm"><Switch checked={loop} onCheckedChange={setLoop} /> Loop (volta ao início)</label>
            <label className="flex items-center gap-2 text-sm">
              Método
              <select className="rounded-md border bg-transparent px-2 py-1 text-sm" value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="auto">auto</option>
                <option value="video">vídeo (Wan 2.2: movimento fluido)</option>
                <option value="edit">pose a pose (Qwen: rápido, mais rígido)</option>
              </select>
            </label>
            <Input className="h-8 w-48" value={name} onChange={(e) => setName(e.target.value)} placeholder={`${asset.slug}-${preset ?? 'anim'}`} />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={busy || (!preset && !prompt.trim())}><Sparkles /> Gerar animação</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

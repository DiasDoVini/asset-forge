import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import {
  Bookmark, ChevronLeft, ChevronRight, Film, Image as ImageIcon, Music, Pause, Play, Repeat, Save, Scissors,
  Sparkles, Trash2, Wand2, X,
} from 'lucide-react'
import { api } from '@/core/api'
import type { ViewerProps } from '@/core/registry'
import { AssetPicker } from '@/core/components/AssetPicker'
import { useJobNav } from '@/core/useJobNav'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export type Marker = { t: number; label: string; color: string }
type Sel = { a: number; b: number } | null

const COLORS = ['#f59e0b', '#22c55e', '#3b82f6', '#ef4444', '#a855f7', '#ec4899']
const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, '0')}`
const ref = (a: { slug: string; version: number }) => `${a.slug}@v${String(a.version).padStart(3, '0')}`

const QUICK: { label: string; params: Record<string, unknown>; sep?: boolean }[] = [
  { label: 'Inverter', params: { reverse: true } },
  { label: 'Boomerang (ida e volta)', params: { pingpong: true } },
  { label: 'Repetir 2×', params: { loop: 2 } },
  { label: '2× mais rápido', params: { speed: 2 }, sep: true },
  { label: '0,5× câmera lenta', params: { speed: 0.5 } },
  { label: '24 fps suave (interpolar)', params: { fps: 24, smooth_fps: true } },
  { label: 'Fade de entrada e saída', params: { fade_in: 0.5, fade_out: 0.5 }, sep: true },
  { label: 'Remover áudio', params: { mute: true } },
  { label: 'Reduzir para 720 px de largura', params: { width: 720 } },
]

/** Editor de vídeo: player, linha do tempo com marcações e seleção, edição manual e por IA. */
export function VideoViewer({ url, asset, project }: ViewerProps) {
  const v = useRef<HTMLVideoElement>(null)
  const bar = useRef<HTMLDivElement>(null)
  const nav = useNavigate()
  const info = (asset.extra?.video ?? {}) as { fps?: number; duration?: number; width?: number; height?: number; has_audio?: boolean }
  const [dur, setDur] = useState(info.duration ?? 0)
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loop, setLoop] = useState(true)
  const [rate, setRate] = useState(1)
  const [sel, setSel] = useState<Sel>(null)
  const [markers, setMarkers] = useState<Marker[]>(((asset.extra?.markers as Marker[]) ?? []).map((m) => ({ ...m })))
  const [aiPrompt, setAiPrompt] = useState('')
  const [audio, setAudio] = useState<string | undefined>()
  const drag = useRef<number | null>(null)
  const { submit, busy } = useJobNav(project, 'Processando vídeo…')
  const fps = info.fps || 24
  const dirtyMarkers = JSON.stringify(markers) !== JSON.stringify(asset.extra?.markers ?? [])

  useEffect(() => {
    setMarkers(((asset.extra?.markers as Marker[]) ?? []).map((m) => ({ ...m })))
    setSel(null)
  }, [asset.slug, asset.version, asset.extra?.markers])

  useEffect(() => {
    const el = v.current
    if (!el) return
    let raf = 0
    const tick = () => {
      setT(el.currentTime)
      if (sel && loop && !el.paused && el.currentTime >= sel.b) el.currentTime = sel.a
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [sel, loop])

  const seek = (x: number) => {
    if (v.current) v.current.currentTime = Math.max(0, Math.min(dur, x))
  }
  const toggle = () => {
    const el = v.current
    if (!el) return
    if (el.paused) {
      if (sel && (el.currentTime < sel.a || el.currentTime >= sel.b)) el.currentTime = sel.a
      void el.play()
    } else el.pause()
  }
  const step = (n: number) => {
    v.current?.pause()
    seek(t + n / fps)
  }
  const addMarker = () => {
    const label = `m${markers.length + 1}`
    setMarkers([...markers, { t: Number(t.toFixed(3)), label, color: COLORS[markers.length % COLORS.length] }].sort((a, b) => a.t - b.t))
  }
  const setIn = () => setSel({ a: t, b: Math.max(t + 0.1, sel?.b ?? dur) })
  const setOut = () => setSel({ a: Math.min(sel?.a ?? 0, t - 0.1), b: t })

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.code === 'Space') { e.preventDefault(); toggle() }
      else if (e.key === 'ArrowLeft') step(e.shiftKey ? -10 : -1)
      else if (e.key === 'ArrowRight') step(e.shiftKey ? 10 : 1)
      else if (e.key === 'm') addMarker()
      else if (e.key === 'i') setIn()
      else if (e.key === 'o') setOut()
      else if (e.key === 'Escape') setSel(null)
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })

  const timeAt = (e: React.PointerEvent) => {
    const r = bar.current!.getBoundingClientRect()
    return Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur))
  }
  const pct = (x: number) => `${(x / Math.max(dur, 0.001)) * 100}%`
  const run = (pipeline: string, params: Record<string, unknown>, msg?: string) =>
    submit(pipeline, { input_asset: ref(asset), ...params }, undefined, msg)

  const saveFrame = async () => {
    const el = v.current
    if (!el) return
    const c = document.createElement('canvas')
    c.width = el.videoWidth
    c.height = el.videoHeight
    c.getContext('2d')!.drawImage(el, 0, 0)
    const blob = await new Promise<Blob>((res) => c.toBlob((b) => res(b!), 'image/png'))
    const av = await api.uploadAsset(project.slug, blob, { modality: '2d', kind: 'image', filename: `${asset.slug}-frame.png`, name: `${asset.slug} @ ${fmt(t)}`, parent: ref(asset) })
    toast.success(`Quadro salvo como ${av.slug}`, { action: { label: 'Abrir', onClick: () => nav(`/p/${project.slug}/a/${av.slug}/${av.version}`) } })
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-auto p-3">
      <div className="relative flex min-h-[220px] flex-1 items-center justify-center rounded-md bg-black">
        <video
          ref={v}
          src={url}
          className="max-h-full max-w-full"
          loop={loop && !sel}
          playsInline
          onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onClick={toggle}
          crossOrigin="anonymous"
        />
      </div>

      {/* linha do tempo */}
      <div className="select-none">
        <div
          ref={bar}
          className="relative h-10 cursor-pointer touch-none rounded bg-muted"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId)
            drag.current = timeAt(e)
            seek(drag.current)
          }}
          onPointerMove={(e) => {
            if (drag.current === null) return
            const x = timeAt(e)
            if (Math.abs(x - drag.current) > dur / 150) setSel({ a: Math.min(x, drag.current), b: Math.max(x, drag.current) })
            seek(x)
          }}
          onPointerUp={() => (drag.current = null)}
        >
          {sel && <div className="absolute inset-y-0 bg-sky-500/25 ring-1 ring-sky-400" style={{ left: pct(sel.a), width: pct(sel.b - sel.a) }} />}
          {markers.map((m, i) => (
            <div key={i} className="absolute inset-y-0 w-0.5" style={{ left: pct(m.t), background: m.color }} title={`${m.label} — ${fmt(m.t)}`}>
              <span className="absolute -top-0.5 left-1 text-[10px] font-medium whitespace-nowrap" style={{ color: m.color }}>{m.label}</span>
            </div>
          ))}
          <div className="absolute inset-y-0 w-0.5 bg-white" style={{ left: pct(t) }} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" onClick={toggle}>{playing ? <Pause /> : <Play />}</Button>
        <Button size="sm" variant="outline" onClick={() => step(-1)} title="quadro anterior (←)"><ChevronLeft /></Button>
        <Button size="sm" variant="outline" onClick={() => step(1)} title="próximo quadro (→)"><ChevronRight /></Button>
        <Button size="sm" variant={loop ? 'default' : 'outline'} onClick={() => setLoop(!loop)} title="repetir"><Repeat /></Button>
        <select value={rate} onChange={(e) => { const r = Number(e.target.value); setRate(r); if (v.current) v.current.playbackRate = r }} className="h-7 rounded border bg-background px-1 text-xs">
          {[0.25, 0.5, 1, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
        </select>
        <span className="font-mono text-xs text-muted-foreground">
          {fmt(t)} / {fmt(dur)} · quadro {Math.round(t * fps)}
          {sel && ` · trecho ${fmt(sel.a)}–${fmt(sel.b)} (${(sel.b - sel.a).toFixed(2)} s)`}
        </span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <Button size="sm" variant="outline" onClick={setIn} title="início do trecho (i)">[ in</Button>
          <Button size="sm" variant="outline" onClick={setOut} title="fim do trecho (o)">out ]</Button>
          {sel && <Button size="sm" variant="ghost" onClick={() => setSel(null)}><X /></Button>}
          <Button size="sm" variant="outline" onClick={addMarker} title="marcação no tempo atual (m)"><Bookmark /> Marcar</Button>
          <Button size="sm" variant="outline" onClick={saveFrame} title="salva o quadro atual como imagem 2D"><ImageIcon /> Quadro</Button>
        </div>
      </div>

      {/* edição manual */}
      <div className="flex flex-wrap items-center gap-1.5 border-t pt-2">
        <Film className="size-4 text-muted-foreground" />
        <Button size="sm" variant="outline" disabled={!sel || busy} onClick={() => sel && run('video.edit', { trim_start: sel.a, trim_end: sel.b })}>
          <Scissors /> Manter trecho
        </Button>
        <Button size="sm" variant="outline" disabled={!sel || busy} onClick={() => sel && run('video.edit', { ops: [{ op: 'cut', start: sel.a, end: sel.b }] })}>
          <Trash2 /> Remover trecho
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="sm" variant="outline" disabled={busy}><Wand2 /> Efeitos</Button>} />
          <DropdownMenuContent className="w-60">
            {QUICK.map((q) => [
              q.sep ? <DropdownMenuSeparator key={q.label + 's'} /> : null,
              <DropdownMenuItem key={q.label} onClick={() => run('video.edit', q.params)}>{q.label}</DropdownMenuItem>,
            ])}
          </DropdownMenuContent>
        </DropdownMenu>
        <div className="flex items-center gap-1">
          <Music className="size-4 text-muted-foreground" />
          <div className="w-44"><AssetPicker project={project.slug} value={audio} onChange={setAudio} modality="audio" placeholder="Trilha de áudio…" /></div>
          <Button size="sm" variant="outline" disabled={!audio || busy} onClick={() => run('video.edit', { audio_asset: audio, audio_mode: info.has_audio ? 'mix' : 'replace', audio_offset: sel?.a ?? 0 })}>
            Aplicar
          </Button>
        </div>
      </div>

      {/* IA */}
      <div className="flex flex-wrap items-center gap-1.5 border-t pt-2">
        <Sparkles className="size-4 text-muted-foreground" />
        <Input value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} placeholder={sel ? 'o que deve acontecer no trecho selecionado…' : 'o que acontece a seguir… (ou selecione um trecho)'} className="h-8 min-w-60 flex-1" />
        <Button size="sm" disabled={!sel || !aiPrompt.trim() || busy} title="refaz o trecho com IA mantendo os quadros das pontas (Wan 14B)"
          onClick={() => sel && run('video.regen_segment', { start: sel.a, end: sel.b, prompt: aiPrompt }, 'Regenerando trecho…')}>
          Regenerar trecho
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => run('video.extend', { prompt: aiPrompt, seconds: 4 }, 'Estendendo…')}>
          Estender +4 s
        </Button>
      </div>

      {/* marcações */}
      {markers.length > 0 && (
        <div className="grid gap-1 border-t pt-2">
          {markers.map((m, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <input type="color" value={m.color} onChange={(e) => setMarkers(markers.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))} className="h-5 w-6 cursor-pointer rounded border bg-transparent" />
              <button className="font-mono text-muted-foreground hover:text-foreground" onClick={() => seek(m.t)}>{fmt(m.t)}</button>
              <Input value={m.label} onChange={(e) => setMarkers(markers.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} className="h-6 max-w-48 text-xs" />
              <Button size="xs" variant="ghost" onClick={() => setMarkers(markers.map((x, j) => (j === i ? { ...x, t: Number(t.toFixed(3)) } : x)).sort((a, b) => a.t - b.t))} title="mover para o tempo atual">↦ agora</Button>
              <Button size="xs" variant="ghost" onClick={() => { const n = markers[i + 1]; setSel({ a: m.t, b: n ? n.t : dur }) }} title="selecionar até a próxima marcação">trecho</Button>
              <Button size="xs" variant="ghost" onClick={() => setMarkers(markers.filter((_, j) => j !== i))}><X /></Button>
            </div>
          ))}
        </div>
      )}
      {dirtyMarkers && (
        <Button size="sm" className="self-start" disabled={busy} onClick={() => run('video.edit', { markers })}>
          <Save /> Salvar marcações
        </Button>
      )}
      <p className={cn('text-xs text-muted-foreground')}>
        Espaço toca · ←/→ quadro a quadro (Shift = 10) · arraste na linha do tempo ou use i/o para selecionar · m marca.
        Cada edição cria uma nova versão.
      </p>
    </div>
  )
}

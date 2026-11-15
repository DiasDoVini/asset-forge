import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Pause, Play, Repeat, Scissors, Square, Trash2, Wand2 } from 'lucide-react'
import type { AssetFile, AssetVersion, Project } from '@/core/api'
import type { ViewerProps } from '@/core/registry'
import { useJobNav } from '@/core/useJobNav'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

let ctxSingleton: AudioContext | null = null
export function audioContext() {
  ctxSingleton ??= new AudioContext()
  return ctxSingleton
}

export async function decode(url: string): Promise<AudioBuffer> {
  const buf = await fetch(url).then((r) => r.arrayBuffer())
  return audioContext().decodeAudioData(buf)
}

export const fmtTime = (s: number) => {
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return `${m}:${r.toFixed(2).padStart(5, '0')}`
}

/** Picos (mín, máx) por coluna, misturando os canais. */
function peaks(buf: AudioBuffer, cols: number): Float32Array {
  const out = new Float32Array(cols * 2)
  const chans = Array.from({ length: buf.numberOfChannels }, (_, i) => buf.getChannelData(i))
  const step = buf.length / cols
  for (let c = 0; c < cols; c++) {
    let lo = 1
    let hi = -1
    const a = Math.floor(c * step)
    const b = Math.max(a + 1, Math.floor((c + 1) * step))
    for (let i = a; i < b; i += Math.max(1, Math.floor((b - a) / 256))) {
      let v = 0
      for (const ch of chans) v += ch[i]
      v /= chans.length
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
    out[c * 2] = lo
    out[c * 2 + 1] = hi
  }
  return out
}

type Sel = { a: number; b: number } | null

/** Player com forma de onda. Arraste para selecionar um trecho; clique para posicionar. */
export function Waveform({ buffer, height = 160, onSelect, sel, playhead, onSeek, compact }: {
  buffer: AudioBuffer
  height?: number
  sel?: Sel
  onSelect?: (s: Sel) => void
  playhead?: number
  onSeek?: (t: number) => void
  compact?: boolean
}) {
  const cv = useRef<HTMLCanvasElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(600)
  const drag = useRef<number | null>(null)
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(Math.max(100, Math.floor(el.clientWidth))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const pk = useMemo(() => peaks(buffer, w), [buffer, w])
  useEffect(() => {
    const c = cv.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    c.width = w * dpr
    c.height = height * dpr
    const g = c.getContext('2d')!
    g.scale(dpr, dpr)
    g.clearRect(0, 0, w, height)
    const mid = height / 2
    if (sel) {
      g.fillStyle = 'rgba(110,190,255,0.15)'
      g.fillRect((sel.a / buffer.duration) * w, 0, ((sel.b - sel.a) / buffer.duration) * w, height)
    }
    g.strokeStyle = 'rgba(120,130,160,0.35)'
    g.beginPath()
    g.moveTo(0, mid)
    g.lineTo(w, mid)
    g.stroke()
    let mx = 0.001
    for (let i = 0; i < pk.length; i++) mx = Math.max(mx, Math.abs(pk[i]))
    g.fillStyle = '#6ebeff'
    for (let x = 0; x < w; x++) {
      const lo = pk[x * 2] / mx
      const hi = pk[x * 2 + 1] / mx
      g.fillRect(x, mid - hi * mid * 0.95, 1, Math.max(1, (hi - lo) * mid * 0.95))
    }
    if (playhead !== undefined && playhead >= 0) {
      g.fillStyle = '#fbbf24'
      g.fillRect((playhead / buffer.duration) * w, 0, 1.5, height)
    }
  }, [pk, w, height, sel, playhead, buffer.duration])

  const t = (e: React.PointerEvent) => {
    const r = cv.current!.getBoundingClientRect()
    return Math.min(buffer.duration, Math.max(0, ((e.clientX - r.left) / r.width) * buffer.duration))
  }
  return (
    <div ref={box} className={cn('w-full', compact ? '' : 'rounded-md bg-[#14161e]')}>
      <canvas
        ref={cv}
        style={{ width: w, height }}
        className="block cursor-text touch-none"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          drag.current = t(e)
          onSelect?.(null)
        }}
        onPointerMove={(e) => {
          if (drag.current === null || !onSelect) return
          const x = t(e)
          if (Math.abs(x - drag.current) > buffer.duration / 200)
            onSelect({ a: Math.min(x, drag.current), b: Math.max(x, drag.current) })
        }}
        onPointerUp={(e) => {
          const x = t(e)
          if (drag.current !== null && Math.abs(x - drag.current) <= buffer.duration / 200) onSeek?.(x)
          drag.current = null
        }}
      />
    </div>
  )
}

/** Tocador simples com posição, loop e trecho. */
export function usePlayer(buffer: AudioBuffer | undefined) {
  const src = useRef<AudioBufferSourceNode | null>(null)
  const started = useRef({ at: 0, offset: 0, end: 0, loop: false })
  const [playing, setPlaying] = useState(false)
  const [pos, setPos] = useState(0)
  const raf = useRef(0)

  const stop = useCallback(() => {
    src.current?.stop()
    src.current = null
    setPlaying(false)
    cancelAnimationFrame(raf.current)
  }, [])

  const play = useCallback(
    (from = 0, to?: number, loop = false) => {
      if (!buffer) return
      stop()
      const ctx = audioContext()
      void ctx.resume()
      const s = ctx.createBufferSource()
      s.buffer = buffer
      s.connect(ctx.destination)
      const end = to ?? buffer.duration
      if (loop) {
        s.loop = true
        s.loopStart = from
        s.loopEnd = end
        s.start(0, from)
      } else s.start(0, from, Math.max(0.01, end - from))
      s.onended = () => {
        if (src.current === s) {
          setPlaying(false)
          setPos(-1)
        }
      }
      src.current = s
      started.current = { at: ctx.currentTime, offset: from, end, loop }
      setPlaying(true)
      const tick = () => {
        const st = started.current
        let p = st.offset + (ctx.currentTime - st.at)
        if (st.loop && st.end > st.offset) p = st.offset + ((p - st.offset) % (st.end - st.offset))
        setPos(Math.min(p, st.end))
        raf.current = requestAnimationFrame(tick)
      }
      raf.current = requestAnimationFrame(tick)
    },
    [buffer, stop],
  )
  useEffect(() => stop, [stop])
  return { play, stop, playing, pos }
}

function variationFiles(asset: AssetVersion): AssetFile[] {
  const prim = asset.files.find((f) => f.role === 'primary')
  const parts = asset.files.filter((f) => f.role === 'part' && f.mime.startsWith('audio/')).sort((a, b) => a.path.localeCompare(b.path))
  return prim ? [prim, ...parts] : parts
}

const QUICK: { label: string; params: Record<string, unknown>; sep?: boolean }[] = [
  { label: 'Normalizar (pico -1 dB)', params: { normalize: 'peak', target: -1 } },
  { label: 'Normalizar loudness (-16 LUFS)', params: { normalize: 'loudness', target: -16 } },
  { label: 'Cortar silêncio', params: { trim_silence: true } },
  { label: 'Fade in + out curtos', params: { fade_in_ms: 10, fade_out_ms: 80 } },
  { label: 'Fechar em loop', params: { make_loop: true }, sep: true },
  { label: 'Inverter (reverse)', params: { reverse: true } },
  { label: 'Mais grave (-4 semitons)', params: { pitch_semitones: -4 } },
  { label: 'Mais agudo (+4 semitons)', params: { pitch_semitones: 4 } },
  { label: 'Mais lento (0,8×)', params: { speed: 0.8 } },
  { label: 'Mais rápido (1,25×)', params: { speed: 1.25 } },
  { label: 'Reverb (sala)', params: { reverb: 0.3, room: 0.5 }, sep: true },
  { label: 'Reverb (caverna)', params: { reverb: 0.45, room: 0.95 } },
  { label: 'Eco', params: { echo_ms: 280 } },
  { label: 'Abafado (passa-baixa 1,2 kHz)', params: { lowpass_hz: 1200 } },
  { label: 'Rádio/telefone', params: { highpass_hz: 400, lowpass_hz: 3200, distortion: 0.15 } },
  { label: 'Retrô 8-bit', params: { ops: [{ op: 'bitcrush', bits: 8, sample_rate: 11025 }] } },
  { label: 'Distorção', params: { distortion: 0.5 } },
  { label: 'Mono', params: { channels: 'mono' }, sep: true },
]

export function AudioViewer({ url, asset, project }: ViewerProps) {
  const vars = variationFiles(asset)
  const [vi, setVi] = useState(0)
  const cur = vars[vi]?.url ?? url
  const q = useQuery({ queryKey: ['audio-buffer', cur], queryFn: () => decode(cur), staleTime: Infinity })
  const buffer = q.data
  const { play, stop, playing, pos } = usePlayer(buffer)
  const [sel, setSel] = useState<Sel>(null)
  const [loop, setLoop] = useState(Boolean(asset.extra?.loop))
  const [cursor, setCursor] = useState(0)
  const { submit, busy } = useJobNav(project as Project, 'Editando áudio…')
  useEffect(() => {
    setSel(null)
    setCursor(0)
  }, [cur])

  const toggle = () => {
    if (playing) return stop()
    if (sel) play(sel.a, sel.b, loop)
    else play(cursor, undefined, loop)
  }
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })

  const edit = (params: Record<string, unknown>) => {
    const ops: Record<string, unknown>[] = []
    if (vi > 0) ops.push({ op: 'use_variation', index: vi + 1 })
    const extraOps = (params.ops as Record<string, unknown>[] | undefined) ?? []
    const rest = { ...params }
    delete rest.ops
    void submit('audio.edit', { input_asset: `${asset.slug}@v${String(asset.version).padStart(3, '0')}`, ops: [...ops, ...extraOps], ...rest })
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4">
      {vars.length > 1 && (
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <span className="text-muted-foreground">Variações:</span>
          {vars.map((f, i) => (
            <Button key={f.path} size="xs" variant={i === vi ? 'default' : 'outline'} onClick={() => setVi(i)}>
              {i + 1}
            </Button>
          ))}
        </div>
      )}
      {buffer ? (
        <Waveform buffer={buffer} height={200} sel={sel} onSelect={setSel} playhead={playing ? pos : cursor} onSeek={(t) => { setCursor(t); if (playing) play(t, undefined, loop) }} />
      ) : (
        <div className="flex h-[200px] items-center justify-center rounded-md bg-[#14161e] text-sm text-muted-foreground">
          {q.isError ? `não foi possível decodificar: ${String(q.error)}` : 'carregando áudio…'}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={toggle} disabled={!buffer}>
          {playing ? <Pause /> : <Play />} {playing ? 'Pausar' : sel ? 'Tocar seleção' : 'Tocar'}
        </Button>
        <Button size="sm" variant="outline" onClick={() => { stop(); setCursor(0) }} disabled={!buffer}>
          <Square />
        </Button>
        <Button size="sm" variant={loop ? 'default' : 'outline'} onClick={() => setLoop(!loop)} title="repetir (testa a emenda do loop)">
          <Repeat />
        </Button>
        <span className="font-mono text-xs text-muted-foreground">
          {fmtTime(playing ? pos : cursor)} / {buffer ? fmtTime(buffer.duration) : '–'}
          {sel && ` · seleção ${fmtTime(sel.a)}–${fmtTime(sel.b)} (${(sel.b - sel.a).toFixed(2)} s)`}
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={!sel || busy} onClick={() => sel && edit({ trim_start: sel.a, trim_end: sel.b })} title="mantém só a seleção">
            <Scissors /> Manter seleção
          </Button>
          <Button size="sm" variant="outline" disabled={!sel || busy} onClick={() => sel && edit({ ops: [{ op: 'cut', start: sel.a, end: sel.b }] })}>
            <Trash2 /> Remover seleção
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm" disabled={busy}><Wand2 /> Efeitos</Button>} />
            <DropdownMenuContent align="end" className="max-h-96 w-64 overflow-auto">
              {QUICK.map((qk) => [
                qk.sep ? <DropdownMenuSeparator key={qk.label + '-s'} /> : null,
                <DropdownMenuItem key={qk.label} onClick={() => edit(qk.params)}>
                  {qk.label}
                </DropdownMenuItem>,
              ])}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Arraste na forma de onda para selecionar; clique para posicionar; espaço toca/pausa. Cada edição cria uma nova versão
        {vi > 0 ? ` (a partir da variação ${vi + 1})` : ''}. Para ajustes finos use a aba Gerar com “Editar áudio”.
      </p>
    </div>
  )
}

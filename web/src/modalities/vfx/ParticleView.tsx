import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PackageOpen, Pause, Play, RotateCcw, Save, Sparkles } from 'lucide-react'
import { api, type AssetVersion, type Project } from '@/core/api'
import type { PanelProps, ViewerProps } from '@/core/registry'
import { ExportDialog } from '@/core/components/ExportDialog'
import { useJobNav } from '@/core/useJobNav'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ParticlePlayer, transformSystem, type ParticleSystem } from './particles'
import { BG_CLASS, BgToggle, type Bg } from './ShaderView'

const ref = (a: { slug: string; version: number }) => `${a.slug}@v${String(a.version).padStart(3, '0')}`

/** Carrega texturas `asset:<slug>` usadas pelos emissores. */
function useAssetTextures(project: Project, sys: ParticleSystem | undefined) {
  const [tex, setTex] = useState<Record<string, CanvasImageSource>>({})
  const names = sys ? [...new Set(sys.emitters.map((e) => e.texture).filter((t) => t.startsWith('asset:')))] : []
  const key = names.join('|')
  useEffect(() => {
    let alive = true
    Promise.all(
      names.map(async (n) => {
        const vs = await api.versions(project.slug, n.slice(6).split('@')[0])
        const v = vs[vs.length - 1]
        const f = v?.files.find((x) => x.role === 'primary')
        if (!f) return null
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.src = f.url
        await img.decode()
        return [n, img] as const
      }),
    ).then((pairs) => alive && setTex(Object.fromEntries(pairs.filter(Boolean) as [string, HTMLImageElement][])))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, project.slug])
  return tex
}

export function ParticleCanvas({ sys, textures, playing = true, size = 512, seed = 1, restartKey = 0, className }: {
  sys: ParticleSystem
  textures: Record<string, CanvasImageSource>
  playing?: boolean
  size?: number
  seed?: number
  restartKey?: number
  className?: string
}) {
  const cv = useRef<HTMLCanvasElement>(null)
  const player = useRef<ParticlePlayer | null>(null)
  const st = useRef({ playing })
  st.current = { playing }
  const [count, setCount] = useState(0)

  useEffect(() => {
    player.current = new ParticlePlayer(sys, textures, seed)
  }, [sys, textures, seed, restartKey])

  useEffect(() => {
    let raf = 0
    let last = performance.now()
    let frame = 0
    const loop = (now: number) => {
      const p = player.current
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      if (p && cv.current) {
        if (st.current.playing) {
          for (let k = 0; k < 4; k++) p.advance(dt / 4)
          // one-shot: reinicia depois que tudo morreu
          if (!p.sys.loop && p.t > p.sys.duration + 0.4 && p.count === 0) p.reset(seed)
        }
        p.draw(cv.current.getContext('2d')!, cv.current.width, cv.current.height)
        if (++frame % 15 === 0) setCount(p.count)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [seed])

  return (
    <div className="relative flex size-full items-center justify-center">
      <canvas ref={cv} width={size} height={size} className={cn('aspect-square max-h-full max-w-full', className)} />
      <span className="absolute right-2 bottom-1 font-mono text-[10px] text-white/40">{count} partículas</span>
    </div>
  )
}

function useSystem(url: string) {
  return useQuery({ queryKey: ['particles-def', url], queryFn: () => fetch(url).then((r) => r.json() as Promise<ParticleSystem>) })
}

const hexToRgb = (h: string): [number, number, number] => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255]

function Knob({ label, value, set, min, max }: { label: string; value: number; set: (v: number) => void; min: number; max: number }) {
  return (
    <div className="grid gap-1">
      <div className="flex justify-between text-xs"><Label className="text-xs">{label}</Label><span className="font-mono text-muted-foreground">{value.toFixed(2)}×</span></div>
      <Slider min={min} max={max} step={0.05} value={[value]} onValueChange={(v) => set(Array.isArray(v) ? v[0] : v)} />
    </div>
  )
}

/** Viewer de partículas: simulação ao vivo, ajustes rápidos (escala, velocidade, quantidade, cor) e salvar. */
export function ParticleViewer({ url, asset, project }: ViewerProps) {
  const q = useSystem(url)
  const tex = useAssetTextures(project, q.data)
  const [playing, setPlaying] = useState(true)
  const [bg, setBg] = useState<Bg>('dark')
  const [restart, setRestart] = useState(0)
  const [scale, setScale] = useState(1)
  const [speed, setSpeed] = useState(1)
  const [amount, setAmount] = useState(1)
  const [color, setColor] = useState<string | null>(null)
  const { submit, busy } = useJobNav(project, 'Salvando partículas…')
  const sys = useMemo(() => (q.data ? transformSystem(q.data, scale, speed, amount, color ? hexToRgb(color) : null) : null), [q.data, scale, speed, amount, color])
  useEffect(() => {
    setScale(1); setSpeed(1); setAmount(1); setColor(null)
  }, [url])
  if (!sys || !q.data) return <div className="p-4 text-sm text-muted-foreground">{q.isError ? String(q.error) : 'carregando…'}</div>
  const dirty = scale !== 1 || speed !== 1 || amount !== 1 || color !== null
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-3 lg:flex-row lg:overflow-hidden">
      <div className={cn('relative min-h-[280px] flex-1 overflow-hidden rounded-md', BG_CLASS[bg])}>
        <ParticleCanvas sys={sys} textures={tex} playing={playing} restartKey={restart} />
      </div>
      <div className="flex w-full shrink-0 flex-col gap-3 overflow-auto lg:w-72">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setPlaying(!playing)}>{playing ? <Pause /> : <Play />}</Button>
          <Button size="sm" variant="outline" onClick={() => setRestart((r) => r + 1)} title="reiniciar"><RotateCcw /></Button>
          <BgToggle bg={bg} setBg={setBg} />
          <span className="ml-auto text-xs text-muted-foreground">{q.data.loop ? 'loop' : 'one-shot'} · {q.data.duration}s</span>
        </div>
        {q.data.description && <p className="text-xs text-muted-foreground">{q.data.description}</p>}
        <p className="text-xs text-muted-foreground">Emissores: {q.data.emitters.map((e) => e.name).join(', ')}</p>
        <Knob label="Escala" value={scale} set={setScale} min={0.3} max={3} />
        <Knob label="Velocidade" value={speed} set={setSpeed} min={0.3} max={3} />
        <Knob label="Quantidade" value={amount} set={setAmount} min={0.2} max={4} />
        <div className="flex items-center justify-between">
          <Label className="text-xs">Recolorir (emissores aditivos)</Label>
          <input type="color" value={color ?? '#ffa030'} onChange={(e) => setColor(e.target.value)} className="h-7 w-12 cursor-pointer rounded border bg-transparent" />
        </div>
        <div className="flex gap-2">
          <Button size="sm" disabled={!dirty || busy} onClick={() => submit('vfx.particles_edit', { input_asset: ref(asset), scale, speed, amount, colors: color ? [color] : [] })}>
            <Save /> Salvar versão
          </Button>
          <Button size="sm" variant="outline" disabled={!dirty} onClick={() => { setScale(1); setSpeed(1); setAmount(1); setColor(null) }}><RotateCcw /></Button>
          <ExportDialog asset={asset as AssetVersion} project={project} initial="vfx.particles_engine" trigger={<Button size="sm" variant="outline" className="ml-auto"><PackageOpen /> Exportar</Button>} />
        </div>
      </div>
    </div>
  )
}

/** Aba "JSON": edita a definição completa com prévia ao vivo. */
export function ParticleEditor({ asset, project }: PanelProps) {
  const prim = asset.files.find((f) => f.role === 'primary')!
  const q = useSystem(prim.url)
  const [text, setText] = useState('')
  const [instr, setInstr] = useState('')
  const [bg, setBg] = useState<Bg>('dark')
  const { submit, busy } = useJobNav(project, 'Salvando partículas…')
  const crit = useQuery({ queryKey: ['refiners'], queryFn: api.refiners, staleTime: 60_000 })
  const claude = crit.data?.api_critic.available ?? false
  useEffect(() => {
    if (q.data) setText(JSON.stringify(q.data, null, 2))
  }, [q.data])
  const parsed = useMemo((): { sys: ParticleSystem | null; error: string | null } => {
    try {
      const s = JSON.parse(text) as ParticleSystem
      if (!Array.isArray(s.emitters) || !s.emitters.length) throw new Error('emitters vazio')
      // preenche padrões mínimos para a prévia (o servidor valida tudo ao salvar)
      const base = q.data?.emitters[0]
      s.canvas ??= [256, 256]
      s.emitters = s.emitters.map((e) => ({ ...(base ?? {}), ...e, bursts: e.bursts ?? [] }))
      return { sys: s, error: null }
    } catch (e) {
      return { sys: null, error: String(e) }
    }
  }, [text, q.data])
  const tex = useAssetTextures(project, parsed.sys ?? q.data)
  if (!q.data) return <p className="p-4 text-sm text-muted-foreground">carregando…</p>
  return (
    <div className="grid h-full min-h-0 gap-3 overflow-auto p-3 max-lg:content-start lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] lg:overflow-hidden">
      <Textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} className="h-[50vh] resize-none font-mono text-xs lg:h-auto lg:min-h-[360px]" />
      <div className="flex flex-col gap-3 lg:min-h-0 lg:overflow-auto">
        <div className={cn('mx-auto aspect-square w-full max-w-[420px] shrink-0 overflow-hidden rounded-md', BG_CLASS[bg])}>
          {parsed.sys && <ParticleCanvas sys={parsed.sys} textures={tex} size={384} />}
        </div>
        {parsed.error ? <p className="text-xs text-red-400">{parsed.error}</p> : <p className="text-xs text-emerald-400">JSON ok ✓</p>}
        <div className="flex items-center gap-2">
          <BgToggle bg={bg} setBg={setBg} />
          <Button size="sm" className="ml-auto" disabled={!parsed.sys || busy || text === JSON.stringify(q.data, null, 2)}
            onClick={() => submit('vfx.particles_edit', { input_asset: ref(asset), definition: JSON.parse(text) })}>
            <Save /> Salvar versão
          </Button>
        </div>
        <div className="grid gap-2 border-t pt-3">
          <Input value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="Pedir mudança ao Claude: “mais faíscas, fumaça mais lenta”" />
          <Button size="sm" variant="outline" disabled={!instr.trim() || !claude || busy} title={claude ? '' : crit.data?.api_critic.detail}
            onClick={() => submit('vfx.particles_edit', { input_asset: ref(asset), instruction: instr }, undefined, 'Claude ajustando…')}>
            <Sparkles /> Aplicar com Claude
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Unidades em px, origem no centro, y para cima, ângulos em graus (90 = cima). Texturas: soft, circle, spark, star, smoke,
          ring, square, flare ou asset:&lt;slug&gt; (gere com “Textura de efeito”).
        </p>
      </div>
    </div>
  )
}

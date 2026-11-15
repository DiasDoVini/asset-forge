import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Pause, Play, RotateCcw, Save } from 'lucide-react'
import type { ViewerProps } from '@/core/registry'
import { useJobNav } from '@/core/useJobNav'
import { checker } from '@/core/components/ImageViewer'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import { ShaderRunner, fromHex, toHex, uniformValues, type ShaderDef, type Uniform } from './glsl'

export type Bg = 'dark' | 'checker' | 'light'
export const BG_CLASS: Record<Bg, string> = { dark: 'bg-[#12141c]', checker, light: 'bg-[#d8dbe2]' }

/** Canvas WebGL2 que roda o shader em tempo real. */
export function ShaderCanvas({ sd, values, texUrl, playing = true, size = 512, scrub, onError, className }: {
  sd: Pick<ShaderDef, 'uniforms' | 'code' | 'blend' | 'one_shot' | 'duration'>
  values: Record<string, number[]>
  texUrl?: string | null
  playing?: boolean
  size?: number
  scrub?: number | null
  onError?: (e: string | null) => void
  className?: string
}) {
  const cv = useRef<HTMLCanvasElement>(null)
  const runner = useRef<ShaderRunner | null>(null)
  const t0 = useRef(performance.now())
  const paused = useRef(0)
  const state = useRef({ sd, values, playing, scrub })
  state.current = { sd, values, playing, scrub }

  useEffect(() => {
    try {
      runner.current = new ShaderRunner(cv.current!)
    } catch (e) {
      onError?.(String(e))
    }
    let raf = 0
    const loop = () => {
      const r = runner.current
      const s = state.current
      if (r) {
        const now = performance.now()
        if (!s.playing) t0.current += now - (paused.current || now)
        paused.current = s.playing ? 0 : now
        let t = (now - t0.current) / 1000
        if (s.scrub != null) t = s.scrub * s.sd.duration
        const pr = s.sd.one_shot ? (t % s.sd.duration) / s.sd.duration : (t / s.sd.duration) % 1
        r.draw(t, pr, s.sd.uniforms, s.values)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      runner.current?.dispose()
      runner.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const r = runner.current
    if (!r) return
    const res = r.compile(sd)
    onError?.(res.ok ? null : res.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sd.code, JSON.stringify(sd.uniforms.map((u) => [u.name, u.type]))])

  useEffect(() => {
    const r = runner.current
    if (!r) return
    if (!texUrl) return r.setTexture(null)
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => r.setTexture(img)
    img.src = texUrl
  }, [texUrl])

  return (
    <canvas
      ref={cv}
      width={size}
      height={size}
      className={cn('aspect-square max-h-full max-w-full', className)}
      style={{ mixBlendMode: sd.blend === 'add' ? 'plus-lighter' : 'normal' }}
    />
  )
}

export function UniformControls({ uniforms, values, onChange }: {
  uniforms: Uniform[]
  values: Record<string, number[]>
  onChange: (name: string, v: number[]) => void
}) {
  return (
    <div className="grid gap-3">
      {uniforms.map((u) => {
        const v = values[u.name] ?? uniformValues(u)
        const label = u.label || u.name
        if (u.type === 'color')
          return (
            <div key={u.name} className="flex items-center justify-between gap-2">
              <Label className="text-xs">{label}</Label>
              <input type="color" value={toHex(v)} onChange={(e) => onChange(u.name, fromHex(e.target.value, v[3] ?? 1))} className="h-7 w-12 cursor-pointer rounded border bg-transparent" />
            </div>
          )
        if (u.type === 'bool')
          return (
            <div key={u.name} className="flex items-center justify-between gap-2">
              <Label className="text-xs">{label}</Label>
              <input type="checkbox" checked={v[0] > 0.5} onChange={(e) => onChange(u.name, [e.target.checked ? 1 : 0])} />
            </div>
          )
        const lo = u.min ?? 0
        const hi = u.max ?? Math.max(1, v[0] * 2)
        return (
          <div key={u.name} className="grid gap-1">
            {(u.type === 'vec2' ? [0, 1] : [0]).map((i) => (
              <div key={i} className="grid gap-1">
                <div className="flex justify-between text-xs">
                  <Label className="text-xs">{label}{u.type === 'vec2' ? (i ? ' y' : ' x') : ''}</Label>
                  <span className="font-mono text-muted-foreground">{v[i].toFixed(2)}</span>
                </div>
                <Slider min={lo} max={hi} step={(hi - lo) / 200} value={[v[i]]} onValueChange={(x) => {
                  const nv = [...v]
                  nv[i] = Array.isArray(x) ? x[0] : x
                  onChange(u.name, nv)
                }} />
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

export function BgToggle({ bg, setBg }: { bg: Bg; setBg: (b: Bg) => void }) {
  return (
    <div className="flex gap-1">
      {(['dark', 'checker', 'light'] as Bg[]).map((b) => (
        <button key={b} title={`fundo ${b}`} onClick={() => setBg(b)} className={cn('size-6 rounded border', BG_CLASS[b], bg === b && 'ring-2 ring-primary')} />
      ))}
    </div>
  )
}

export function useShaderDef(url: string) {
  return useQuery({ queryKey: ['shader-def', url], queryFn: () => fetch(url).then((r) => r.json() as Promise<ShaderDef>) })
}

const ref = (a: { slug: string; version: number }) => `${a.slug}@v${String(a.version).padStart(3, '0')}`

/** Viewer do asset de shader: prévia ao vivo, controles dos uniforms e linha do tempo para one-shots. */
export function ShaderViewer({ url, asset, project }: ViewerProps) {
  const q = useShaderDef(url)
  const sd = q.data
  const tex = asset.files.find((f) => f.role === 'texture')?.url
  const [values, setValues] = useState<Record<string, number[]>>({})
  const [playing, setPlaying] = useState(true)
  const [scrub, setScrub] = useState<number | null>(null)
  const [bg, setBg] = useState<Bg>('dark')
  const [err, setErr] = useState<string | null>(null)
  const { submit, busy } = useJobNav(project, 'Salvando shader…')
  useEffect(() => setValues({}), [url])
  if (!sd) return <div className="p-4 text-sm text-muted-foreground">{q.isError ? String(q.error) : 'carregando shader…'}</div>
  const dirty = Object.keys(values).length > 0
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-3 lg:flex-row lg:overflow-hidden">
      <div className={cn('relative flex min-h-[280px] flex-1 items-center justify-center overflow-hidden rounded-md', BG_CLASS[bg])}>
        <ShaderCanvas sd={sd} values={values} texUrl={sd.uses_texture ? tex : null} playing={playing} scrub={scrub} onError={setErr} />
        {err && <pre className="absolute inset-x-2 bottom-2 max-h-40 overflow-auto rounded bg-red-950/90 p-2 text-xs text-red-200">{err}</pre>}
      </div>
      <div className="flex w-full shrink-0 flex-col gap-3 overflow-auto lg:w-72">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setPlaying(!playing)}>{playing ? <Pause /> : <Play />}</Button>
          <BgToggle bg={bg} setBg={setBg} />
          <span className="ml-auto text-xs text-muted-foreground">{sd.one_shot ? 'one-shot' : 'loop'} · {sd.duration}s · {sd.blend}</span>
        </div>
        {sd.one_shot && (
          <div className="grid gap-1">
            <div className="flex justify-between text-xs"><Label className="text-xs">Progresso</Label>
              <button className="text-muted-foreground hover:text-foreground" onClick={() => setScrub(null)}>{scrub == null ? 'auto' : scrub.toFixed(2)}</button>
            </div>
            <Slider min={0} max={1} step={0.01} value={[scrub ?? 0]} onValueChange={(x) => setScrub(Array.isArray(x) ? x[0] : x)} />
          </div>
        )}
        {sd.description && <p className="text-xs text-muted-foreground">{sd.description}</p>}
        <UniformControls uniforms={sd.uniforms} values={values} onChange={(n, v) => setValues((s) => ({ ...s, [n]: v }))} />
        <div className="flex gap-2">
          <Button size="sm" disabled={!dirty || busy} onClick={() => submit('vfx.shader_edit', { input_asset: ref(asset), uniforms: values })}>
            <Save /> Salvar valores
          </Button>
          <Button size="sm" variant="outline" disabled={!dirty} onClick={() => setValues({})}><RotateCcw /></Button>
        </div>
      </div>
    </div>
  )
}

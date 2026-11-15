import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PackageOpen, Save, Sparkles } from 'lucide-react'
import { api } from '@/core/api'
import type { PanelProps } from '@/core/registry'
import { ExportDialog } from '@/core/components/ExportDialog'
import { useJobNav } from '@/core/useJobNav'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { type ShaderDef, type Uniform } from './glsl'
import { BG_CLASS, BgToggle, ShaderCanvas, UniformControls, useShaderDef, type Bg } from './ShaderView'

const ref = (a: { slug: string; version: number }) => `${a.slug}@v${String(a.version).padStart(3, '0')}`

/** Aba "Código": edita GLSL e uniforms com prévia ao vivo; salva como nova versão (validada no servidor). */
export function ShaderEditor({ asset, project }: PanelProps) {
  const prim = asset.files.find((f) => f.role === 'primary')!
  const q = useShaderDef(prim.url)
  const tex = asset.files.find((f) => f.role === 'texture')?.url
  const [code, setCode] = useState('')
  const [unifText, setUnifText] = useState('[]')
  const [values, setValues] = useState<Record<string, number[]>>({})
  const [err, setErr] = useState<string | null>(null)
  const [bg, setBg] = useState<Bg>('dark')
  const [instr, setInstr] = useState('')
  const { submit, busy } = useJobNav(project, 'Salvando shader…')
  const api_ = useQuery({ queryKey: ['refiners'], queryFn: api.refiners, staleTime: 60_000 })
  const claude = api_.data?.api_critic.available ?? false

  useEffect(() => {
    if (!q.data) return
    setCode(q.data.code)
    setUnifText(JSON.stringify(q.data.uniforms, null, 2))
    setValues({})
  }, [q.data])

  const parsed = useMemo((): { uniforms: Uniform[] | null; error: string | null } => {
    try {
      const u = JSON.parse(unifText) as Uniform[]
      if (!Array.isArray(u)) throw new Error('uniforms deve ser uma lista')
      return { uniforms: u, error: null }
    } catch (e) {
      return { uniforms: null, error: String(e) }
    }
  }, [unifText])

  if (!q.data) return <p className="p-4 text-sm text-muted-foreground">carregando…</p>
  const sd: ShaderDef = { ...q.data, code, uniforms: parsed.uniforms ?? q.data.uniforms }
  const dirty = code !== q.data.code || JSON.stringify(sd.uniforms) !== JSON.stringify(q.data.uniforms)

  return (
    <div className="grid h-full min-h-0 gap-3 overflow-auto p-3 max-lg:content-start lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] lg:overflow-hidden">
      <div className="flex flex-col gap-2 lg:min-h-0">
        <Textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          spellCheck={false}
          className="h-[50vh] resize-none font-mono text-xs leading-relaxed lg:h-auto lg:min-h-[320px] lg:flex-1"
          onKeyDown={(e) => {
            if (e.key === 'Tab') {
              e.preventDefault()
              const t = e.currentTarget
              const s = t.selectionStart
              setCode(code.slice(0, s) + '    ' + code.slice(t.selectionEnd))
              requestAnimationFrame(() => t.setSelectionRange(s + 4, s + 4))
            }
          }}
        />
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Uniforms (JSON) {parsed.error && <span className="text-red-400">— {parsed.error}</span>}</summary>
          <Textarea value={unifText} onChange={(e) => setUnifText(e.target.value)} spellCheck={false} className="mt-2 h-48 font-mono text-xs" />
        </details>
        <p className="text-xs text-muted-foreground">
          Escreva <code>vec4 effect(vec2 uv)</code>. Disponível: uTime, uProgress, uResolution, sampleTex(uv), seus uniforms e
          hash21, vnoise, fbm, voronoi, rot2, polar, hsv2rgb, sdCircle, sdBox, sdRing, glow, remap, emissive. Floats com ponto (2.0).
        </p>
      </div>
      <div className="flex flex-col gap-3 lg:min-h-0 lg:overflow-auto">
        <div className={cn('relative mx-auto flex aspect-square w-full max-w-[420px] shrink-0 items-center justify-center overflow-hidden rounded-md', BG_CLASS[bg])}>
          <ShaderCanvas sd={sd} values={values} texUrl={sd.uses_texture || code.includes('sampleTex') ? tex : null} size={384} onError={setErr} />
        </div>
        {err ? (
          <pre className="max-h-32 overflow-auto rounded bg-red-950/80 p-2 text-xs whitespace-pre-wrap text-red-200">{err}</pre>
        ) : (
          <p className="text-xs text-emerald-400">compila ✓</p>
        )}
        <div className="flex items-center gap-2">
          <BgToggle bg={bg} setBg={setBg} />
          <Button size="sm" className="ml-auto" disabled={!dirty || !!err || !!parsed.error || busy}
            onClick={() => submit('vfx.shader_edit', { input_asset: ref(asset), code, uniform_defs: parsed.uniforms, uniforms: values })}>
            <Save /> Salvar versão
          </Button>
          <ExportDialog asset={asset} project={project} initial="vfx.shader_code" trigger={<Button size="sm" variant="outline"><PackageOpen /></Button>} />
        </div>
        <UniformControls uniforms={sd.uniforms} values={values} onChange={(n, v) => setValues((s) => ({ ...s, [n]: v }))} />
        <div className="grid gap-2 border-t pt-3">
          <Input value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="Pedir mudança ao Claude: “mais lento, tons de verde, faíscas”" />
          <Button size="sm" variant="outline" disabled={!instr.trim() || !claude || busy} title={claude ? '' : api_.data?.api_critic.detail}
            onClick={() => submit('vfx.shader_edit', { input_asset: ref(asset), instruction: instr }, undefined, 'Claude reescrevendo o shader…')}>
            <Sparkles /> Aplicar com Claude
          </Button>
          {!claude && <p className="text-xs text-muted-foreground">Sem chave da Claude API: edite o código aqui ou peça ao agente (Claude Code via MCP: vfx.shader_edit com code).</p>}
        </div>
      </div>
    </div>
  )
}

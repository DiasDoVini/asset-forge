// Efeitos de camada não destrutivos (como no Pixelorama): lista ordenada, liga/desliga, parâmetros ao vivo, e a
// máscara de recorte. Os efeitos são aplicados na composição; o cel continua intacto (dá para desligar a qualquer hora).
import { ArrowDown, ArrowUp, ChevronDown, Eye, EyeOff, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { ADJUST_TITLES, hex, parseHex, ramp, type Adjust } from './color'
import { updateLayer, type Layer, type LayerEffect, type SpriteDoc } from './doc'
import type { RGBA } from './raster'

type Kind = Adjust['kind']
const EFFECT_KINDS: Kind[] = ['outline', 'shadow', 'hsv', 'brightness', 'invert', 'desaturate', 'posterize', 'gradient_map', 'replace', 'fit_palette']

function newEffect(kind: Kind, doc: SpriteDoc, fg: RGBA, bg: RGBA): LayerEffect {
  const a: Adjust =
    kind === 'outline' ? { kind, color: fg, place: 'outside', diagonal: false }
    : kind === 'shadow' ? { kind, color: [0, 0, 0, 160], dx: 1, dy: 1 }
    : kind === 'hsv' ? { kind, hue: 0, sat: 0, val: 0 }
    : kind === 'brightness' ? { kind, brightness: 0, contrast: 0 }
    : kind === 'posterize' ? { kind, levels: 4 }
    : kind === 'gradient_map' ? { kind, colors: ramp(fg, bg, 6) }
    : kind === 'replace' ? { kind, from: fg, to: bg, tolerance: 0 }
    : kind === 'fit_palette' ? { kind, palette: doc.palette, dither: false }
    : ({ kind } as Adjust)
  return { enabled: true, adjust: a, ...(kind === 'outline' ? { thickness: 1 } : {}) }
}

export function EffectsPanel({ doc, layer, commit, fg, bg }: {
  doc: SpriteDoc
  layer: Layer
  commit: (d: SpriteDoc, mergeKey?: string) => void
  fg: RGBA
  bg: RGBA
}) {
  const fx = layer.effects ?? []
  const setFx = (next: LayerEffect[], mergeKey?: string) => commit(updateLayer(doc, layer.id, { effects: next }), mergeKey)
  const patch = (i: number, p: Partial<LayerEffect> | { adjust: Partial<Adjust> }, live = false) =>
    setFx(fx.map((e, k) => (k === i ? ({ ...e, ...p, adjust: { ...e.adjust, ...('adjust' in p ? p.adjust : {}) } } as LayerEffect) : e)), live ? `fx:${layer.id}:${i}` : undefined)
  const move = (i: number, d: -1 | 1) => {
    const j = i + d
    if (j < 0 || j >= fx.length) return
    const n = [...fx]
    ;[n[i], n[j]] = [n[j], n[i]]
    setFx(n)
  }

  const num = (i: number, key: string, v: number, min: number, max: number, label: string) => (
    <label className="grid grid-cols-[64px_1fr_32px] items-center gap-1">
      <span className="text-muted-foreground">{label}</span>
      <input type="range" min={min} max={max} value={v} onChange={(e) => patch(i, { adjust: { [key]: Number(e.target.value) } as Partial<Adjust> }, true)} />
      <span className="text-right tabular-nums">{v}</span>
    </label>
  )
  const color = (i: number, key: string, c: RGBA, label: string) => (
    <label className="flex items-center gap-1">
      <span className="w-16 text-muted-foreground">{label}</span>
      <input type="color" className="h-5 w-7 rounded border bg-transparent" value={hex([c[0], c[1], c[2], 255])} onChange={(e) => {
        const p = parseHex(e.target.value)
        if (p) patch(i, { adjust: { [key]: [p[0], p[1], p[2], c[3]] } as Partial<Adjust> }, true)
      }} />
    </label>
  )

  return (
    <div className="shrink-0 space-y-1.5 border-t pt-2 text-xs">
      <div className="flex items-center gap-1">
        <span className="mr-auto font-medium">Efeitos <span className="font-normal text-muted-foreground">({layer.name})</span></span>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="xs" variant="ghost">Adicionar <ChevronDown /></Button>} />
          <DropdownMenuContent align="end">
            {EFFECT_KINDS.map((k) => (
              <DropdownMenuItem key={k} onClick={() => setFx([...fx, newEffect(k, doc, fg, bg)])}>{ADJUST_TITLES[k]}</DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <label className="flex items-center gap-1.5" title="A camada só aparece onde a camada de baixo tem pixels (Alt+clique entre camadas no Photoshop)">
        <input type="checkbox" checked={!!layer.clip} onChange={(e) => commit(updateLayer(doc, layer.id, { clip: e.target.checked }))} />
        Máscara de recorte (na camada de baixo)
      </label>
      {fx.length === 0 && <p className="text-[11px] text-muted-foreground">Contorno, sombra, matiz, gradient map… sem mexer nos pixels.</p>}
      {fx.map((e, i) => {
        const a = e.adjust
        return (
          <div key={i} className={cn('space-y-1 rounded border p-1.5', !e.enabled && 'opacity-60')}>
            <div className="flex items-center gap-0.5">
              <button onClick={() => patch(i, { enabled: !e.enabled })} title={e.enabled ? 'Desligar' : 'Ligar'}>{e.enabled ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}</button>
              <span className="ml-1 mr-auto font-medium">{ADJUST_TITLES[a.kind]}</span>
              <Button size="icon-xs" variant="ghost" onClick={() => move(i, -1)} title="Aplicar antes"><ArrowUp /></Button>
              <Button size="icon-xs" variant="ghost" onClick={() => move(i, 1)} title="Aplicar depois"><ArrowDown /></Button>
              <Button size="icon-xs" variant="ghost" onClick={() => setFx(fx.filter((_, k) => k !== i))} title="Remover"><Trash2 /></Button>
            </div>
            {a.kind === 'outline' && (
              <>
                {color(i, 'color', a.color, 'Cor')}
                <div className="flex items-center gap-2">
                  <select className="h-5 rounded border bg-transparent px-1" value={a.place} onChange={(ev) => patch(i, { adjust: { place: ev.target.value as 'outside' | 'inside' } })}>
                    <option value="outside">Por fora</option>
                    <option value="inside">Por dentro</option>
                  </select>
                  <label className="flex items-center gap-1"><input type="checkbox" checked={a.diagonal} onChange={(ev) => patch(i, { adjust: { diagonal: ev.target.checked } })} /> cantos</label>
                </div>
                <label className="grid grid-cols-[64px_1fr_32px] items-center gap-1">
                  <span className="text-muted-foreground">Espessura</span>
                  <input type="range" min={1} max={8} value={e.thickness ?? 1} onChange={(ev) => patch(i, { thickness: Number(ev.target.value) }, true)} />
                  <span className="text-right tabular-nums">{e.thickness ?? 1}</span>
                </label>
              </>
            )}
            {a.kind === 'shadow' && (
              <>
                {color(i, 'color', a.color, 'Cor')}
                {num(i, 'dx', a.dx, -16, 16, 'X')}
                {num(i, 'dy', a.dy, -16, 16, 'Y')}
              </>
            )}
            {a.kind === 'hsv' && (
              <>
                {num(i, 'hue', a.hue, -180, 180, 'Matiz')}
                {num(i, 'sat', a.sat, -100, 100, 'Saturação')}
                {num(i, 'val', a.val, -100, 100, 'Brilho')}
              </>
            )}
            {a.kind === 'brightness' && (
              <>
                {num(i, 'brightness', a.brightness, -100, 100, 'Brilho')}
                {num(i, 'contrast', a.contrast, -100, 100, 'Contraste')}
              </>
            )}
            {a.kind === 'posterize' && num(i, 'levels', a.levels, 2, 16, 'Níveis')}
            {a.kind === 'replace' && (
              <>
                {color(i, 'from', a.from, 'De')}
                {color(i, 'to', a.to, 'Para')}
                {num(i, 'tolerance', a.tolerance, 0, 255, 'Tolerância')}
              </>
            )}
            {a.kind === 'gradient_map' && (
              <div className="flex items-center gap-1">
                <div className="flex h-3 flex-1 overflow-hidden rounded">{a.colors.map((c, k) => <span key={k} className="flex-1" style={{ background: hex(c) }} />)}</div>
                <Button size="xs" variant="ghost" title="Usar principal → secundária" onClick={() => patch(i, { adjust: { colors: ramp(fg, bg, 6) } })}>↺</Button>
                <Button size="xs" variant="ghost" title="Usar a paleta, na ordem" disabled={!doc.palette.length} onClick={() => patch(i, { adjust: { colors: doc.palette.filter((c) => c[3] > 0) } })}>Paleta</Button>
              </div>
            )}
            {a.kind === 'fit_palette' && (
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1"><input type="checkbox" checked={a.dither} onChange={(ev) => patch(i, { adjust: { dither: ev.target.checked } })} /> dithering</label>
                <Button size="xs" variant="ghost" title="Atualizar com a paleta atual do sprite" onClick={() => patch(i, { adjust: { palette: doc.palette } })}>Paleta atual ({a.palette.length})</Button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

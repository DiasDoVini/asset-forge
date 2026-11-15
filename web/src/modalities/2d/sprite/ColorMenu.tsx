// Menu "Cor": modo de cor (RGB, indexado com ou sem dithering, tons de cinza) e ajustes com prévia ao vivo no canvas
// (matiz/saturação/brilho, brilho/contraste, inverter, dessaturar, posterizar, substituir cor, contorno, sombra,
// gradient map, ajustar à paleta), no cel, na camada ou no sprite inteiro, respeitando a seleção.
import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ADJUST_TITLES, hex, parseHex, ramp, type Adjust } from './color'
import { applyAdjust, setColorMode, type Scope } from './colorOps'
import type { SpriteDoc } from './doc'
import type { RGBA } from './raster'

type Kind = Adjust['kind']

const NEEDS_DIALOG: Kind[] = ['hsv', 'brightness', 'posterize', 'replace', 'outline', 'shadow', 'gradient_map', 'fit_palette']

export function ColorMenu({ doc, commit, layer, frame, mask, fg, bg, onPreview }: {
  doc: SpriteDoc
  commit: (d: SpriteDoc) => void
  layer: number
  frame: number
  mask: Uint8Array | null
  fg: RGBA
  bg: RGBA
  onPreview: (d: SpriteDoc | null) => void
}) {
  const [open, setOpen] = useState<Kind | null>(null)
  const quick = (k: Kind) => {
    if (NEEDS_DIALOG.includes(k)) return setOpen(k)
    commit(applyAdjust(doc, { kind: k } as Adjust, 'cel', layer, frame, mask))
  }
  const mode = (m: SpriteDoc['colorMode'], dither = false) => commit(setColorMode(doc, m, { dither }))
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button size="sm" variant="ghost">Cor <ChevronDown /></Button>} />
        <DropdownMenuContent align="start" className="w-64">
          <div className="px-2 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">Modo de cor</div>
          <DropdownMenuItem onClick={() => mode('rgb')}>{doc.colorMode === 'rgb' && <Check />} RGB</DropdownMenuItem>
          <DropdownMenuItem onClick={() => mode('indexed')}>{doc.colorMode === 'indexed' && <Check />} Indexado (paleta)</DropdownMenuItem>
          <DropdownMenuItem disabled={doc.colorMode === 'indexed'} onClick={() => mode('indexed', true)}>Indexado com dithering</DropdownMenuItem>
          <DropdownMenuItem onClick={() => mode('gray')}>{doc.colorMode === 'gray' && <Check />} Tons de cinza</DropdownMenuItem>
          <DropdownMenuSeparator />
          <div className="px-2 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">Ajustes {mask ? '(na seleção)' : ''}</div>
          {(Object.keys(ADJUST_TITLES) as Kind[]).map((k) => (
            <DropdownMenuItem key={k} onClick={() => quick(k)}>
              {ADJUST_TITLES[k]}{NEEDS_DIALOG.includes(k) ? '…' : ''}
              {!NEEDS_DIALOG.includes(k) && <span className="ml-auto text-xs text-muted-foreground">cel atual</span>}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {open && (
        <AdjustDialog
          kind={open}
          doc={doc}
          layer={layer}
          frame={frame}
          mask={mask}
          fg={fg}
          bg={bg}
          onPreview={onPreview}
          onClose={() => { onPreview(null); setOpen(null) }}
          onApply={(d) => { onPreview(null); commit(d); setOpen(null) }}
        />
      )}
    </>
  )
}

function defaults(kind: Kind, doc: SpriteDoc, fg: RGBA, bg: RGBA): Adjust {
  switch (kind) {
    case 'hsv': return { kind, hue: 0, sat: 0, val: 0 }
    case 'brightness': return { kind, brightness: 0, contrast: 0 }
    case 'posterize': return { kind, levels: 4 }
    case 'replace': return { kind, from: fg, to: bg, tolerance: 0 }
    case 'outline': return { kind, color: fg, place: 'outside', diagonal: false }
    case 'shadow': return { kind, color: fg, dx: 1, dy: 1 }
    case 'gradient_map': return { kind, colors: ramp(fg, bg, 8) }
    case 'fit_palette': return { kind, palette: doc.palette, dither: false }
    default: return { kind } as Adjust
  }
}

function AdjustDialog({ kind, doc, layer, frame, mask, fg, bg, onPreview, onClose, onApply }: {
  kind: Kind; doc: SpriteDoc; layer: number; frame: number; mask: Uint8Array | null; fg: RGBA; bg: RGBA
  onPreview: (d: SpriteDoc | null) => void; onClose: () => void; onApply: (d: SpriteDoc) => void
}) {
  const [a, setA] = useState<Adjust>(() => defaults(kind, doc, fg, bg))
  const [scope, setScope] = useState<Scope>('cel')
  const [gmSource, setGmSource] = useState<'fgbg' | 'palette'>('fgbg')
  const result = useMemo(() => applyAdjust(doc, a, scope, layer, frame, mask), [doc, a, scope, layer, frame, mask])
  useEffect(() => {
    const t = window.setTimeout(() => onPreview(result), 60)
    return () => window.clearTimeout(t)
  }, [result, onPreview])

  const set = (patch: Partial<Adjust>) => setA((x) => ({ ...x, ...patch }) as Adjust)
  const slider = (label: string, key: string, min: number, max: number, v: number) => (
    <label className="grid grid-cols-[110px_1fr_48px] items-center gap-2">
      <span className="text-muted-foreground">{label}</span>
      <input type="range" min={min} max={max} value={v} onChange={(e) => set({ [key]: Number(e.target.value) } as Partial<Adjust>)} />
      <Input type="number" className="h-6 px-1 text-xs" value={v} onChange={(e) => set({ [key]: Number(e.target.value) || 0 } as Partial<Adjust>)} />
    </label>
  )
  const colorField = (label: string, key: string, c: RGBA) => (
    <label className="flex items-center gap-2">
      <span className="w-[110px] text-muted-foreground">{label}</span>
      <input type="color" className="h-7 w-10 rounded border bg-transparent" value={hex([c[0], c[1], c[2], 255])} onChange={(e) => {
        const p = parseHex(e.target.value)
        if (p) set({ [key]: [p[0], p[1], p[2], 255] } as Partial<Adjust>)
      }} />
      <span className="font-mono text-xs text-muted-foreground">{hex(c)}</span>
    </label>
  )

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="sm:top-24 sm:right-6 sm:left-auto sm:max-w-md sm:translate-x-0 sm:translate-y-0"
        overlayClassName="bg-transparent supports-backdrop-filter:backdrop-blur-none"
      >
        <DialogHeader>
          <DialogTitle>{ADJUST_TITLES[kind]}</DialogTitle>
          <DialogDescription>A prévia aparece no canvas. {mask ? 'Só dentro da seleção.' : ''}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 text-sm">
          {a.kind === 'hsv' && (
            <>
              {slider('Matiz (°)', 'hue', -180, 180, a.hue)}
              {slider('Saturação', 'sat', -100, 100, a.sat)}
              {slider('Brilho', 'val', -100, 100, a.val)}
            </>
          )}
          {a.kind === 'brightness' && (
            <>
              {slider('Brilho', 'brightness', -100, 100, a.brightness)}
              {slider('Contraste', 'contrast', -100, 100, a.contrast)}
            </>
          )}
          {a.kind === 'posterize' && slider('Níveis por canal', 'levels', 2, 16, a.levels)}
          {a.kind === 'replace' && (
            <>
              {colorField('Cor de origem', 'from', a.from)}
              {colorField('Nova cor', 'to', a.to)}
              {slider('Tolerância', 'tolerance', 0, 255, a.tolerance)}
            </>
          )}
          {a.kind === 'outline' && (
            <>
              {colorField('Cor', 'color', a.color)}
              <label className="flex items-center gap-2">
                <span className="w-[110px] text-muted-foreground">Posição</span>
                <select className="h-7 rounded border bg-transparent px-1" value={a.place} onChange={(e) => set({ place: e.target.value as 'outside' | 'inside' })}>
                  <option value="outside">Por fora</option>
                  <option value="inside">Por dentro</option>
                </select>
                <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={a.diagonal} onChange={(e) => set({ diagonal: e.target.checked })} /> Cantos (8 vizinhos)</label>
              </label>
            </>
          )}
          {a.kind === 'shadow' && (
            <>
              {colorField('Cor', 'color', a.color)}
              {slider('Deslocamento X', 'dx', -16, 16, a.dx)}
              {slider('Deslocamento Y', 'dy', -16, 16, a.dy)}
            </>
          )}
          {a.kind === 'gradient_map' && (
            <>
              <label className="flex items-center gap-2">
                <span className="w-[110px] text-muted-foreground">Cores</span>
                <select className="h-7 rounded border bg-transparent px-1" value={gmSource} onChange={(e) => {
                  const v = e.target.value as 'fgbg' | 'palette'
                  setGmSource(v)
                  set({ colors: v === 'palette' ? doc.palette.filter((c) => c[3] > 0) : ramp(fg, bg, 8) })
                }}>
                  <option value="fgbg">Principal → secundária</option>
                  <option value="palette" disabled={!doc.palette.length}>Paleta, na ordem (escuro → claro)</option>
                </select>
              </label>
              <div className="flex h-4 overflow-hidden rounded">{a.colors.map((c, i) => <span key={i} className="flex-1" style={{ background: hex(c) }} />)}</div>
            </>
          )}
          {a.kind === 'fit_palette' && (
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={a.dither} onChange={(e) => set({ dither: e.target.checked })} /> Dithering ordenado (Bayer 4×4)
              <span className="ml-auto text-muted-foreground">{doc.palette.length} cores na paleta</span>
            </label>
          )}
          <label className="mt-1 flex items-center gap-2">
            <span className="w-[110px] text-muted-foreground">Aplicar em</span>
            <select className="h-7 rounded border bg-transparent px-1" value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
              <option value="cel">Cel atual</option>
              <option value="layer">Camada (todos os quadros)</option>
              <option value="sprite">Sprite inteiro</option>
            </select>
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => onApply(result)}>Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

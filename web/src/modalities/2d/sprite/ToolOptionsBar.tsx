// Barra de contexto (como a do Aseprite): só as opções que valem para a ferramenta atual.
import { FlipHorizontal2, FlipVertical2, Grid2x2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { BRUSH_SHAPES, INKS, type BrushShape, type Ink, type Symmetry } from './paint'
import type { SelMode } from './selection'
import { SELECT_TOOLS, type Tool, type ToolOptions } from './tools'
import type { Algo } from './transform'

const SEL_MODES: { id: SelMode; title: string; hint: string }[] = [
  { id: 'replace', title: 'Nova', hint: 'substitui a seleção' },
  { id: 'add', title: '+', hint: 'somar (Shift)' },
  { id: 'subtract', title: '−', hint: 'subtrair (Alt)' },
  { id: 'intersect', title: '∩', hint: 'intersectar (Shift+Alt)' },
]

export type FloatState = { x: number; y: number; w: number; h: number; angle: number }

/** Barra durante a transformação de uma seleção flutuante (colada ou levantada). */
export function TransformBar({ f, algo, setAlgo, onChange, onFlip, onRotate, onApply, onCancel }: {
  f: FloatState
  algo: Algo
  setAlgo: (a: Algo) => void
  onChange: (p: Partial<FloatState>) => void
  onFlip: (axis: 'h' | 'v') => void
  onRotate: (deg: number) => void
  onApply: () => void
  onCancel: () => void
}) {
  const field = (label: string, v: number, k: keyof FloatState, step = 1) => (
    <label className="flex items-center gap-1 text-muted-foreground">
      {label}
      <Input
        key={`${k}${Math.round(v * 100)}`}
        type="number"
        step={step}
        className="h-6 w-16 px-1 text-xs"
        defaultValue={Math.round(v * 100) / 100}
        onBlur={(e) => onChange({ [k]: Number(e.target.value) || 0 })}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    </label>
  )
  return (
    <div className="flex h-8 items-center gap-1.5 overflow-x-auto border-b bg-primary/5 px-2 text-xs whitespace-nowrap">
      <span className="font-medium">Transformar</span>
      {field('X', f.x, 'x')}
      {field('Y', f.y, 'y')}
      {field('L', f.w, 'w')}
      {field('A', f.h, 'h')}
      {field('Ângulo', f.angle, 'angle', 1)}
      <select className="h-6 rounded border bg-transparent px-1" value={algo} onChange={(e) => setAlgo(e.target.value as Algo)} title="Algoritmo de rotação/escala">
        <option value="rotsprite">RotSprite (pixel art)</option>
        <option value="nearest">Vizinho mais próximo</option>
      </select>
      <Button size="icon-xs" variant="ghost" title="Espelhar na horizontal" onClick={() => onFlip('h')}><FlipHorizontal2 /></Button>
      <Button size="icon-xs" variant="ghost" title="Espelhar na vertical" onClick={() => onFlip('v')}><FlipVertical2 /></Button>
      <Button size="xs" variant="ghost" title="Girar 90° anti-horário" onClick={() => onRotate(-90)}>↺ 90°</Button>
      <Button size="xs" variant="ghost" title="Girar 90° horário" onClick={() => onRotate(90)}>↻ 90°</Button>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">Arraste dentro para mover, nas alças para escalar (Shift: proporção), fora dos cantos para girar (Shift: 15°)</span>
      <Button size="xs" onClick={onApply}>Aplicar (Enter)</Button>
      <Button size="xs" variant="ghost" onClick={onCancel}>Cancelar (Esc)</Button>
    </div>
  )
}

const BRUSH_TOOLS: Tool[] = ['pencil', 'eraser', 'line', 'curve', 'rect', 'ellipse']
const INK_TOOLS: Tool[] = ['pencil', 'line', 'curve', 'rect', 'ellipse', 'polygon', 'contour', 'fill']
const SYM_TOOLS: Tool[] = ['pencil', 'eraser', 'line', 'curve', 'rect', 'ellipse', 'polygon', 'contour', 'fill']

function Sep() {
  return <span className="mx-1 h-4 border-l" />
}

function Toggle({ on, onClick, children, title }: { on: boolean; onClick: () => void; children: React.ReactNode; title?: string }) {
  return (
    <Button size="xs" variant={on ? 'secondary' : 'ghost'} onClick={onClick} title={title}>
      {children}
    </Button>
  )
}

export function ToolOptionsBar({ tool, opts, set, docW, docH }: {
  tool: Tool
  opts: ToolOptions
  set: <K extends keyof ToolOptions>(k: K, v: ToolOptions[K]) => void
  docW: number
  docH: number
}) {
  const shapeOutline = (tool === 'rect' || tool === 'ellipse') && !opts.filled
  const showBrush = BRUSH_TOOLS.includes(tool) && (tool !== 'rect' && tool !== 'ellipse' ? true : shapeOutline)
  const sym = opts.symmetry
  const setSym = (patch: Partial<Symmetry>) => {
    const next = { ...sym, ...patch }
    if (next.mode !== 'none' && sym.mode === 'none' && !sym.x && !sym.y) Object.assign(next, { x: docW / 2, y: docH / 2 })
    set('symmetry', next)
  }
  const num = (v: string, min: number, max: number) => Math.max(min, Math.min(max, Number(v) || 0))

  return (
    <div className="flex h-8 items-center gap-1 overflow-x-auto overflow-y-hidden border-b px-2 text-xs whitespace-nowrap">
      {showBrush && (
        <>
          <select className="h-6 rounded border bg-transparent px-1" value={opts.brush} onChange={(e) => set('brush', e.target.value as BrushShape)} title="Forma do pincel">
            {BRUSH_SHAPES.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
          </select>
          <Input type="number" min={1} max={64} className="h-6 w-12 px-1 text-xs" value={opts.size} onChange={(e) => set('size', num(e.target.value, 1, 64))} title="Tamanho do pincel ([ e ])" />
          <span className="text-muted-foreground">px</span>
          <Sep />
        </>
      )}

      {INK_TOOLS.includes(tool) && (
        <>
          <span className="text-muted-foreground">Ink</span>
          <select className="h-6 rounded border bg-transparent px-1" value={opts.ink} onChange={(e) => set('ink', e.target.value as Ink)} title={INKS.find((i) => i.id === opts.ink)?.hint}>
            {INKS.map((i) => <option key={i.id} value={i.id} title={i.hint}>{i.title}</option>)}
          </select>
          <Sep />
        </>
      )}

      {(tool === 'pencil' || tool === 'eraser') && (
        <>
          <Toggle on={opts.pixelPerfect} onClick={() => set('pixelPerfect', !opts.pixelPerfect)} title="Remove os cotovelos em L dos traços de 1 px">Pixel-perfect</Toggle>
          <span className="text-muted-foreground">Estabilizador</span>
          <Input type="number" min={0} max={64} className="h-6 w-12 px-1 text-xs" value={opts.stabilizer} onChange={(e) => set('stabilizer', num(e.target.value, 0, 64))} title="Distância (px de tela/zoom) que o mouse anda antes do pincel seguir; 0 = desligado" />
          <Sep />
        </>
      )}

      {(tool === 'rect' || tool === 'ellipse') && (
        <>
          <Toggle on={opts.filled} onClick={() => set('filled', !opts.filled)} title="Preenchido (U/Shift+U de novo alterna)">Preenchido</Toggle>
          <span className="text-muted-foreground">Shift: proporcional</span>
          <Sep />
        </>
      )}

      {SELECT_TOOLS.includes(tool) && (
        <>
          {SEL_MODES.map((m) => (
            <Toggle key={m.id} on={opts.selMode === m.id} onClick={() => set('selMode', m.id)} title={m.hint}>{m.title}</Toggle>
          ))}
          <Sep />
          {(tool === 'wand' || tool === 'sel_color') && (
            <>
              <span className="text-muted-foreground">Tolerância</span>
              <Input type="number" min={0} max={255} className="h-6 w-14 px-1 text-xs" value={opts.tolerance} onChange={(e) => set('tolerance', num(e.target.value, 0, 255))} />
              <Toggle on={opts.sampleAll} onClick={() => set('sampleAll', !opts.sampleAll)} title="Comparar na composição de todas as camadas">
                {opts.sampleAll ? 'Todas as camadas' : 'Camada atual'}
              </Toggle>
              <Sep />
            </>
          )}
          <span className="min-w-0 flex-1 truncate text-muted-foreground" title="Ctrl+A tudo, Ctrl+D nada, Ctrl+Shift+I inverter, Ctrl+C/X/V, Del apaga, V move/transforma">
            Ctrl+A tudo · Ctrl+D nada · Ctrl+Shift+I inverter · Ctrl+C/X/V · Del apaga · V transforma
          </span>
        </>
      )}

      {(tool === 'fill' || tool === 'picker') && (
        <>
          {tool === 'fill' && (
            <>
              <Toggle on={opts.contiguous} onClick={() => set('contiguous', !opts.contiguous)} title="Contíguo: só a região ligada. Desligado: todos os pixels dessa cor">
                {opts.contiguous ? 'Contíguo' : 'Global'}
              </Toggle>
              <span className="text-muted-foreground">Tolerância</span>
              <Input type="number" min={0} max={255} className="h-6 w-14 px-1 text-xs" value={opts.tolerance} onChange={(e) => set('tolerance', num(e.target.value, 0, 255))} />
            </>
          )}
          <Toggle on={opts.sampleAll} onClick={() => set('sampleAll', !opts.sampleAll)} title="Comparar/pegar cor na composição de todas as camadas (desligado: só a camada atual)">
            {opts.sampleAll ? 'Todas as camadas' : 'Camada atual'}
          </Toggle>
          <Sep />
        </>
      )}

      {tool === 'gradient' && (
        <>
          <select className="h-6 rounded border bg-transparent px-1" value={opts.gradient.kind} onChange={(e) => set('gradient', { ...opts.gradient, kind: e.target.value as 'linear' | 'radial' })}>
            <option value="linear">Linear</option>
            <option value="radial">Radial</option>
          </select>
          <select className="h-6 rounded border bg-transparent px-1" value={opts.gradient.dither} onChange={(e) => set('gradient', { ...opts.gradient, dither: e.target.value as ToolOptions['gradient']['dither'] })} title="Dithering ordenado: só as duas cores, em padrão (pixel art)">
            <option value="bayer2">Dither 2×2</option>
            <option value="bayer4">Dither 4×4</option>
            <option value="bayer8">Dither 8×8</option>
            <option value="none">Suave (sem dither)</option>
          </select>
          <Toggle on={opts.gradient.onlyOpaque} onClick={() => set('gradient', { ...opts.gradient, onlyOpaque: !opts.gradient.onlyOpaque })} title="Só onde a camada já tem pixels">Só nos pixels</Toggle>
          <span className="text-muted-foreground">cor principal → secundária</span>
          <Sep />
        </>
      )}

      {SYM_TOOLS.includes(tool) && (
        <>
          <span className="text-muted-foreground">Simetria</span>
          <Toggle on={sym.mode === 'h' || sym.mode === 'both'} onClick={() => setSym({ mode: sym.mode === 'h' ? 'none' : sym.mode === 'both' ? 'v' : sym.mode === 'v' ? 'both' : 'h' })} title="Espelho horizontal (eixo vertical)">
            <FlipHorizontal2 />
          </Toggle>
          <Toggle on={sym.mode === 'v' || sym.mode === 'both'} onClick={() => setSym({ mode: sym.mode === 'v' ? 'none' : sym.mode === 'both' ? 'h' : sym.mode === 'h' ? 'both' : 'v' })} title="Espelho vertical (eixo horizontal)">
            <FlipVertical2 />
          </Toggle>
          {sym.mode !== 'none' && (
            <>
              {(sym.mode === 'h' || sym.mode === 'both') && (
                <Input type="number" step={0.5} min={0} max={docW} className="h-6 w-14 px-1 text-xs" value={sym.x} onChange={(e) => setSym({ x: num(e.target.value, 0, docW) })} title="Posição do eixo vertical (em px; .5 = no meio de um pixel)" />
              )}
              {(sym.mode === 'v' || sym.mode === 'both') && (
                <Input type="number" step={0.5} min={0} max={docH} className="h-6 w-14 px-1 text-xs" value={sym.y} onChange={(e) => setSym({ y: num(e.target.value, 0, docH) })} title="Posição do eixo horizontal" />
              )}
              <Button size="icon-xs" variant="ghost" title="Centralizar eixos" onClick={() => setSym({ x: docW / 2, y: docH / 2 })}><Grid2x2 /></Button>
            </>
          )}
        </>
      )}

      {tool === 'curve' && <span className="min-w-0 flex-1 truncate text-muted-foreground" title="Arraste de uma ponta à outra; depois arraste o 1º e o 2º controle. Enter aplica, Esc cancela.">Arraste de uma ponta à outra; depois arraste o 1º e o 2º controle. Enter aplica, Esc cancela.</span>}
      {tool === 'polygon' && <span className="min-w-0 flex-1 truncate text-muted-foreground" title="Clique os vértices; duplo clique, Enter ou clicar no 1º ponto fecha. Esc cancela.">Clique os vértices; duplo clique, Enter ou clicar no 1º ponto fecha. Esc cancela.</span>}
      {tool === 'move' && (
        <>
          <span className="text-muted-foreground">Rotação/escala</span>
          <select className="h-6 rounded border bg-transparent px-1" value={opts.algo} onChange={(e) => set('algo', e.target.value as Algo)}>
            <option value="rotsprite">RotSprite (pixel art)</option>
            <option value="nearest">Vizinho mais próximo</option>
          </select>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">Com seleção: arraste dentro dela para levantar e transformar (Ctrl+T). Sem seleção: move o cel inteiro.</span>
        </>
      )}
    </div>
  )
}

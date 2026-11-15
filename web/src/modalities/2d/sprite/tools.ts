// Ferramentas do editor de sprite: definição, opções (com preferência salva no navegador) e o cálculo puro do resultado
// de cada traço/forma a partir da imagem de antes (`base`).
import { useEffect, useState } from 'react'
import { linePoints, type Pt } from '@/modalities/pixel/tools'
import {
  allPixels, applyInk, bezierPoints, coverage, ellipsePoints, gradientInto, pixelPerfect, polygonPoints, polyline, rectPoints,
  type BrushShape, type GradientOpts, type Ink, type Symmetry,
} from './paint'
import type { RGBA } from './raster'
import type { SelMode } from './selection'
import type { Algo } from './transform'

export type Tool =
  | 'pencil' | 'eraser' | 'fill' | 'gradient' | 'picker' | 'line' | 'curve' | 'rect' | 'ellipse' | 'polygon' | 'contour' | 'move'
  | 'sel_rect' | 'sel_ellipse' | 'lasso' | 'poly_lasso' | 'wand' | 'sel_color' | 'slice' | 'text'

export const SELECT_TOOLS: Tool[] = ['sel_rect', 'sel_ellipse', 'lasso', 'poly_lasso', 'wand', 'sel_color']

export type ToolDef = { id: Tool; label: string; key: string; shift?: boolean }

export const TOOL_DEFS: ToolDef[] = [
  { id: 'pencil', label: 'Lápis', key: 'b' },
  { id: 'eraser', label: 'Borracha', key: 'e' },
  { id: 'fill', label: 'Balde', key: 'g' },
  { id: 'gradient', label: 'Gradiente', key: 'g', shift: true },
  { id: 'picker', label: 'Conta-gotas', key: 'i' },
  { id: 'line', label: 'Linha', key: 'l' },
  { id: 'curve', label: 'Curva (arraste as pontas, depois os 2 controles)', key: 'l', shift: true },
  { id: 'rect', label: 'Retângulo', key: 'u' },
  { id: 'ellipse', label: 'Elipse', key: 'u', shift: true },
  { id: 'polygon', label: 'Polígono (clique os vértices; duplo clique ou Enter fecha)', key: 'd', shift: true },
  { id: 'contour', label: 'Contorno (desenhe à mão; ao soltar, preenche)', key: 'd' },
  { id: 'move', label: 'Mover / transformar (com seleção: move, escala e gira a seleção)', key: 'v' },
  { id: 'sel_rect', label: 'Seleção retangular', key: 'm' },
  { id: 'sel_ellipse', label: 'Seleção elíptica', key: 'm', shift: true },
  { id: 'lasso', label: 'Laço', key: 'q' },
  { id: 'poly_lasso', label: 'Laço poligonal (cliques; Enter fecha)', key: 'q', shift: true },
  { id: 'wand', label: 'Varinha mágica', key: 'w' },
  { id: 'sel_color', label: 'Selecionar por cor (em todo o cel)', key: 'w', shift: true },
  { id: 'slice', label: 'Fatia (arraste para criar; clique para selecionar e arrastar para mover)', key: 'c', shift: true },
  { id: 'text', label: 'Texto (clique onde começa)', key: 't' },
]

export type ToolOptions = {
  brush: BrushShape
  size: number
  ink: Ink
  pixelPerfect: boolean
  stabilizer: number // px de "corda"; 0 = desligado
  symmetry: Symmetry
  filled: boolean // retângulo, elipse
  contiguous: boolean // balde
  tolerance: number // balde 0..255
  sampleAll: boolean // balde/conta-gotas: compara na composição de todas as camadas
  gradient: { kind: GradientOpts['kind']; dither: GradientOpts['dither']; onlyOpaque: boolean }
  selMode: SelMode // seleção: substituir, somar (Shift), subtrair (Alt), intersectar (Shift+Alt)
  algo: Algo // transformação: RotSprite ou vizinho mais próximo
}

export const DEFAULT_OPTIONS: ToolOptions = {
  brush: 'square', size: 1, ink: 'simple', pixelPerfect: true, stabilizer: 0,
  symmetry: { mode: 'none', x: 0, y: 0 },
  filled: false, contiguous: true, tolerance: 0, sampleAll: false,
  gradient: { kind: 'linear', dither: 'bayer4', onlyOpaque: false },
  selMode: 'replace', algo: 'rotsprite',
}

const STORE = 'asset-forge.sprite-tools'

/** Opções das ferramentas, lembradas entre sessões (só neste navegador; falha silenciosa sem storage). */
export function useToolOptions() {
  const [opts, setOpts] = useState<ToolOptions>(() => {
    try {
      const raw = localStorage.getItem(STORE)
      if (raw) return { ...DEFAULT_OPTIONS, ...JSON.parse(raw), symmetry: DEFAULT_OPTIONS.symmetry }
    } catch {
      /* sem storage */
    }
    return DEFAULT_OPTIONS
  })
  useEffect(() => {
    try {
      const { symmetry: _s, ...keep } = opts
      localStorage.setItem(STORE, JSON.stringify(keep))
    } catch {
      /* sem storage */
    }
  }, [opts])
  const set = <K extends keyof ToolOptions>(k: K, v: ToolOptions[K]) => setOpts((o) => ({ ...o, [k]: v }))
  return { opts, set, setOpts }
}

// ---------------------------------------------------------------- traços

export type StrokeState = {
  tool: Tool
  base: Uint8ClampedArray
  points: Pt[] // pontos do mouse (à mão livre) ou [início, atual] nas formas
  color: RGBA | null
  step: 1 | -1 // sombreamento: esquerdo +1, direito -1
  shift: boolean // forma proporcional (quadrado/círculo)
}

/** Ponto final com Shift: mesma largura e altura (quadrado, círculo, linha a 45°). */
export function constrain(a: Pt, b: Pt, square: boolean): Pt {
  if (!square) return b
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const d = Math.max(Math.abs(dx), Math.abs(dy))
  return [a[0] + Math.sign(dx || 1) * d, a[1] + Math.sign(dy || 1) * d]
}

export type Ctx = { w: number; h: number; opts: ToolOptions; ramp: RGBA[]; wrap?: 'none' | 'x' | 'y' | 'both' }

/** Resultado do traço/forma: a imagem nova do cel. */
export function strokeResult(s: StrokeState, c: Ctx, extra?: { curve?: [Pt, Pt, Pt, Pt]; polygon?: Pt[]; gradTo?: RGBA }): Uint8ClampedArray {
  const { w, h, opts } = c
  const so = { w, h, brush: opts.brush, size: opts.size, symmetry: opts.symmetry, wrap: c.wrap }
  const out = s.base.slice()
  const ink = (pixels: Iterable<number>, color = s.color) =>
    applyInk(s.base, out, pixels, { ink: s.tool === 'eraser' ? 'simple' : opts.ink, color: s.tool === 'eraser' ? null : color, ramp: c.ramp, rampStep: s.step })
  const a = s.points[0]
  const b = s.points[s.points.length - 1]
  switch (s.tool) {
    case 'pencil':
    case 'eraser': {
      let path = polyline(s.points)
      if (opts.pixelPerfect && opts.size === 1) path = pixelPerfect(path)
      ink(coverage(path, so))
      break
    }
    case 'line': {
      const e = constrain(a, b, s.shift)
      ink(coverage(linePoints(a[0], a[1], e[0], e[1]), so))
      break
    }
    case 'rect':
    case 'ellipse': {
      const e = constrain(a, b, s.shift)
      const pts = s.tool === 'rect' ? rectPoints(a, e, opts.filled) : ellipsePoints(a, e, opts.filled)
      ink(coverage(pts, so, !opts.filled))
      break
    }
    case 'contour':
      ink(coverage(polygonPoints(s.points), so, false))
      break
    case 'curve': {
      const [p0, c1, c2, p3] = extra?.curve ?? [a, a, b, b]
      ink(coverage(bezierPoints(p0, c1, c2, p3), so))
      break
    }
    case 'polygon':
      ink(coverage(polygonPoints(extra?.polygon ?? s.points), so, false))
      break
    case 'gradient': {
      if (!s.color) break
      const to = extra?.gradTo ?? s.color
      const px = opts.gradient.onlyOpaque ? allPixels(w, h).filter((p) => s.base[p * 4 + 3] > 0) : allPixels(w, h)
      gradientInto(out, s.base, w, px, a, b, { kind: opts.gradient.kind, dither: opts.gradient.dither, from: s.color, to })
      break
    }
  }
  return out
}

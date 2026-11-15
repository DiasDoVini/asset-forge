import { describe, expect, it } from 'vitest'
import type { Pt } from '@/modalities/pixel/tools'
import {
  applyInk, bezierPoints, brushOffsets, coverage, ellipsePoints, fillRegion, gradientInto, mirror, pixelPerfect, polygonPoints,
  polyline, stabilize,
} from './paint'
import type { RGBA } from './raster'

const key = (pts: Pt[]) => new Set(pts.map(([x, y]) => `${x},${y}`))

describe('pincel e caminho', () => {
  it('formas de pincel', () => {
    expect(brushOffsets('square', 1)).toEqual([[0, 0]])
    expect(brushOffsets('square', 3)).toHaveLength(9)
    expect(brushOffsets('hline', 4).map((p) => p[1])).toEqual([0, 0, 0, 0])
    const circ = brushOffsets('circle', 5)
    expect(circ.length).toBeLessThan(25)
    expect(key(circ).has('0,0')).toBe(true)
    expect(key(circ).has('-2,-2')).toBe(false)
  })

  it('pixel-perfect remove cotovelos em L, mantém retas', () => {
    const l: Pt[] = [[0, 0], [1, 0], [1, 1], [2, 1], [2, 2]]
    expect(pixelPerfect(l)).toEqual([[0, 0], [1, 1], [2, 2]]) // escada de L vira diagonal limpa
    expect(pixelPerfect([[0, 0], [1, 0], [2, 0], [2, 1], [2, 2]])).toEqual([[0, 0], [1, 0], [2, 1], [2, 2]])
    const straight: Pt[] = [[0, 0], [1, 0], [2, 0], [3, 0]]
    expect(pixelPerfect(straight)).toEqual(straight)
  })

  it('polyline liga pontos sem buracos', () => {
    expect(polyline([[0, 0], [3, 0]])).toEqual([[0, 0], [1, 0], [2, 0], [3, 0]])
  })

  it('estabilizador segura até a corda esticar', () => {
    expect(stabilize([0, 0], [3, 0], 5)).toEqual([0, 0])
    expect(stabilize([0, 0], [10, 0], 5)).toEqual([5, 0])
  })

  it('simetria horizontal, vertical e nas duas', () => {
    const s = { x: 4, y: 4 }
    expect(mirror([[0, 1]], { mode: 'h', ...s })).toEqual([[0, 1], [7, 1]])
    expect(mirror([[0, 1]], { mode: 'v', ...s })).toEqual([[0, 1], [0, 6]])
    expect(key(mirror([[0, 1]], { mode: 'both', ...s }))).toEqual(new Set(['0,1', '7,1', '0,6', '7,6']))
  })

  it('cobertura recorta ao canvas', () => {
    const c = coverage([[0, 0]], { w: 4, h: 4, brush: 'square', size: 3, symmetry: { mode: 'none', x: 2, y: 2 } })
    expect(c.size).toBe(4) // só o quadrante dentro do canvas
  })
})

describe('formas', () => {
  it('elipse: simétrica, preenchida contém o contorno', () => {
    const outline = ellipsePoints([0, 0], [8, 6], false)
    const filled = ellipsePoints([0, 0], [8, 6], true)
    const k = key(outline)
    for (const [x, y] of outline) {
      expect(k.has(`${8 - x},${y}`)).toBe(true)
      expect(k.has(`${x},${6 - y}`)).toBe(true)
    }
    const f = key(filled)
    for (const p of k) expect(f.has(p)).toBe(true)
    expect(f.has('4,3')).toBe(true)
    expect(k.has('4,3')).toBe(false)
    expect(k.has('0,0')).toBe(false) // canto fora da elipse
  })

  it('curva de bézier passa pelas pontas', () => {
    const pts = bezierPoints([0, 0], [0, 10], [10, 10], [10, 0])
    expect(pts[0]).toEqual([0, 0])
    expect(pts[pts.length - 1]).toEqual([10, 0])
    expect(Math.max(...pts.map((p) => p[1]))).toBeGreaterThan(5)
  })

  it('polígono preenche o triângulo', () => {
    const k = key(polygonPoints([[0, 0], [8, 0], [0, 8]]))
    expect(k.has('1,1')).toBe(true)
    expect(k.has('7,7')).toBe(false)
  })
})

describe('inks', () => {
  const px = (r: number, g: number, b: number, a: number) => new Uint8ClampedArray([r, g, b, a])
  const run = (base: Uint8ClampedArray, opts: Parameters<typeof applyInk>[3]) => {
    const out = base.slice()
    applyInk(base, out, [0], opts)
    return Array.from(out)
  }
  const red: RGBA = [255, 0, 0, 255]
  const halfBlue: RGBA = [0, 0, 255, 128]

  it('simples, alfa, copiar e travar alfa', () => {
    expect(run(px(0, 255, 0, 255), { ink: 'simple', color: red })).toEqual([255, 0, 0, 255])
    const mixed = run(px(255, 0, 0, 255), { ink: 'simple', color: halfBlue })
    expect(mixed[0]).toBeGreaterThan(100)
    expect(mixed[2]).toBeGreaterThan(100)
    expect(run(px(255, 0, 0, 255), { ink: 'copy', color: halfBlue })).toEqual([0, 0, 255, 128])
    expect(run(px(0, 0, 0, 0), { ink: 'lock_alpha', color: red })).toEqual([0, 0, 0, 0])
    expect(run(px(0, 255, 0, 100), { ink: 'lock_alpha', color: red })).toEqual([255, 0, 0, 100])
    expect(run(px(9, 9, 9, 255), { ink: 'simple', color: [0, 0, 0, 0] })).toEqual([0, 0, 0, 0])
    expect(run(px(9, 9, 9, 255), { ink: 'simple', color: null })).toEqual([0, 0, 0, 0])
  })

  it('sombreamento anda na rampa e ignora cores fora dela', () => {
    const ramp: RGBA[] = [[10, 10, 10, 255], [50, 50, 50, 255], [90, 90, 90, 255]]
    expect(run(px(50, 50, 50, 255), { ink: 'shading', color: null, ramp, rampStep: 1 })).toEqual([90, 90, 90, 255])
    expect(run(px(50, 50, 50, 255), { ink: 'shading', color: null, ramp, rampStep: -1 })).toEqual([10, 10, 10, 255])
    expect(run(px(90, 90, 90, 255), { ink: 'shading', color: null, ramp, rampStep: 1 })).toEqual([90, 90, 90, 255])
    expect(run(px(1, 2, 3, 255), { ink: 'shading', color: null, ramp, rampStep: 1 })).toEqual([1, 2, 3, 255])
  })
})

describe('balde e gradiente', () => {
  // 4x1: vermelho, vermelho-quase, azul, vermelho
  const row = new Uint8ClampedArray([255, 0, 0, 255, 250, 0, 0, 255, 0, 0, 255, 255, 255, 0, 0, 255])
  it('contíguo, tolerância e global', () => {
    expect([...fillRegion(row, 4, 1, 0, 0, { contiguous: true, tolerance: 0 })]).toEqual([0])
    expect(new Set(fillRegion(row, 4, 1, 0, 0, { contiguous: true, tolerance: 10 }))).toEqual(new Set([0, 1]))
    expect(new Set(fillRegion(row, 4, 1, 0, 0, { contiguous: false, tolerance: 0 }))).toEqual(new Set([0, 3]))
  })

  it('gradiente com dithering usa só as duas cores e vai de uma à outra', () => {
    const w = 16
    const base = new Uint8ClampedArray(w * 4)
    const out = base.slice()
    const a: RGBA = [0, 0, 0, 255]
    const b: RGBA = [255, 255, 255, 255]
    gradientInto(out, base, w, Array.from({ length: w }, (_, i) => i), [0, 0], [15, 0], { kind: 'linear', dither: 'bayer4', from: a, to: b })
    const vals = Array.from({ length: w }, (_, i) => out[i * 4])
    expect(new Set(vals)).toEqual(new Set([0, 255]))
    expect(vals[0]).toBe(0)
    expect(vals[15]).toBe(255)
    const smooth = base.slice()
    gradientInto(smooth, base, w, Array.from({ length: w }, (_, i) => i), [0, 0], [15, 0], { kind: 'linear', dither: 'none', from: a, to: b })
    expect(smooth[7 * 4]).toBeGreaterThan(100)
    expect(smooth[7 * 4]).toBeLessThan(140)
  })
})

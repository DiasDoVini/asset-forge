import { describe, expect, it } from 'vitest'
import { anchorOffset, blankDoc, contentBounds, cropDoc, getCel, insertFrame, resizeCanvas, writeCel } from './doc'
import {
  bounds, cleared, clipToMask, colorMask, combine, ellipseMask, extract, invert, maskFromImage, outline, polygonMask, rectMask,
} from './selection'
import { flipSprite, rotateSprite, scaleSprite } from './spriteOps'
import { flip, resizeNearest, rotate90, scale2x, stampOver, transformImage } from './transform'

const img = (w: number, h: number, fn: (x: number, y: number) => [number, number, number, number]) => {
  const data = new Uint8ClampedArray(w * h * 4)
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data.set(fn(x, y), (y * w + x) * 4)
  return { w, h, data }
}
const at = (i: { w: number; data: Uint8ClampedArray }, x: number, y: number) => Array.from(i.data.slice((y * i.w + x) * 4, (y * i.w + x) * 4 + 4))
const count = (m: Uint8Array) => m.reduce((s, v) => s + v, 0)

describe('seleção', () => {
  it('retângulo, elipse, polígono e combinações', () => {
    const r = rectMask(8, 8, [1, 1], [4, 3])
    expect(count(r)).toBe(12)
    expect(bounds(r, 8)).toEqual({ x: 1, y: 1, w: 4, h: 3 })
    const e = ellipseMask(8, 8, [0, 0], [7, 7])
    expect(e[0]).toBe(0)
    expect(e[3 * 8 + 3]).toBe(1)
    expect(count(polygonMask(8, 8, [[0, 0], [7, 0], [0, 7]]))).toBeGreaterThan(20)
    const b = rectMask(8, 8, [3, 1], [6, 3])
    expect(count(combine(r, b, 'add'))).toBe(18)
    expect(count(combine(r, b, 'subtract'))).toBe(6)
    expect(count(combine(r, b, 'intersect'))).toBe(6)
    expect(count(invert(r))).toBe(64 - 12)
  })

  it('varinha (contígua) e por cor (global)', () => {
    const i = img(4, 1, (x) => (x === 2 ? [0, 0, 255, 255] : [255, 0, 0, 255]))
    expect(Array.from(colorMask(i.data, 4, 1, [0, 0], true, 0))).toEqual([1, 1, 0, 0])
    expect(Array.from(colorMask(i.data, 4, 1, [0, 0], false, 0))).toEqual([1, 1, 0, 1])
  })

  it('extrair, apagar, recortar a pintura e borda', () => {
    const i = img(4, 4, (x, y) => [x * 10, y * 10, 0, 255])
    const m = rectMask(4, 4, [1, 1], [2, 2])
    const ex = extract(i.data, 4, m)!
    expect([ex.x, ex.y, ex.w, ex.h]).toEqual([1, 1, 2, 2])
    expect(at(ex, 0, 0)).toEqual([10, 10, 0, 255])
    const cl = cleared(i.data, m)
    expect(at({ w: 4, data: cl }, 1, 1)[3]).toBe(0)
    expect(at({ w: 4, data: cl }, 0, 0)[3]).toBe(255)
    const painted = new Uint8ClampedArray(i.data.length).fill(7)
    clipToMask(painted, i.data, m)
    expect(at({ w: 4, data: painted }, 0, 0)).toEqual([0, 0, 0, 255]) // fora: volta o original
    expect(at({ w: 4, data: painted }, 1, 1)).toEqual([7, 7, 7, 7])
    expect(outline(m, 4, 4)).toHaveLength(8)
    expect(count(maskFromImage(4, 4, ex, 1, 1))).toBe(4)
  })
})

describe('transformações', () => {
  it('scale2x arredonda a diagonal sem inventar cores', () => {
    const d = img(2, 2, (x, y) => (x === y ? [255, 255, 255, 255] : [0, 0, 0, 0]))
    const s = scale2x(d)
    expect([s.w, s.h]).toEqual([4, 4])
    const colors = new Set<string>()
    for (let i = 0; i < s.data.length; i += 4) colors.add(s.data.slice(i, i + 4).join())
    expect(colors.size).toBe(2)
  })

  it('girar 90°, espelhar e escalar', () => {
    const i = img(3, 2, (x, y) => [x, y, 0, 255])
    const r = rotate90(i, 1)
    expect([r.w, r.h]).toEqual([2, 3])
    expect(at(r, 1, 0)).toEqual([0, 0, 0, 255]) // canto superior esquerdo vai para o superior direito
    expect(at(rotate90(rotate90(rotate90(r, 1), 1), 1), 0, 0)).toEqual(at(i, 0, 0))
    expect(at(flip(i, 'h'), 0, 0)).toEqual(at(i, 2, 0))
    const big = resizeNearest(i, 6, 4)
    expect(at(big, 5, 3)).toEqual(at(i, 2, 1))
  })

  it('RotSprite: 90° dá o mesmo que rotate90, cores preservadas, escala ×2', () => {
    const i = img(4, 4, (x, y) => (y === 0 ? [255, 0, 0, 255] : x === 0 ? [0, 255, 0, 255] : [0, 0, 0, 0]))
    const t = transformImage(i, { sx: 1, sy: 1, angle: 90, algo: 'rotsprite' })
    expect([t.w, t.h]).toEqual([4, 4])
    const ref = rotate90(i, 1)
    expect(Array.from(t.data)).toEqual(Array.from(ref.data))
    const r45 = transformImage(i, { sx: 1, sy: 1, angle: 45, algo: 'rotsprite' })
    const allowed = new Set(['255,0,0,255', '0,255,0,255', '0,0,0,0'])
    for (let k = 0; k < r45.data.length; k += 4) expect(allowed.has(r45.data.slice(k, k + 4).join())).toBe(true)
    const s2 = transformImage(i, { sx: 2, sy: 2, angle: 0, algo: 'nearest' })
    expect([s2.w, s2.h, s2.dx, s2.dy]).toEqual([8, 8, -4, -4])
    const fl = transformImage(i, { sx: -1, sy: 1, angle: 0, algo: 'nearest' })
    expect(at(fl, 3, 0)).toEqual(at(i, 0, 0))
  })

  it('carimbar com alfa', () => {
    const dst = new Uint8ClampedArray(4 * 4 * 4)
    stampOver(dst, 4, 4, img(2, 2, () => [9, 9, 9, 255]), 3, 3) // só 1 px cai dentro
    expect(dst.filter((_, k) => k % 4 === 3 && dst[k] === 255)).toHaveLength(1)
  })
})

describe('sprite inteiro', () => {
  const doc0 = () => {
    let d = blankDoc(4, 2)
    d = writeCel(d, 1, 0, img(4, 2, (x, y) => (x === 0 && y === 0 ? [255, 0, 0, 255] : [0, 0, 0, 0])).data)
    return insertFrame(d, 0, 'linked')
  }

  it('redimensionar canvas com âncora preserva ligações', () => {
    const d = doc0()
    const [dx, dy] = anchorOffset(d, 6, 4, 1, 1)
    expect([dx, dy]).toEqual([1, 1])
    const r = resizeCanvas(d, 6, 4, dx, dy)
    expect([r.w, r.h]).toEqual([6, 4])
    expect(at({ w: 6, data: getCel(r, 1, 0)!.data }, 1, 1)).toEqual([255, 0, 0, 255])
    expect(getCel(r, 1, 0)).toBe(getCel(r, 1, 1))
  })

  it('aparar, escalar, espelhar e girar', () => {
    const d = doc0()
    expect(contentBounds(d)).toEqual({ x: 0, y: 0, w: 1, h: 1 })
    expect(cropDoc(d, { x: 0, y: 0, w: 1, h: 1 }).w).toBe(1)
    const s = scaleSprite(d, 8, 4)
    expect(at({ w: 8, data: getCel(s, 1, 0)!.data }, 1, 1)).toEqual([255, 0, 0, 255])
    expect(at({ w: 4, data: getCel(flipSprite(d, 'h'), 1, 0)!.data }, 3, 0)).toEqual([255, 0, 0, 255])
    const rot = rotateSprite(d, 1)
    expect([rot.w, rot.h]).toEqual([2, 4])
    expect(at({ w: 2, data: getCel(rot, 1, 0)!.data }, 1, 0)).toEqual([255, 0, 0, 255])
  })
})

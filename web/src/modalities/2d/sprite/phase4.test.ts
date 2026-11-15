import { describe, expect, it } from 'vitest'
import { readAseprite, writeAseprite } from './aseprite'
import {
  adjust, hsvToRgb, makeNearest, oklabToRgb, parseHex, quantize, ramp, reducePalette, remapColor, rgbToHsv, rgbToOklab, sortPalette,
  toGray, uniqueColors,
} from './color'
import { blankDoc, celKey, getCel, writeCel } from './doc'
import {
  parseAdobeAse, parseAsepritePalette, parseGpl, parseHexList, parseJascPal, parseRiffPal, writeAdobeAse, writeGpl, writeHex,
  writeJascPal, writePdnTxt,
} from './paletteIO'
import type { RGBA } from './raster'

const P: RGBA[] = [[0, 0, 0, 255], [255, 0, 0, 255], [0, 0, 255, 255], [255, 255, 255, 255]]
const px = (d: Uint8ClampedArray, i = 0) => Array.from(d.slice(i * 4, i * 4 + 4))

describe('conversões', () => {
  it('HSV e OKLab vão e voltam', () => {
    const [h, s, v] = rgbToHsv(200, 100, 50)
    expect(hsvToRgb(h, s, v)).toEqual([200, 100, 50])
    const lab = rgbToOklab(12, 200, 99)
    expect(oklabToRgb(...lab)).toEqual([12, 200, 99])
    expect(parseHex('#ff8800')).toEqual([255, 136, 0, 255])
    expect(parseHex('ff880080')).toEqual([255, 136, 0, 128])
    expect(parseHex('zz')).toBeNull()
  })

  it('cor mais próxima e quantização (alfa binário)', () => {
    const near = makeNearest([[0, 0, 0, 0], ...P])
    expect(near(250, 10, 10, 255)).toBe(2)
    expect(near(1, 2, 3, 0)).toBe(0)
    const q = quantize(new Uint8ClampedArray([240, 20, 20, 255, 9, 9, 9, 100, 10, 10, 250, 200]), 3, P)
    expect(px(q, 0)).toEqual([255, 0, 0, 255])
    expect(px(q, 1)).toEqual([0, 0, 0, 0])
    expect(px(q, 2)).toEqual([0, 0, 255, 255])
  })

  it('dithering alterna entre as duas cores mais próximas', () => {
    const w = 8
    const grey = new Uint8ClampedArray(w * 4).fill(128)
    for (let i = 3; i < grey.length; i += 4) grey[i] = 255
    const q = quantize(grey, w, [[0, 0, 0, 255], [255, 255, 255, 255]], true)
    const vals = new Set(Array.from({ length: w }, (_, i) => q[i * 4]))
    expect(vals).toEqual(new Set([0, 255]))
  })

  it('extrair, reduzir, rampa e ordenar', () => {
    const d = new Uint8ClampedArray([255, 0, 0, 255, 255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 0, 0])
    expect(uniqueColors([d])).toEqual([[255, 0, 0, 255], [0, 255, 0, 255]])
    expect(reducePalette([d], 1)).toHaveLength(1)
    const r = ramp([0, 0, 0, 255], [255, 255, 255, 255], 5)
    expect(r).toHaveLength(5)
    expect(r[0]).toEqual([0, 0, 0, 255])
    expect(r[4]).toEqual([255, 255, 255, 255])
    expect(sortPalette(P, 'luma')[0]).toEqual([0, 0, 0, 255])
  })
})

describe('ajustes', () => {
  const img = new Uint8ClampedArray([200, 50, 50, 255, 0, 0, 0, 0, 10, 20, 30, 255, 0, 0, 0, 0])
  it('inverter, dessaturar, posterizar, HSV e brilho', () => {
    expect(px(adjust(img, 2, 2, { kind: 'invert' }))).toEqual([55, 205, 205, 255])
    expect(px(adjust(img, 2, 2, { kind: 'invert' }), 1)).toEqual([0, 0, 0, 0]) // transparente fica
    const g = px(adjust(img, 2, 2, { kind: 'desaturate' }))
    expect(g[0]).toBe(g[1])
    expect(px(adjust(img, 2, 2, { kind: 'posterize', levels: 2 }))).toEqual([255, 0, 0, 255])
    const shifted = px(adjust(img, 2, 2, { kind: 'hsv', hue: 120, sat: 0, val: 0 }))
    expect(shifted[1]).toBeGreaterThan(shifted[0])
    expect(px(adjust(img, 2, 2, { kind: 'brightness', brightness: 100, contrast: 0 }))[0]).toBe(255)
  })

  it('contorno por fora/por dentro, sombra, substituir, gradient map e máscara', () => {
    const one = new Uint8ClampedArray(3 * 3 * 4)
    one.set([9, 9, 9, 255], 4 * 4) // pixel do meio
    const red: RGBA = [255, 0, 0, 255]
    const out = adjust(one, 3, 3, { kind: 'outline', color: red, place: 'outside', diagonal: false })
    expect(px(out, 1)).toEqual([255, 0, 0, 255])
    expect(px(out, 0)).toEqual([0, 0, 0, 0])
    expect(px(adjust(one, 3, 3, { kind: 'outline', color: red, place: 'inside', diagonal: false }), 4)).toEqual([255, 0, 0, 255])
    expect(px(adjust(one, 3, 3, { kind: 'shadow', color: red, dx: 1, dy: 1 }), 8)).toEqual([255, 0, 0, 255])
    expect(px(adjust(img, 2, 2, { kind: 'replace', from: [200, 50, 50, 255], to: [1, 2, 3, 255], tolerance: 0 }))).toEqual([1, 2, 3, 255])
    expect(px(adjust(img, 2, 2, { kind: 'gradient_map', colors: [[0, 0, 0, 255], [255, 255, 255, 255]] }), 2)).toEqual([0, 0, 0, 255])
    const mask = new Uint8Array([0, 0, 1, 0])
    expect(px(adjust(img, 2, 2, { kind: 'invert' }, mask))).toEqual([200, 50, 50, 255])
    expect(px(remapColor(img, [200, 50, 50, 255], [7, 7, 7, 255]))).toEqual([7, 7, 7, 255])
    expect(px(toGray(img))[0]).toBe(px(toGray(img))[2])
  })
})

describe('formatos de paleta', () => {
  it('GPL, JASC, HEX, Paint.NET e Adobe ASE vão e voltam', () => {
    expect(parseGpl(writeGpl(P))).toEqual(P)
    expect(parseJascPal(writeJascPal(P))).toEqual(P)
    expect(parseHexList(writeHex(P))).toEqual(P)
    expect(parseHexList(writePdnTxt(P))).toEqual(P)
    const ase = writeAdobeAse(P)
    expect(parseAdobeAse(ase.buffer as ArrayBuffer)).toEqual(P)
    expect(() => parseGpl('oi')).toThrow()
  })

  it('RIFF PAL binário', () => {
    const n = 2
    const buf = new ArrayBuffer(24 + n * 4)
    const dv = new DataView(buf)
    const w4 = (o: number, s: string) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)))
    w4(0, 'RIFF')
    dv.setUint32(4, buf.byteLength - 8, true)
    w4(8, 'PAL ')
    w4(12, 'data')
    dv.setUint32(16, 4 + n * 4, true)
    dv.setUint16(20, 0x300, true)
    dv.setUint16(22, n, true)
    ;[[1, 2, 3], [4, 5, 6]].forEach((c, i) => c.forEach((v, k) => dv.setUint8(24 + i * 4 + k, v)))
    expect(parseRiffPal(buf)).toEqual([[1, 2, 3, 255], [4, 5, 6, 255]])
  })
})

describe('.aseprite com paleta e modos de cor', () => {
  const docWith = (mode: 'rgb' | 'indexed' | 'gray') => {
    let d = blankDoc(2, 1)
    d.palette = [...P]
    d.colorMode = mode
    d = writeCel(d, 1, 0, new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 0, 0]))
    return d
  }

  it('RGB grava e lê a paleta', async () => {
    const back = (await readAseprite((await writeAseprite(docWith('rgb'))).buffer as ArrayBuffer)).doc
    expect(back.colorMode).toBe('rgb')
    expect(back.palette).toEqual(P)
  })

  it('indexado grava índices e lê de volta as cores; paleta embutida é legível', async () => {
    const bytes = await writeAseprite(docWith('indexed'))
    const back = (await readAseprite(bytes.buffer as ArrayBuffer)).doc
    expect(back.colorMode).toBe('indexed')
    const cel = getCel(back, back.layers[0].id, 0)!
    expect(px(cel.data, 0)).toEqual([255, 0, 0, 255])
    expect(px(cel.data, 1)[3]).toBe(0)
    expect(back.palette.slice(0, 4)).toEqual(P)
    expect(back.palette[4][3]).toBe(0) // entrada transparente acrescentada
    expect(parseAsepritePalette(bytes.buffer as ArrayBuffer).slice(0, 4)).toEqual(P)
  })

  it('tons de cinza grava 16 bits', async () => {
    const back = (await readAseprite((await writeAseprite(docWith('gray'))).buffer as ArrayBuffer)).doc
    expect(back.colorMode).toBe('gray')
    const c = back.cels[celKey(back.layers[0].id, 0)]
    const [r, g, b] = px(c.data, 0)
    expect(r).toBe(g)
    expect(g).toBe(b)
  })
})

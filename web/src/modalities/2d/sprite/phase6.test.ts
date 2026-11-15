import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { readAseprite, writeAseprite } from './aseprite'
import { blankDoc, celKey, getCel, newCelImage, renderFrame, updateLayer } from './doc'
import { lzf, parseXml, readKra, readPsd, readPxo } from './formats/layered'
import { comboOf, conflicts, defaults, keymap } from './shortcuts'

const fixture = (name: string) => {
  const b = readFileSync(resolve(__dirname, '../../../../../packages/forge_2d/tests/fixtures', name))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const px = (d: Uint8ClampedArray, x: number, y: number, w = 4) => Array.from(d.slice((y * w + x) * 4, (y * w + x) * 4 + 4))

describe('formatos em camadas', () => {
  it('lê .pxo (camadas, quadros, durações e tags)', async () => {
    const d = await readPxo(fixture('sample.pxo'))
    expect([d.w, d.h]).toEqual([4, 2])
    expect(d.layers.map((l) => [l.name, l.opacity, l.locked])).toEqual([['fundo', 255, false], ['topo', 128, true]])
    expect(d.frames.map((f) => f.duration)).toEqual([100, 200])
    expect(d.tags).toMatchObject([{ name: 'walk', from: 0, to: 1, color: '#ff0000' }])
    expect(px(getCel(d, 2, 0)!.data, 0, 0)).toEqual([0, 0, 255, 255])
    expect(getCel(d, 2, 1)).toBeUndefined() // quadro vazio não vira cel
    expect(px(getCel(d, 1, 1)!.data, 3, 1)).toEqual([255, 0, 0, 255])
  })

  it('lê .psd (grupos por divisores, nome unicode, RLE, deslocamento)', () => {
    const d = readPsd(fixture('sample.psd'))
    expect(d.layers.map((l) => [l.name, l.kind, l.parent, l.opacity])).toEqual([
      ['fundo', 'image', null, 255], ['Grupo X', 'group', null, 200], ['Ponto ✓', 'image', 2, 255],
    ])
    expect(px(getCel(d, 1, 0)!.data, 0, 0)).toEqual([255, 255, 255, 255])
    const pt = getCel(d, 3, 0)!.data
    expect(px(pt, 2, 1)).toEqual([255, 0, 0, 255])
    expect(px(pt, 1, 1)).toEqual([0, 0, 0, 0])
  })

  it('lê .kra (árvore de camadas, deslocamento, modo de mistura, fps)', async () => {
    const d = await readKra(fixture('sample.kra'))
    expect(d.layers.map((l) => [l.name, l.kind, l.parent, l.opacity, l.blend])).toEqual([
      ['fundo', 'image', null, 255, 'normal'], ['Grupo & cia', 'group', null, 200, 'normal'], ['ponto', 'image', 2, 255, 'multiply'],
    ])
    expect(d.frames[0].duration).toBe(125)
    expect(px(getCel(d, 1, 0)!.data, 3, 1)).toEqual([0, 255, 0, 255])
    const pt = getCel(d, 3, 0)!.data
    expect(px(pt, 1, 0)).toEqual([255, 0, 0, 255])
    expect(px(pt, 0, 0)).toEqual([0, 0, 0, 0])
  })

  it('o documento lido vira .aseprite e volta igual', async () => {
    const d = await readKra(fixture('sample.kra'))
    const back = (await readAseprite((await writeAseprite(d)).buffer as ArrayBuffer)).doc
    expect(back.layers.map((l) => [l.name, l.kind, l.blend])).toEqual(d.layers.map((l) => [l.name, l.kind, l.blend]))
    expect(Array.from(renderFrame(back, 0))).toEqual(Array.from(renderFrame(d, 0)))
  })

  it('LZF: literais e referências', () => {
    // "abcabcabc": literal "abc" (ctrl 2) + referência len 6 (ctrl (4<<5)|0, off 2)
    const out = lzf(new Uint8Array([2, 97, 98, 99, (4 << 5) | 0, 2]), 9)
    expect(new TextDecoder().decode(out)).toBe('abcabcabc')
  })

  it('XML: atributos, entidades, aninhamento e tags auto-fechadas', () => {
    const r = parseXml('<?xml version="1.0"?><!-- c --><a x="1 &amp; 2"><b/><c y=\'z\'><d/></c></a>')
    const a = r.children[0]
    expect(a.attrs.x).toBe('1 & 2')
    expect(a.children.map((c) => c.tag)).toEqual(['b', 'c'])
    expect(a.children[1].children[0].tag).toBe('d')
  })

  it('efeitos e recorte sobrevivem ao .aseprite', async () => {
    let d = blankDoc(2, 1)
    d.layers.push({ ...d.layers[0], id: 2, name: 'b' })
    d.nextId = 3
    d.cels[celKey(1, 0)] = newCelImage(2, 1, new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 0, 0]))
    d.cels[celKey(2, 0)] = newCelImage(2, 1, new Uint8ClampedArray([0, 0, 255, 255, 0, 0, 255, 255]))
    d = updateLayer(d, 2, { clip: true, effects: [{ enabled: true, adjust: { kind: 'invert' } }] })
    const back = (await readAseprite((await writeAseprite(d)).buffer as ArrayBuffer)).doc
    expect(back.layers[1]).toMatchObject({ clip: true, effects: [{ enabled: true, adjust: { kind: 'invert' } }] })
    expect(Array.from(renderFrame(back, 0))).toEqual([255, 255, 0, 255, 0, 0, 0, 0])
  })
})

describe('atalhos configuráveis', () => {
  const ev = (key: string, m: Partial<Record<'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey', boolean>> = {}) =>
    ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...m })
  it('normaliza combinações', () => {
    expect(comboOf(ev('z', { ctrlKey: true }))).toBe('Ctrl+Z')
    expect(comboOf(ev('Z', { metaKey: true, shiftKey: true }))).toBe('Ctrl+Shift+Z')
    expect(comboOf(ev('N', { shiftKey: true }))).toBe('Shift+N')
    expect(comboOf(ev('+', { shiftKey: true }))).toBe('+') // símbolo com Shift: o Shift já está no caractere
    expect(comboOf(ev('F3'))).toBe('F3')
    expect(comboOf(ev('Shift', { shiftKey: true }))).toBeNull()
  })
  it('padrões sem conflito; troca e conflito detectados', () => {
    const d = defaults()
    expect(conflicts(d).size).toBe(0)
    expect(keymap(d).get('Shift+G')).toBe('tool:gradient')
    expect(keymap(d).get('Ctrl+Shift+Z')).toBe('redo')
    const b = { ...d, 'tool:pencil': ['P'], 'tool:eraser': ['E', 'P'] }
    expect(keymap(b).get('P')).toBe('tool:pencil')
    expect(keymap(b).get('B')).toBeUndefined()
    expect(conflicts(b).get('P')).toEqual(['tool:pencil', 'tool:eraser'])
  })
})

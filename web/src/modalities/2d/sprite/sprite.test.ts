import { describe, expect, it } from 'vitest'
import { readAseprite, writeAseprite } from './aseprite'
import {
  addLayer, addTag, blankDoc, celKey, displayOrder, duplicateLayer, getCel, insertFrame, isLinked, linkCels, mergeDown, moveFrame,
  moveLayer, playSequence, removeFrame, removeLayer, renderFrame, setParent, unlinkCel, updateLayer, writeCel, type SpriteDoc,
} from './doc'

const W = 4
const H = 3

function solid(r: number, g: number, b: number, a = 255): Uint8ClampedArray {
  const d = new Uint8ClampedArray(W * H * 4)
  for (let i = 0; i < d.length; i += 4) d.set([r, g, b, a], i)
  return d
}

function px(data: Uint8ClampedArray, x = 0, y = 0) {
  const i = (y * W + x) * 4
  return Array.from(data.slice(i, i + 4))
}

describe('documento', () => {
  it('compõe camadas na ordem, com opacidade, visibilidade e modos', () => {
    let d = blankDoc(W, H)
    d = writeCel(d, 1, 0, solid(255, 0, 0))
    const r = addLayer(d, 1)
    d = writeCel(r.doc, r.id, 0, solid(0, 0, 255))
    expect(px(renderFrame(d, 0))).toEqual([0, 0, 255, 255])
    d = updateLayer(d, r.id, { opacity: 128 })
    const mid = px(renderFrame(d, 0))
    expect(mid[0]).toBeGreaterThan(120)
    expect(mid[2]).toBeGreaterThan(120)
    d = updateLayer(d, r.id, { opacity: 255, visible: false })
    expect(px(renderFrame(d, 0))).toEqual([255, 0, 0, 255])
    d = updateLayer(d, r.id, { visible: true, blend: 'multiply' })
    expect(px(renderFrame(d, 0))).toEqual([0, 0, 0, 255])
  })

  it('grupos: visibilidade e opacidade valem para os filhos; ordem de exibição', () => {
    let d = blankDoc(W, H)
    const g = addLayer(d, 1, 'group')
    d = g.doc
    const c = addLayer(d, g.id)
    d = setParent(c.doc, c.id, g.id)
    d = writeCel(d, c.id, 0, solid(0, 255, 0))
    expect(displayOrder(d).map((l) => l.id)).toEqual([g.id, c.id, 1])
    expect(px(renderFrame(d, 0))).toEqual([0, 255, 0, 255])
    d = updateLayer(d, g.id, { visible: false })
    expect(px(renderFrame(d, 0))[3]).toBe(0)
    d = updateLayer(d, g.id, { visible: true, opacity: 0 })
    expect(px(renderFrame(d, 0))[3]).toBe(0)
    expect(removeLayer(d, g.id).layers.map((l) => l.id)).toEqual([1]) // grupo leva os filhos
  })

  it('quadros: inserir, duplicar ligado, mover, apagar mantém cels e tags no lugar', () => {
    let d = blankDoc(W, H)
    d = writeCel(d, 1, 0, solid(10, 0, 0))
    d = insertFrame(d, 0, 'duplicate')
    d = writeCel(d, 1, 1, solid(20, 0, 0))
    d = insertFrame(d, 1, 'linked')
    expect(d.frames).toHaveLength(3)
    expect(isLinked(d, 1, 2)).toBe(true)
    d = writeCel(d, 1, 2, solid(30, 0, 0)) // pintar num ligado altera os dois
    expect(px(getCel(d, 1, 1)!.data)[0]).toBe(30)
    d = unlinkCel(d, 1, 2)
    expect(isLinked(d, 1, 2)).toBe(false)
    d = addTag(d, 1, 2, 'walk')
    d = moveFrame(d, 0, 2)
    expect(px(getCel(d, 1, 2)!.data)[0]).toBe(10)
    d = removeFrame(d, 0)
    expect(d.frames).toHaveLength(2)
    expect(d.tags[0]).toMatchObject({ from: 0, to: 1 })
    d = linkCels(d, 1, [0, 1])
    expect(getCel(d, 1, 0)).toBe(getCel(d, 1, 1))
  })

  it('duplicar e mesclar camadas, mover na pilha', () => {
    let d = blankDoc(W, H)
    d = writeCel(d, 1, 0, solid(255, 0, 0))
    const dup = duplicateLayer(d, 1)
    d = dup.doc
    expect(getCel(d, dup.id, 0)!.data).not.toBe(getCel(d, 1, 0)!.data)
    d = writeCel(d, dup.id, 0, solid(0, 0, 255, 0))
    d = moveLayer(d, dup.id, -1)
    expect(d.layers[0].id).toBe(dup.id)
    d = moveLayer(d, dup.id, 1)
    const merged = mergeDown(d, dup.id)
    expect(merged.layers).toHaveLength(1)
    expect(px(getCel(merged, 1, 0)!.data)).toEqual([255, 0, 0, 255])
  })

  it('sequência de reprodução das tags', () => {
    let d = blankDoc(W, H, 4)
    d = addTag(d, 0, 3)
    expect(playSequence(d, d.tags[0])).toEqual([0, 1, 2, 3])
    expect(playSequence(d, { ...d.tags[0], direction: 'reverse' })).toEqual([3, 2, 1, 0])
    expect(playSequence(d, { ...d.tags[0], direction: 'pingpong' })).toEqual([0, 1, 2, 3, 2, 1])
  })
})

describe('.aseprite', () => {
  it('ida e volta preserva camadas, grupos, ligações, durações e tags', async () => {
    let d: SpriteDoc = blankDoc(W, H, 2)
    d.frames = [{ duration: 80 }, { duration: 120 }]
    const g = addLayer(d, 1, 'group', 'Corpo')
    d = g.doc
    const c = addLayer(d, g.id, 'image', 'Braço')
    d = setParent(c.doc, c.id, g.id)
    d = updateLayer(d, c.id, { opacity: 200, blend: 'screen' })
    d = updateLayer(d, g.id, { visible: false })
    const partial = new Uint8ClampedArray(W * H * 4)
    partial.set([9, 8, 7, 255], (1 * W + 2) * 4)
    d = writeCel(d, 1, 0, partial)
    d = linkCels(d, 1, [0, 1])
    d = writeCel(d, c.id, 1, solid(1, 2, 3))
    d = addTag(d, 0, 1, 'idle')
    d.tags[0] = { ...d.tags[0], direction: 'pingpong', repeat: 2, color: '#ff8800' }

    const bytes = await writeAseprite(d)
    const { doc: back, skipped } = await readAseprite(bytes.buffer as ArrayBuffer)
    expect(skipped).toEqual([])
    expect(back.frames.map((f) => f.duration)).toEqual([80, 120])
    expect(back.layers.map((l) => [l.name, l.kind])).toEqual([['Camada 1', 'image'], ['Corpo', 'group'], ['Braço', 'image']])
    const corpo = back.layers[1]
    const braco = back.layers[2]
    expect(braco.parent).toBe(corpo.id)
    expect(corpo.visible).toBe(false)
    expect([braco.opacity, braco.blend]).toEqual([200, 'screen'])
    const base = back.layers[0].id
    expect(back.cels[celKey(base, 0)]).toBe(back.cels[celKey(base, 1)]) // ligado
    expect(px(back.cels[celKey(base, 0)].data, 2, 1)).toEqual([9, 8, 7, 255]) // cel recortado volta na posição certa
    expect(back.cels[celKey(braco.id, 0)]).toBeUndefined()
    expect(back.tags[0]).toMatchObject({ name: 'idle', from: 0, to: 1, direction: 'pingpong', repeat: 2, color: '#ff8800' })
  })

  it('recusa arquivo que não é Aseprite', async () => {
    await expect(readAseprite(new Uint8Array(200).buffer)).rejects.toThrow()
  })
})

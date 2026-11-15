import { describe, expect, it } from 'vitest'
import { readAseprite, writeAseprite } from './aseprite'
import { applyAdjust } from './colorOps'
import { blankDoc, celKey, getCel, insertFrame, writeCel, type SpriteDoc } from './doc'
import { addTilemapLayer, applyPixels, convertToImage, convertToTilemap, fillTiles, getTilemap, pruneTileset, setTilemap } from './tilemap'

const W = 8
const H = 4
// imagem 8×4 com dois tiles 4×4 iguais (vermelho) — para deduplicar
const twoRed = () => {
  const d = new Uint8ClampedArray(W * H * 4)
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) d.set([255, 0, 0, 255], (y * W + x) * 4)
  return d
}
const px = (d: Uint8ClampedArray, x: number, y: number, w = W) => Array.from(d.slice((y * w + x) * 4, (y * w + x) * 4 + 4))

describe('tilemap', () => {
  it('converte imagem em tilemap reaproveitando tiles iguais e volta', () => {
    let d = writeCel(blankDoc(W, H), 1, 0, twoRed())
    d = convertToTilemap(d, 1, 4, 4)
    expect(d.layers[0].kind).toBe('tilemap')
    expect(d.tilesets[0].tiles).toHaveLength(2) // vazio + vermelho
    const tm = getCel(d, 1, 0)!.tilemap!
    expect(Array.from(tm.tiles)).toEqual([1, 1])
    expect(px(getCel(d, 1, 0)!.data, 6, 2)).toEqual([255, 0, 0, 255])
    const back = convertToImage(d, 1)
    expect(back.layers[0].kind).toBe('image')
    expect(getCel(back, 1, 0)!.tilemap).toBeUndefined()
  })

  it('pintar no modo manual muda o tile em todas as cópias; no auto cria tile novo', () => {
    let d = convertToTilemap(writeCel(blankDoc(W, H), 1, 0, twoRed()), 1, 4, 4)
    const edit = getCel(d, 1, 0)!.data.slice()
    edit.set([0, 0, 255, 255], 0) // pixel (0,0) do primeiro tile
    const manual = applyPixels(d, 1, 0, edit, 'manual')
    expect(px(getCel(manual, 1, 0)!.data, 4, 0)).toEqual([0, 0, 255, 255]) // a outra cópia mudou junto
    const auto = applyPixels(d, 1, 0, edit, 'auto')
    expect(px(getCel(auto, 1, 0)!.data, 4, 0)).toEqual([255, 0, 0, 255])
    expect(Array.from(getCel(auto, 1, 0)!.tilemap!.tiles)).toEqual([2, 1])
    expect(auto.tilesets[0].tiles).toHaveLength(3)
    d = setTilemap(auto, 1, 0, { ...getCel(auto, 1, 0)!.tilemap!, tiles: new Uint32Array([1, 1]) })
    expect(pruneTileset(d, d.tilesets[0].id).tilesets[0].tiles).toHaveLength(2)
  })

  it('camada nova, balde de tiles e cels ligados', () => {
    const r = addTilemapLayer(blankDoc(W, H), 1, 4, 4)
    let d = r.doc
    const layer = d.layers.find((l) => l.id === r.id)!
    const ts = d.tilesets[0]
    d = { ...d, tilesets: [{ ...ts, tiles: [...ts.tiles, new Uint8ClampedArray(64).fill(200)] }] }
    const tm = getTilemap(d, layer, 0)!
    d = setTilemap(d, r.id, 0, fillTiles(tm, 0, 0, 1))
    expect(Array.from(getCel(d, r.id, 0)!.tilemap!.tiles)).toEqual([1, 1])
    d = insertFrame(d, 0, 'duplicate')
    expect(getCel(d, r.id, 1)!.tilemap).not.toBe(getCel(d, r.id, 0)!.tilemap)
    expect(Array.from(getCel(d, r.id, 1)!.tilemap!.tiles)).toEqual([1, 1])
  })

  it('ajuste de cor numa camada de tilemap muda os tiles', () => {
    const d = convertToTilemap(writeCel(blankDoc(W, H), 1, 0, twoRed()), 1, 4, 4)
    const inv = applyAdjust(d, { kind: 'invert' }, 'cel', 1, 0, null)
    expect(px(inv.tilesets[0].tiles[1], 0, 0, 4)).toEqual([0, 255, 255, 255])
    expect(px(getCel(inv, 1, 0)!.data, 5, 1)).toEqual([0, 255, 255, 255])
  })
})

describe('.aseprite: tilemaps, slices e grade', () => {
  it('ida e volta preserva tilesets, tilemaps, slices com 9-slice/pivô/cor e a grade', async () => {
    let d: SpriteDoc = convertToTilemap(writeCel(blankDoc(W, H), 1, 0, twoRed()), 1, 4, 4)
    d.slices = [{ name: 'botao', color: '#ff8800', x: 1, y: 1, w: 6, h: 2, center: { x: 2, y: 0, w: 2, h: 2 }, pivot: { x: 3, y: 1 } }]
    d.grid = { x: 0, y: 0, w: 4, h: 4 }
    const { doc: back, skipped } = await readAseprite((await writeAseprite(d)).buffer as ArrayBuffer)
    expect(skipped).toEqual([])
    expect(back.layers[0]).toMatchObject({ kind: 'tilemap', tileset: d.tilesets[0].id })
    expect(back.tilesets[0]).toMatchObject({ tw: 4, th: 4 })
    expect(back.tilesets[0].tiles).toHaveLength(2)
    const cel = back.cels[celKey(back.layers[0].id, 0)]
    expect(Array.from(cel.tilemap!.tiles)).toEqual([1, 1])
    expect(px(cel.data, 7, 3)).toEqual([255, 0, 0, 255])
    expect(back.slices).toEqual(d.slices)
    expect(back.grid).toEqual(d.grid)
  })
})

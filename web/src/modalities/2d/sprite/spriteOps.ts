// Operações no sprite inteiro que usam as transformações de imagem (escalar, espelhar, girar 90°).
import { mapCels, type SpriteDoc } from './doc'
import { flip, resizeNearest, rotate90 } from './transform'

export function scaleSprite(doc: SpriteDoc, w: number, h: number): SpriteDoc {
  return mapCels(doc, w, h, (data) => resizeNearest({ w: doc.w, h: doc.h, data }, w, h).data)
}

export function flipSprite(doc: SpriteDoc, axis: 'h' | 'v'): SpriteDoc {
  return mapCels(doc, doc.w, doc.h, (data) => flip({ w: doc.w, h: doc.h, data }, axis).data)
}

export function rotateSprite(doc: SpriteDoc, dir: 1 | -1 | 2): SpriteDoc {
  if (dir === 2) return flipSprite(flipSprite(doc, 'h'), 'v')
  return mapCels(doc, doc.h, doc.w, (data) => rotate90({ w: doc.w, h: doc.h, data }, dir).data)
}

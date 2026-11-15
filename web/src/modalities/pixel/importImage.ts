// Importa uma imagem como ponto de partida: recorta a área opaca, amostra por vizinho mais próximo
// para caber no canvas (ancorado conforme a spec) e encaixa cada pixel na cor permitida mais próxima.
import type { PixelSpec } from '@/core/api'
import { emptyGrid, nearestColor, type Grid } from './render'

export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('não foi possível carregar a imagem'))
    img.src = url
  })
}

/** area: retângulo-alvo (topo-esquerda) dentro do canvas; padrão = canvas inteiro. */
export function imageToGrid(img: HTMLImageElement, w: number, h: number, palette: number[], anchor: PixelSpec['anchor'] = 'bottom', area?: [number, number, number, number]): Grid {
  const iw = img.naturalWidth, ih = img.naturalHeight
  const c = document.createElement('canvas')
  c.width = iw
  c.height = ih
  const ctx = c.getContext('2d', { willReadFrequently: true })!
  ctx.drawImage(img, 0, 0)
  const src = ctx.getImageData(0, 0, iw, ih).data
  // caixa da área opaca
  let x0 = iw, y0 = ih, x1 = -1, y1 = -1
  for (let y = 0; y < ih; y++)
    for (let x = 0; x < iw; x++)
      if (src[(y * iw + x) * 4 + 3] >= 128) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
  const g = emptyGrid(w, h)
  if (x1 < 0 || !palette.length) return g
  const bw = x1 - x0 + 1, bh = y1 - y0 + 1
  const [ax, ay, aw, ah] = area ?? [0, 0, w, h]
  const s = Math.min(1, aw / bw, ah / bh)
  const dw = Math.max(1, Math.min(aw, Math.round(bw * s))), dh = Math.max(1, Math.min(ah, Math.round(bh * s)))
  const px0 = ax + Math.floor((aw - dw) / 2)
  const py0 = ay + (anchor === 'top' ? 0 : anchor === 'center' ? Math.floor((ah - dh) / 2) : ah - dh)
  const cache = new Map<number, number>()
  for (let ty = 0; ty < dh; ty++)
    for (let tx = 0; tx < dw; tx++) {
      const sx = x0 + Math.min(bw - 1, Math.floor(((tx + 0.5) * bw) / dw))
      const sy = y0 + Math.min(bh - 1, Math.floor(((ty + 0.5) * bh) / dh))
      const i = (sy * iw + sx) * 4
      if (src[i + 3] < 128) continue
      const v = (src[i] << 16) | (src[i + 1] << 8) | src[i + 2]
      let snapped = cache.get(v)
      if (snapped === undefined) {
        snapped = nearestColor(v, palette)
        cache.set(v, snapped)
      }
      g.px[(py0 + ty) * w + px0 + tx] = snapped
    }
  return g
}

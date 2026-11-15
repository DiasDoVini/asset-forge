// Utilitários de cor e de buffers RGBA do tamanho do canvas (cels do documento). O desenho fica em paint.ts.

export type RGBA = [number, number, number, number]

export function hexToRgba(hex: string, alpha = 255): RGBA {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16) || 0, parseInt(h.slice(2, 4), 16) || 0, parseInt(h.slice(4, 6), 16) || 0, alpha]
}

export const rgbaToHex = ([r, g, b]: RGBA) => `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`

/** Desloca o conteúdo (o que sai do canvas é perdido, como mover o cel no Aseprite). */
export function shifted(src: Uint8ClampedArray, w: number, h: number, dx: number, dy: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(src.length)
  for (let y = 0; y < h; y++) {
    const sy = y - dy
    if (sy < 0 || sy >= h) continue
    for (let x = 0; x < w; x++) {
      const sx = x - dx
      if (sx < 0 || sx >= w) continue
      const s = (sy * w + sx) * 4
      const d = (y * w + x) * 4
      out[d] = src[s]
      out[d + 1] = src[s + 1]
      out[d + 2] = src[s + 2]
      out[d + 3] = src[s + 3]
    }
  }
  return out
}

/** Pinta `data` com uma cor sólida mantendo o alfa (onion skin colorido). */
export function tinted(data: Uint8ClampedArray, rgb: [number, number, number], alpha: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(data.length)
  for (let i = 0; i < data.length; i += 4) {
    if (!data[i + 3]) continue
    out[i] = (data[i] + rgb[0]) >> 1
    out[i + 1] = (data[i + 1] + rgb[1]) >> 1
    out[i + 2] = (data[i + 2] + rgb[2]) >> 1
    out[i + 3] = data[i + 3] * alpha
  }
  return out
}

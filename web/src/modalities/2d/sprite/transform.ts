// Transformações de imagem para pixel art: Scale2x (EPX), RotSprite (girar/escalar sem borrar), espelhar e girar 90°.
//
// RotSprite (Xenowhirl): amplia 8× com Scale2x três vezes — que suaviza as diagonais sem inventar cores — e amostra o
// resultado pelo vizinho mais próximo na posição girada/escalada. Só aparecem cores que já existiam na imagem.
import type { Img } from './selection'

const u32 = (d: Uint8ClampedArray) => new Uint32Array(d.buffer, d.byteOffset, d.byteLength >> 2)

/** Scale2x / EPX: dobra o tamanho, arredondando degraus diagonais. */
export function scale2x(src: Img): Img {
  const { w, h } = src
  const s = u32(src.data)
  const out = new Uint8ClampedArray(w * h * 16)
  const o = u32(out)
  const W2 = w * 2
  const at = (x: number, y: number) => s[Math.min(h - 1, Math.max(0, y)) * w + Math.min(w - 1, Math.max(0, x))]
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const P = s[y * w + x]
      const A = at(x, y - 1)
      const B = at(x + 1, y)
      const C = at(x - 1, y)
      const D = at(x, y + 1)
      let e0 = P, e1 = P, e2 = P, e3 = P
      if (C === A && C !== D && A !== B) e0 = A
      if (A === B && A !== C && B !== D) e1 = B
      if (D === C && D !== B && C !== A) e2 = C
      if (B === D && B !== A && D !== C) e3 = D
      const i = y * 2 * W2 + x * 2
      o[i] = e0
      o[i + 1] = e1
      o[i + W2] = e2
      o[i + W2 + 1] = e3
    }
  return { w: w * 2, h: h * 2, data: out }
}

export type Algo = 'rotsprite' | 'nearest'

export type TransformParams = { sx: number; sy: number; angle: number; algo: Algo } // angle em graus, horário

/** Fator de ampliação prévia do RotSprite conforme o tamanho (evita estourar a memória em imagens grandes). */
function upFactor(img: Img): number {
  const n = img.w * img.h
  return n <= 512 * 512 ? 8 : n <= 1024 * 1024 ? 4 : n <= 2048 * 2048 ? 2 : 1
}

/**
 * Imagem transformada e o deslocamento do seu canto em relação ao centro da original (para manter o centro no lugar).
 * Espelhar = escala negativa.
 */
export function transformImage(src: Img, p: TransformParams): Img & { dx: number; dy: number } {
  const rad = (p.angle * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  const tw = src.w * Math.abs(p.sx)
  const th = src.h * Math.abs(p.sy)
  // caixa da imagem girada
  const bw = Math.max(1, Math.round(Math.abs(tw * cos) + Math.abs(th * sin)))
  const bh = Math.max(1, Math.round(Math.abs(tw * sin) + Math.abs(th * cos)))
  const plain = p.angle % 360 === 0
  let up: Img = src
  let k = 1
  if (p.algo === 'rotsprite' && !plain) {
    k = upFactor(src)
    for (let f = k; f > 1; f /= 2) up = scale2x(up)
  }
  const s = u32(up.data)
  const out = new Uint8ClampedArray(bw * bh * 4)
  const o = u32(out)
  const cxT = bw / 2
  const cyT = bh / 2
  for (let y = 0; y < bh; y++)
    for (let x = 0; x < bw; x++) {
      // centro do pixel de destino → coordenadas da imagem original (gira de volta e desfaz a escala)
      const dx = x + 0.5 - cxT
      const dy = y + 0.5 - cyT
      const rx = dx * cos + dy * sin
      const ry = -dx * sin + dy * cos
      const ux = rx / p.sx + src.w / 2
      const uy = ry / p.sy + src.h / 2
      if (ux < 0 || uy < 0 || ux >= src.w || uy >= src.h) continue
      const sxp = Math.min(up.w - 1, Math.floor(ux * k))
      const syp = Math.min(up.h - 1, Math.floor(uy * k))
      o[y * bw + x] = s[syp * up.w + sxp]
    }
  return { w: bw, h: bh, data: out, dx: -bw / 2, dy: -bh / 2 }
}

export function flip(src: Img, axis: 'h' | 'v'): Img {
  const s = u32(src.data)
  const out = new Uint8ClampedArray(src.data.length)
  const o = u32(out)
  for (let y = 0; y < src.h; y++)
    for (let x = 0; x < src.w; x++) {
      const tx = axis === 'h' ? src.w - 1 - x : x
      const ty = axis === 'v' ? src.h - 1 - y : y
      o[ty * src.w + tx] = s[y * src.w + x]
    }
  return { w: src.w, h: src.h, data: out }
}

/** Gira 90° (dir 1 = horário, -1 = anti-horário). */
export function rotate90(src: Img, dir: 1 | -1): Img {
  const s = u32(src.data)
  const out = new Uint8ClampedArray(src.data.length)
  const o = u32(out)
  const W = src.h
  for (let y = 0; y < src.h; y++)
    for (let x = 0; x < src.w; x++) {
      const tx = dir === 1 ? src.h - 1 - y : y
      const ty = dir === 1 ? x : src.w - 1 - x
      o[ty * W + tx] = s[y * src.w + x]
    }
  return { w: src.h, h: src.w, data: out }
}

/** Escala inteira ou fracionária pelo vizinho mais próximo (redimensionar sprite). */
export function resizeNearest(src: Img, w: number, h: number): Img {
  const s = u32(src.data)
  const out = new Uint8ClampedArray(w * h * 4)
  const o = u32(out)
  for (let y = 0; y < h; y++) {
    const sy = Math.min(src.h - 1, Math.floor(((y + 0.5) * src.h) / h))
    for (let x = 0; x < w; x++) o[y * w + x] = s[sy * src.w + Math.min(src.w - 1, Math.floor(((x + 0.5) * src.w) / w))]
  }
  return { w, h, data: out }
}

/** Cola `img` em (x, y) sobre `dst` (W×H) com composição alfa normal. */
export function stampOver(dst: Uint8ClampedArray, W: number, H: number, img: Img, x: number, y: number) {
  for (let yy = 0; yy < img.h; yy++) {
    const ty = y + yy
    if (ty < 0 || ty >= H) continue
    for (let xx = 0; xx < img.w; xx++) {
      const tx = x + xx
      if (tx < 0 || tx >= W) continue
      const s = (yy * img.w + xx) * 4
      const sa = img.data[s + 3] / 255
      if (!sa) continue
      const d = (ty * W + tx) * 4
      const da = dst[d + 3] / 255
      const ra = sa + da * (1 - sa)
      for (let c = 0; c < 3; c++) dst[d + c] = Math.round((img.data[s + c] * sa + dst[d + c] * da * (1 - sa)) / ra)
      dst[d + 3] = Math.round(ra * 255)
    }
  }
}

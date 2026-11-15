// Lógica pura do editor de recorte: grade, máscara de conteúdo, detecção de grade e de ilhas.
// Espelha forge_2d/spritesheet.py (o servidor recebe sempre os retângulos finais, então o resultado é o que se vê).

export type Rect = [number, number, number, number] // x, y, w, h

export type GridParams = {
  columns?: number
  rows?: number
  cellW?: number
  cellH?: number
  marginX: number
  marginY: number
  spacingX: number
  spacingY: number
}

export const defaultGrid: GridParams = { columns: 4, rows: 1, marginX: 0, marginY: 0, spacingX: 0, spacingY: 0 }

function axis(total: number, count: number | undefined, cell: number | undefined, margin: number, spacing: number) {
  const avail = total - margin
  if (cell && !count) count = Math.max(1, Math.floor((avail + spacing) / (cell + spacing)))
  else if (count && !cell) cell = Math.max(1, Math.floor((avail - (count - 1) * spacing) / count))
  if (!count || !cell) return null
  return { count, cell }
}

export function resolveGrid(W: number, H: number, g: GridParams) {
  const x = axis(W, g.columns, g.cellW, g.marginX, g.spacingX)
  const y = axis(H, g.rows, g.cellH, g.marginY, g.spacingY)
  return x && y ? { cols: x.count, rows: y.count, cw: x.cell, ch: y.cell } : null
}

/** Células da grade, linha a linha; as que passam da borda da imagem são descartadas. */
export function gridCells(W: number, H: number, g: GridParams): Rect[] {
  const r = resolveGrid(W, H, g)
  if (!r) return []
  const out: Rect[] = []
  for (let row = 0; row < r.rows; row++)
    for (let col = 0; col < r.cols; col++) {
      const x = g.marginX + col * (r.cw + g.spacingX)
      const y = g.marginY + row * (r.ch + g.spacingY)
      if (x + r.cw <= W && y + r.ch <= H) out.push([x, y, r.cw, r.ch])
    }
  return out
}

/** 1 onde há conteúdo: alfa > 0 ou, em imagens opacas, cor diferente do fundo (a mais comum nos cantos). */
export function contentMask(rgba: Uint8ClampedArray, W: number, H: number, tolerance = 8): Uint8Array {
  const n = W * H
  const mask = new Uint8Array(n)
  let translucent = false
  for (let i = 0; i < n; i++)
    if (rgba[i * 4 + 3] < 255) {
      translucent = true
      break
    }
  if (translucent) {
    for (let i = 0; i < n; i++) mask[i] = rgba[i * 4 + 3] > 0 ? 1 : 0
    return mask
  }
  const at = (x: number, y: number) => (y * W + x) * 4
  const corners = [at(0, 0), at(W - 1, 0), at(0, H - 1), at(W - 1, H - 1)].map((o) => [rgba[o], rgba[o + 1], rgba[o + 2]])
  const votes = new Map<string, { c: number[]; n: number }>()
  for (const c of corners) {
    const k = c.join(',')
    votes.set(k, { c, n: (votes.get(k)?.n ?? 0) + 1 })
  }
  const bg = [...votes.values()].sort((a, b) => b.n - a.n)[0].c
  for (let i = 0; i < n; i++) {
    const o = i * 4
    mask[i] = Math.max(Math.abs(rgba[o] - bg[0]), Math.abs(rgba[o + 1] - bg[1]), Math.abs(rgba[o + 2] - bg[2])) > tolerance ? 1 : 0
  }
  return mask
}

/** Imagem integral da máscara: consulta de área em O(1). */
export function integral(mask: Uint8Array, W: number, H: number): Int32Array {
  const w1 = W + 1
  const ii = new Int32Array(w1 * (H + 1))
  for (let y = 0; y < H; y++) {
    let run = 0
    for (let x = 0; x < W; x++) {
      run += mask[y * W + x]
      ii[(y + 1) * w1 + x + 1] = ii[y * w1 + x + 1] + run
    }
  }
  return ii
}

export function countIn(ii: Int32Array, W: number, [x, y, w, h]: Rect): number {
  const w1 = W + 1
  return ii[(y + h) * w1 + x + w] - ii[y * w1 + x + w] - ii[(y + h) * w1 + x] + ii[y * w1 + x]
}

function runs(flags: ArrayLike<number>): [number, number][] {
  const out: [number, number][] = []
  let start = -1
  for (let i = 0; i < flags.length; i++) {
    if (flags[i] && start < 0) start = i
    else if (!flags[i] && start >= 0) {
      out.push([start, i])
      start = -1
    }
  }
  if (start >= 0) out.push([start, flags.length])
  return out
}

/** Ordem de leitura: retângulos que se sobrepõem na vertical formam uma linha. */
export function readingOrder(rects: Rect[]): Rect[] {
  const sorted = [...rects].sort((a, b) => a[1] - b[1] || a[0] - b[0])
  const rows: Rect[][] = []
  for (const r of sorted) {
    const row = rows.find((row) => {
      const top = Math.min(...row.map((q) => q[1]))
      const bot = Math.max(...row.map((q) => q[1] + q[3]))
      return r[1] < bot && r[1] + r[3] > top && r[1] + r[3] / 2 < bot + (bot - top) * 0.25
    })
    if (row) row.push(r)
    else rows.push([r])
  }
  return rows
    .sort((a, b) => Math.min(...a.map((q) => q[1])) - Math.min(...b.map((q) => q[1])))
    .flatMap((row) => row.sort((a, b) => a[0] - b[0]))
}

function dilate(mask: Uint8Array, W: number, H: number, g: number): Uint8Array {
  if (g <= 0) return mask
  const tmp = new Uint8Array(W * H)
  const out = new Uint8Array(W * H)
  const pre = new Int32Array(Math.max(W, H) + 1)
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) pre[x + 1] = pre[x] + mask[y * W + x]
    for (let x = 0; x < W; x++) tmp[y * W + x] = pre[Math.min(W, x + g + 1)] - pre[Math.max(0, x - g)] > 0 ? 1 : 0
  }
  for (let x = 0; x < W; x++) {
    for (let y = 0; y < H; y++) pre[y + 1] = pre[y] + tmp[y * W + x]
    for (let y = 0; y < H; y++) out[y * W + x] = pre[Math.min(H, y + g + 1)] - pre[Math.max(0, y - g)] > 0 ? 1 : 0
  }
  return out
}

/** Retângulos das ilhas de conteúdo (8 vizinhos); `mergeGap` junta ilhas separadas por até N px. */
export function detectSprites(mask: Uint8Array, W: number, H: number, minArea = 4, mergeGap = 0): Rect[] {
  const work = dilate(mask, W, H, mergeGap)
  const seen = new Uint8Array(W * H)
  const stack: number[] = []
  const rects: Rect[] = []
  for (let start = 0; start < W * H; start++) {
    if (!work[start] || seen[start]) continue
    let x0 = W, y0 = H, x1 = -1, y1 = -1, area = 0
    stack.push(start)
    seen[start] = 1
    while (stack.length) {
      const p = stack.pop()!
      const px = p % W
      const py = (p / W) | 0
      if (mask[p]) {
        area++
        if (px < x0) x0 = px
        if (px > x1) x1 = px
        if (py < y0) y0 = py
        if (py > y1) y1 = py
      }
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          const nx = px + dx
          const ny = py + dy
          if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue
          const q = ny * W + nx
          if (work[q] && !seen[q]) {
            seen[q] = 1
            stack.push(q)
          }
        }
    }
    if (area >= minArea && x1 >= 0) rects.push([x0, y0, x1 - x0 + 1, y1 - y0 + 1])
  }
  return readingOrder(rects)
}

export type GridGuess = { grid: GridParams; method: 'gaps' | 'contrast' | 'none'; confidence: number }

/** Sugere a grade: pelos vãos vazios entre sprites; senão, pelo contraste nas emendas (tilesets contínuos). */
export function detectGrid(rgba: Uint8ClampedArray, mask: Uint8Array, W: number, H: number): GridGuess {
  const colAny = new Uint8Array(W)
  const rowAny = new Uint8Array(H)
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++)
      if (mask[y * W + x]) {
        colAny[x] = 1
        rowAny[y] = 1
      }
  const colRuns = runs(colAny)
  const rowRuns = runs(rowAny)
  if (!colRuns.length) return { grid: { ...defaultGrid, columns: 1, rows: 1 }, method: 'none', confidence: 0 }

  const median = (v: number[]) => [...v].sort((a, b) => a - b)[Math.floor(v.length / 2)]
  const period = (rs: [number, number][]) => {
    if (rs.length < 2) return null
    const starts = rs.map((r) => r[0])
    const steps = starts.slice(1).map((s, i) => s - starts[i])
    const step = median(steps)
    if (step <= 0 || Math.max(...steps.map((s) => Math.abs(s - step))) > Math.max(2, step >> 3)) return null
    const cell = Math.max(...rs.map((r) => r[1] - r[0]))
    return { step, margin: starts[0], gap: Math.max(0, step - cell) }
  }
  const px = period(colRuns)
  const py = period(rowRuns)
  if (px || py) {
    const g: GridParams = {
      columns: px ? colRuns.length : 1,
      rows: py ? rowRuns.length : 1,
      cellW: px ? px.step : W,
      cellH: py ? py.step : H,
      marginX: 0,
      marginY: 0,
      spacingX: 0,
      spacingY: 0,
    }
    return { grid: g, method: 'gaps', confidence: 0.8 }
  }

  // contraste entre colunas/linhas vizinhas
  const dX = new Float64Array(Math.max(0, W - 1))
  const dY = new Float64Array(Math.max(0, H - 1))
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const o = (y * W + x) * 4
      if (x + 1 < W) dX[x] += Math.abs(rgba[o] - rgba[o + 4]) + Math.abs(rgba[o + 1] - rgba[o + 5]) + Math.abs(rgba[o + 2] - rgba[o + 6]) + Math.abs(rgba[o + 3] - rgba[o + 7])
      if (y + 1 < H) {
        const o2 = o + W * 4
        dY[y] += Math.abs(rgba[o] - rgba[o2]) + Math.abs(rgba[o + 1] - rgba[o2 + 1]) + Math.abs(rgba[o + 2] - rgba[o2 + 2]) + Math.abs(rgba[o + 3] - rgba[o2 + 3])
      }
    }
  const ratioOf = (d: Float64Array, period: number) => {
    let cutSum = 0, cutN = 0, otherSum = 0, otherN = 0
    for (let i = 0; i < d.length; i++) {
      if ((i + 1) % period === 0) {
        cutSum += d[i]
        cutN++
      } else {
        otherSum += d[i]
        otherN++
      }
    }
    if (!cutN || !otherN) return 0
    return cutSum / cutN / (otherSum / otherN + 1e-3)
  }
  const best = (d: Float64Array, total: number) => {
    const scores: [number, number][] = []
    for (let p = 4; p < total; p++) if (total % p === 0) scores.push([p, ratioOf(d, p)])
    const good = scores.filter(([, r]) => r > 1.5)
    if (!good.length) return { cell: total, ratio: 0 }
    const top = Math.max(...good.map(([, r]) => r))
    const close = good.filter(([, r]) => r >= 0.85 * top).sort((a, b) => a[0] - b[0])
    return { cell: close[0][0], ratio: close[0][1] }
  }
  const bx = best(dX, W)
  const by = best(dY, H)
  const found = bx.cell !== W || by.cell !== H
  return {
    grid: { columns: W / bx.cell, rows: H / by.cell, cellW: bx.cell, cellH: by.cell, marginX: 0, marginY: 0, spacingX: 0, spacingY: 0 },
    method: found ? 'contrast' : 'none',
    confidence: found ? Math.min(1, (bx.ratio + by.ratio) / 6) : 0.1,
  }
}

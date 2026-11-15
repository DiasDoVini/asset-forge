// Simulação/render de partículas no navegador — mesma semântica de forge_vfx/particles.py
// (px, origem no centro, y para cima, graus anti-horário; 90 = para cima).

export type Burst = { time: number; count: number }
export type Emitter = {
  name: string
  texture: string
  blend: 'add' | 'alpha'
  rate: number
  bursts: Burst[]
  max_particles: number
  start_delay: number
  emit_duration: number | null
  shape: 'point' | 'circle' | 'ring' | 'box' | 'line'
  radius: number
  width: number
  height: number
  position: [number, number]
  direction: number
  spread: number
  speed: [number, number]
  lifetime: [number, number]
  gravity: [number, number]
  drag: number
  radial_accel: number
  tangential_accel: number
  turbulence: number
  size: [number, number]
  size_curve: [number, number][]
  rotation: [number, number]
  spin: [number, number]
  align_to_velocity: boolean
  stretch: number
  color: [number, string][]
  brightness_variation: number
  random_color?: boolean
}
export type ParticleSystem = { name: string; description?: string; duration: number; loop: boolean; canvas: [number, number]; emitters: Emitter[]; preset?: string | null }

const DEG = Math.PI / 180

function rng(seed: number) {
  let s = seed >>> 0 || 1
  return () => {
    s ^= s << 13
    s ^= s >>> 17
    s ^= s << 5
    return ((s >>> 0) % 1_000_000) / 1_000_000
  }
}

export function parseColor(c: string): [number, number, number, number] {
  let s = c.replace('#', '')
  if (s.length <= 4) s = [...s].map((x) => x + x).join('')
  if (s.length === 6) s += 'ff'
  return [0, 2, 4, 6].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as [number, number, number, number]
}

function interp(xs: number[], ys: number[], t: number) {
  if (t <= xs[0]) return ys[0]
  for (let i = 1; i < xs.length; i++) if (t <= xs[i]) return ys[i - 1] + ((ys[i] - ys[i - 1]) * (t - xs[i - 1])) / Math.max(1e-6, xs[i] - xs[i - 1])
  return ys[ys.length - 1]
}

export function gradientAt(grad: [number, string][], t: number): [number, number, number, number] {
  const ts = grad.map((g) => g[0])
  const cs = grad.map((g) => parseColor(g[1]))
  return [0, 1, 2, 3].map((k) => interp(ts, cs.map((c) => c[k]), t)) as [number, number, number, number]
}

type P = { x: number; y: number; vx: number; vy: number; age: number; life: number; size: number; rot: number; spin: number; bright: number; hue: number }

class EmitterState {
  ps: P[] = []
  acc = 0
  rand: () => number
  em: Emitter
  constructor(em: Emitter, seed: number) {
    this.em = em
    this.rand = rng(seed)
  }
  r(lo: number, hi: number) {
    return lo + (hi - lo) * this.rand()
  }
  spawn(n: number) {
    const em = this.em
    n = Math.min(n, em.max_particles - this.ps.length)
    for (let i = 0; i < n; i++) {
      let ox = 0
      let oy = 0
      if (em.shape === 'circle') {
        const a = this.r(0, Math.PI * 2)
        const rr = em.radius * Math.sqrt(this.rand())
        ox = Math.cos(a) * rr
        oy = Math.sin(a) * rr
      } else if (em.shape === 'ring') {
        const a = this.r(0, Math.PI * 2)
        ox = Math.cos(a) * em.radius
        oy = Math.sin(a) * em.radius
      } else if (em.shape === 'box') {
        ox = this.r(-em.width / 2, em.width / 2)
        oy = this.r(-em.height / 2, em.height / 2)
      } else if (em.shape === 'line') ox = this.r(-em.width / 2, em.width / 2)
      const d = (em.direction + this.r(-em.spread, em.spread)) * DEG
      const sp = this.r(em.speed[0], em.speed[1])
      this.ps.push({
        x: em.position[0] + ox, y: em.position[1] + oy, vx: Math.cos(d) * sp, vy: Math.sin(d) * sp, age: 0,
        life: Math.max(0.01, this.r(em.lifetime[0], em.lifetime[1])), size: this.r(em.size[0], em.size[1]),
        rot: this.r(em.rotation[0], em.rotation[1]), spin: this.r(em.spin[0], em.spin[1]),
        bright: 1 - this.r(0, em.brightness_variation), hue: this.rand(),
      })
    }
  }
  step(t: number, dt: number, dur: number, loop: boolean) {
    const em = this.em
    const local = t - em.start_delay
    const span = em.emit_duration ?? dur
    if (local >= 0 && (loop || local < span) && em.rate > 0 && (!loop || local % dur < span)) {
      this.acc += em.rate * dt
      const k = Math.floor(this.acc)
      this.acc -= k
      this.spawn(k)
    }
    for (const b of em.bursts) {
      const bt = em.start_delay + b.time
      if (loop) {
        const prev = (((t - dt - bt) % dur) + dur) % dur
        const cur = (((t - bt) % dur) + dur) % dur
        if (t - bt >= 0 && cur < prev) this.spawn(b.count)
      } else if (t - dt < bt && bt <= t) this.spawn(b.count)
    }
    const drag = em.drag ? Math.max(0, 1 - em.drag * dt) : 1
    for (const p of this.ps) {
      let ax = em.gravity[0]
      let ay = em.gravity[1]
      if (em.radial_accel || em.tangential_accel) {
        const rx = p.x - em.position[0]
        const ry = p.y - em.position[1]
        const dist = Math.hypot(rx, ry) + 1e-4
        const nx = rx / dist
        const ny = ry / dist
        ax += nx * em.radial_accel - ny * em.tangential_accel
        ay += ny * em.radial_accel + nx * em.tangential_accel
      }
      if (em.turbulence) {
        const px = p.x * 0.02
        const py = p.y * 0.02
        ax += (Math.sin(py * 1.7 + t * 1.3) + Math.sin(px * 0.9 - t)) * em.turbulence
        ay += (Math.cos(px * 1.3 - t * 1.1) + Math.sin(py * 0.7 + t)) * em.turbulence
      }
      p.vx = (p.vx + ax * dt) * drag
      p.vy = (p.vy + ay * dt) * drag
      p.x += p.vx * dt
      p.y += p.vy * dt
      p.rot += p.spin * dt
      p.age += dt
    }
    this.ps = this.ps.filter((p) => p.age < p.life)
  }
}

// ---------------------------------------------------------------- texturas procedurais

const texCache = new Map<string, HTMLCanvasElement>()

export function builtinTexture(name: string, size = 64): HTMLCanvasElement {
  const key = `${name}:${size}`
  const hit = texCache.get(key)
  if (hit) return hit
  const cv = document.createElement('canvas')
  cv.width = cv.height = size
  const g = cv.getContext('2d')!
  const img = g.createImageData(size, size)
  const c = (size - 1) / 2
  // ruído de valor simples para "smoke"
  const grid = Array.from({ length: 9 * 9 }, (_, i) => ((Math.sin(i * 12.9898) * 43758.5453) % 1 + 1) % 1)
  const noise = (x: number, y: number) => {
    const gx = x * 8
    const gy = y * 8
    const ix = Math.floor(gx)
    const iy = Math.floor(gy)
    const fx = gx - ix
    const fy = gy - iy
    const v = (a: number, b: number) => grid[Math.min(8, a) * 9 + Math.min(8, b)]
    const top = v(ix, iy) * (1 - fx) + v(ix + 1, iy) * fx
    const bot = v(ix, iy + 1) * (1 - fx) + v(ix + 1, iy + 1) * fx
    return top * (1 - fy) + bot * fy
  }
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const dx = (x - c) / c
      const dy = (y - c) / c
      const r = Math.hypot(dx, dy)
      let a = 0
      if (name === 'circle') a = Math.min(1, Math.max(0, ((1 - r) * c) / 1.5))
      else if (name === 'spark') a = Math.exp(-dx * dx * 2.5 - dy * dy * 60)
      else if (name === 'star') a = Math.min(1, Math.exp(-dx * dx * 40) * Math.exp(-dy * dy * 2) + Math.exp(-dy * dy * 40) * Math.exp(-dx * dx * 2) + Math.exp(-r * r * 30))
      else if (name === 'smoke') a = r < 1 ? Math.max(0, Math.min(1, Math.exp(-r * r * 3) * (0.55 + noise(x / size, y / size) * 0.9) - 0.15)) : 0
      else if (name === 'ring') a = Math.exp(-((r - 0.75) ** 2) * 120)
      else if (name === 'square') a = 1
      else if (name === 'flare') a = r < 1 ? Math.min(1, Math.exp(-r * r * 2.5) * 0.5 + Math.exp(-r * r * 25)) : 0
      else a = r < 1 ? Math.exp(-r * r * 4.5) : 0 // soft
      const i = (y * size + x) * 4
      img.data[i] = img.data[i + 1] = img.data[i + 2] = 255
      img.data[i + 3] = Math.round(a * 255)
    }
  g.putImageData(img, 0, 0)
  texCache.set(key, cv)
  return cv
}

// ---------------------------------------------------------------- player

export class ParticlePlayer {
  states: EmitterState[] = []
  t = 0
  sys: ParticleSystem
  textures: Record<string, CanvasImageSource>
  constructor(sys: ParticleSystem, textures: Record<string, CanvasImageSource> = {}, seed = 1) {
    this.sys = sys
    this.textures = textures
    this.reset(seed)
  }
  reset(seed = 1) {
    this.states = this.sys.emitters.map((e, i) => new EmitterState(e, seed * 1000 + i + 1))
    this.t = 0
    if (this.sys.loop) {
      const warm = Math.max(this.sys.duration, ...this.sys.emitters.map((e) => e.lifetime[1] + e.start_delay))
      for (let k = 0; k < warm * 60; k++) this.advance(1 / 60)
    }
  }
  advance(dt: number) {
    for (const s of this.states) s.step(this.t, dt, this.sys.duration, this.sys.loop)
    this.t += dt
  }
  get count() {
    return this.states.reduce((n, s) => n + s.ps.length, 0)
  }
  draw(g: CanvasRenderingContext2D, w: number, h: number) {
    const sx = w / this.sys.canvas[0]
    const sy = h / this.sys.canvas[1]
    const k = (sx + sy) / 2
    g.clearRect(0, 0, w, h)
    for (const st of this.states) {
      const em = st.em
      const tex = (em.texture.startsWith('asset:') ? this.textures[em.texture] : null) ?? builtinTexture(em.texture)
      g.globalCompositeOperation = em.blend === 'add' ? 'lighter' : 'source-over'
      const cts = em.size_curve.map((c) => c[0])
      const cvs = em.size_curve.map((c) => c[1])
      for (const p of st.ps) {
        const tl = Math.min(1, p.age / p.life)
        const col = gradientAt(em.color, tl)
        if (em.random_color) {
          const rc = gradientAt(em.color, p.hue * 0.95)
          col[0] = rc[0]; col[1] = rc[1]; col[2] = rc[2]
        }
        const d = p.size * interp(cts, cvs, tl) * k
        if (d < 0.5 || col[3] <= 0.003) continue
        let wd = d
        let ang = -p.rot * DEG
        if (em.align_to_velocity || em.stretch) {
          ang = -Math.atan2(p.vy, p.vx)
          if (em.stretch) wd = d * (1 + (em.stretch * Math.hypot(p.vx, p.vy)) / 100)
        }
        const b = p.bright
        g.save()
        g.translate(w / 2 + p.x * sx, h / 2 - p.y * sy)
        g.rotate(ang)
        g.globalAlpha = col[3]
        // tinge: desenha a textura e multiplica pela cor
        g.drawImage(tintTexture(tex, col[0] * b, col[1] * b, col[2] * b), -wd / 2, -d / 2, wd, d)
        g.restore()
      }
    }
    g.globalCompositeOperation = 'source-over'
    g.globalAlpha = 1
  }
}

const tintCache = new Map<string, HTMLCanvasElement>()
const texIds = new WeakMap<object, number>()
let texSeq = 0

function tintTexture(tex: CanvasImageSource, r: number, g: number, b: number): HTMLCanvasElement {
  const q = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 31)
  if (!texIds.has(tex as object)) texIds.set(tex as object, ++texSeq)
  const key = `${texIds.get(tex as object)}:${q(r)}:${q(g)}:${q(b)}`
  const hit = tintCache.get(key)
  if (hit) return hit
  const src = tex as HTMLCanvasElement
  const cv = document.createElement('canvas')
  cv.width = src.width || 64
  cv.height = src.height || 64
  const c = cv.getContext('2d')!
  c.drawImage(src, 0, 0, cv.width, cv.height)
  c.globalCompositeOperation = 'multiply'
  c.fillStyle = `rgb(${(q(r) / 31) * 255},${(q(g) / 31) * 255},${(q(b) / 31) * 255})`
  c.fillRect(0, 0, cv.width, cv.height)
  c.globalCompositeOperation = 'destination-in'
  c.drawImage(src, 0, 0, cv.width, cv.height)
  if (tintCache.size > 4000) tintCache.clear()
  tintCache.set(key, cv)
  return cv
}

/** Mesmas transformações de vfx.particles_edit (escala/velocidade/quantidade/cores). */
export function transformSystem(sys: ParticleSystem, scale: number, speed: number, amount: number, color?: [number, number, number] | null): ParticleSystem {
  return {
    ...sys,
    emitters: sys.emitters.map((e) => ({
      ...e,
      size: [e.size[0] * scale, e.size[1] * scale],
      speed: [e.speed[0] * scale * speed, e.speed[1] * scale * speed],
      position: [e.position[0] * scale, e.position[1] * scale],
      gravity: [e.gravity[0] * scale, e.gravity[1] * scale],
      radius: e.radius * scale,
      width: e.width * scale,
      height: e.height * scale,
      rate: e.rate * amount,
      bursts: e.bursts.map((b) => ({ ...b, count: Math.max(1, Math.round(b.count * amount)) })),
      color: color && e.blend === 'add' ? tintGradient(e.color, color) : e.color,
    })),
  }
}

function tintGradient(grad: [number, string][], rgb: [number, number, number]): [number, string][] {
  return grad.map(([t, c]) => {
    const [r, g, b, a] = parseColor(c)
    const lum = Math.max(r, g, b)
    if (lum - Math.min(r, g, b) < 0.15 && lum > 0.85) return [t, c]
    const hx = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, '0')
    return [t, `#${hx(rgb[0] * lum)}${hx(rgb[1] * lum)}${hx(rgb[2] * lum)}${hx(a)}`]
  })
}

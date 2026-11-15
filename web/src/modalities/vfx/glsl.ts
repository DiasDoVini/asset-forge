// Runtime WebGL2 dos shaders do Asset Forge. Monta o mesmo GLSL ES 3.00 que o servidor (forge_vfx.shaders.glsl_es),
// com o prelude compartilhado direto do pacote Python.
import prelude from '../../../../packages/forge_vfx/src/forge_vfx/glsl/prelude.glsl?raw'

export type Uniform = { name: string; type: 'float' | 'color' | 'vec2' | 'bool'; default: number[]; min?: number | null; max?: number | null; label?: string }
export type ShaderDef = {
  format?: string
  name: string
  description?: string
  blend: 'alpha' | 'add'
  mode: 'sprite' | 'overlay' | 'fullscreen'
  uses_texture: boolean
  one_shot: boolean
  duration: number
  uniforms: Uniform[]
  code: string
  template?: string | null
}

const SIZE = { float: 1, bool: 1, vec2: 2, color: 4 } as const
const GLTYPE = { float: 'float', bool: 'float', vec2: 'vec2', color: 'vec4' } as const

export function uniformValues(u: Uniform): number[] {
  const d = [...u.default]
  if (u.type === 'color' && d.length === 3) d.push(1)
  while (d.length < SIZE[u.type]) d.push(1)
  return d.slice(0, SIZE[u.type])
}

export function fragmentSource(sd: Pick<ShaderDef, 'uniforms' | 'code'>): { src: string; offset: number } {
  const us = sd.uniforms.map((u) => `uniform ${GLTYPE[u.type]} ${u.name};`).join('\n')
  const head = `#version 300 es
precision highp float;
uniform float uTime;
uniform float uProgress;
uniform vec2 uResolution;
uniform sampler2D uTexture;
uniform float uHasTexture;
${us}
in vec2 vUv;
out vec4 fragColor;
vec4 sampleTex(vec2 uv) {
    if (uHasTexture < 0.5 || uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return vec4(0.0);
    return texture(uTexture, uv);
}
${prelude}
`
  return { src: `${head}${sd.code}\nvoid main() {\n    fragColor = effect(vUv);\n}\n`, offset: head.split('\n').length - 1 }
}

const VERT = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() {
    vUv = aPos * 0.5 + 0.5;
    gl_Position = vec4(aPos, 0.0, 1.0);
}
`

export type CompileResult = { ok: true } | { ok: false; error: string }

/** Renderizador de um shader num canvas. Recompila quando o código muda; uniforms são trocados a cada quadro. */
export class ShaderRunner {
  gl: WebGL2RenderingContext
  prog: WebGLProgram | null = null
  tex: WebGLTexture | null = null
  hasTex = false
  private locs = new Map<string, WebGLUniformLocation | null>()
  canvas: HTMLCanvasElement

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: false, alpha: true, preserveDrawingBuffer: true })
    if (!gl) throw new Error('WebGL2 indisponível neste navegador')
    this.gl = gl
    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  }

  compile(sd: Pick<ShaderDef, 'uniforms' | 'code'>): CompileResult {
    const gl = this.gl
    const { src, offset } = fragmentSource(sd)
    const sh = (type: number, s: string) => {
      const o = gl.createShader(type)!
      gl.shaderSource(o, s)
      gl.compileShader(o)
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(o) ?? 'erro'
        gl.deleteShader(o)
        throw new Error(log)
      }
      return o
    }
    try {
      const vs = sh(gl.VERTEX_SHADER, VERT)
      const fs = sh(gl.FRAGMENT_SHADER, src)
      const p = gl.createProgram()!
      gl.attachShader(p, vs)
      gl.attachShader(p, fs)
      gl.bindAttribLocation(p, 0, 'aPos')
      gl.linkProgram(p)
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'erro de link')
      if (this.prog) gl.deleteProgram(this.prog)
      this.prog = p
      this.locs.clear()
      return { ok: true }
    } catch (e) {
      // linhas relativas ao código do efeito
      const msg = String((e as Error).message).replace(/\0/g, '').replace(/0:(\d+):/g, (_, n) => `linha ${Math.max(1, Number(n) - offset)}:`)
      return { ok: false, error: msg }
    }
  }

  setTexture(img: HTMLImageElement | null) {
    const gl = this.gl
    if (!img) {
      this.hasTex = false
      return
    }
    this.tex ??= gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, this.tex)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img)
    gl.generateMipmap(gl.TEXTURE_2D)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    this.hasTex = true
  }

  private loc(name: string) {
    if (!this.locs.has(name)) this.locs.set(name, this.gl.getUniformLocation(this.prog!, name))
    return this.locs.get(name)!
  }

  draw(time: number, progress: number, uniforms: Uniform[], values: Record<string, number[]>) {
    const gl = this.gl
    if (!this.prog) return
    const w = this.canvas.width
    const h = this.canvas.height
    gl.viewport(0, 0, w, h)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.useProgram(this.prog)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    gl.uniform1f(this.loc('uTime'), time)
    gl.uniform1f(this.loc('uProgress'), progress)
    gl.uniform2f(this.loc('uResolution'), w, h)
    gl.uniform1f(this.loc('uHasTexture'), this.hasTex ? 1 : 0)
    if (this.tex) {
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, this.tex)
      gl.uniform1i(this.loc('uTexture'), 0)
    }
    for (const u of uniforms) {
      const v = values[u.name] ?? uniformValues(u)
      const l = this.loc(u.name)
      if (!l) continue
      if (v.length === 1) gl.uniform1f(l, v[0])
      else if (v.length === 2) gl.uniform2f(l, v[0], v[1])
      else if (v.length === 3) gl.uniform3f(l, v[0], v[1], v[2])
      else gl.uniform4f(l, v[0], v[1], v[2], v[3])
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  dispose() {
    const gl = this.gl
    if (this.prog) gl.deleteProgram(this.prog)
    if (this.tex) gl.deleteTexture(this.tex)
    gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
}

export const toHex = (v: number[]) => '#' + v.slice(0, 3).map((x) => Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16).padStart(2, '0')).join('')
export const fromHex = (h: string, a = 1) => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255, a]

// "Detalhar": mark a region of a generated mesh (brush, volumes or a text description) and regenerate it with
// more detail (3d.refine_region).
import { OrbitControls } from "@react-three/drei"
import { Canvas, type ThreeEvent } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import { Box, Brush, Circle, Eraser, Loader2, Sparkles, Trash2, Type } from "lucide-react"
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { extOf, isMeshFile, loadMesh, meshStats } from "../mesh"
import { runJob, splitRef } from "../editor/jobs"
import { PrimitivePreview, type GizmoMode } from "../editor/Previews"
import type { PrimitiveTool, Quat, Vec3 } from "../editor/ops"
import { StudioEnvironment } from "../environment"

type Tool = "brush" | "erase" | "volume" | "text"

interface Volume {
  id: string
  shape: "box" | "sphere"
  position: Vec3
  quaternion: Quat
  scale: Vec3
}

let seq = 0

export function RefineEditor({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const file = asset.files.find((f) => f.role === "primary" && isMeshFile(f)) ?? asset.files.find(isMeshFile)
  const hasSource = asset.files.some((f) => f.role === "source" && /\.(png|jpe?g|webp)$/i.test(f.path)) || Boolean(asset.parent)

  const [obj, setObj] = useState<THREE.Object3D | null>(null)
  const [tool, setTool] = useState<Tool>("brush")
  const [points, setPoints] = useState<Vec3[]>([])
  const [radiusPct, setRadiusPct] = useState(4) // % of the model size
  const [volumes, setVolumes] = useState<Volume[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [gizmo, setGizmo] = useState<GizmoMode>("translate")
  const [prompt, setPrompt] = useState("")
  const [octree, setOctree] = useState(384)
  const [busy, setBusy] = useState<string | null>(null)
  const painting = useRef(false)

  useEffect(() => {
    if (!file) return
    let alive = true
    loadMesh(file.url, extOf(file.path)).then((o) => alive && setObj(o)).catch((e) => toast.error(String(e)))
    return () => { alive = false }
  }, [file])

  const stats = useMemo(() => (obj ? meshStats(obj) : null), [obj])
  const size = stats ? Math.max(stats.size.x, stats.size.y, stats.size.z) : 1
  const center = stats ? stats.min.clone().addScaledVector(stats.size, 0.5) : new THREE.Vector3()
  const radius = (size * radiusPct) / 100

  const stamp = (e: ThreeEvent<PointerEvent>) => {
    const p = e.point
    if (tool === "brush") {
      setPoints((ps) => {
        const last = ps[ps.length - 1]
        if (last && p.distanceTo(new THREE.Vector3(...last)) < radius * 0.4) return ps
        return [...ps, [p.x, p.y, p.z]]
      })
    } else if (tool === "erase") {
      setPoints((ps) => ps.filter((q) => p.distanceTo(new THREE.Vector3(...q)) > radius))
    }
  }
  const onDown = (e: ThreeEvent<PointerEvent>) => {
    if ((tool !== "brush" && tool !== "erase") || e.button !== 0) return
    e.stopPropagation()
    painting.current = true
    stamp(e)
    const up = () => { painting.current = false; window.removeEventListener("pointerup", up) }
    window.addEventListener("pointerup", up)
  }
  const onMove = (e: ThreeEvent<PointerEvent>) => {
    if (painting.current) stamp(e)
  }

  const addVolume = (shape: Volume["shape"]) => {
    const s = size * 0.2
    const v: Volume = { id: `v${seq++}`, shape, position: [center.x, center.y, center.z], quaternion: [0, 0, 0, 1], scale: [s, s, s] }
    setVolumes((vs) => [...vs, v])
    setSelected(v.id)
    setTool("volume")
  }
  const patch = (id: string, c: Partial<Volume>) => setVolumes((vs) => vs.map((v) => (v.id === id ? { ...v, ...c } : v)))

  const empty = !points.length && !volumes.length && !prompt.trim()
  const submit = async () => {
    const region = {
      ...(points.length ? { points, radius } : {}),
      ...(volumes.length ? {
        shapes: volumes.map((v) => ({
          shape: v.shape,
          size: v.scale.map(Math.abs),
          matrix: rowMajor(new THREE.Matrix4().compose(new THREE.Vector3(...v.position), new THREE.Quaternion(...v.quaternion), new THREE.Vector3(1, 1, 1))),
        })),
      } : {}),
      ...(prompt.trim() ? { prompt: prompt.trim() } : {}),
    }
    setBusy("Enviando…")
    try {
      const ref = await runJob(project.slug, "3d.refine_region", { input_asset: asset.ref, region, octree_resolution: octree },
        (job) => setBusy(`${job.message ?? "Processando"} ${Math.round((job.progress ?? 0) * 100)}%`))
      toast.success(`Região detalhada: ${ref}`)
      const { slug, version } = splitRef(ref)
      await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 15000 })
    } finally {
      setBusy(null)
    }
  }

  if (!file) return <p className="p-4 text-sm text-muted-foreground">Este asset não tem malha 3D.</p>
  const brushing = tool === "brush" || tool === "erase"

  return (
    <div className="flex h-full min-h-[520px] w-full gap-3">
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-lg bg-muted/40">
        <Canvas dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }} onPointerMissed={() => tool === "volume" && setSelected(null)}>
          <ambientLight intensity={0.45} />
          <StudioEnvironment />
          <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
          <directionalLight position={[5, 10, 7]} intensity={1.6} />
          {obj && stats && (
            <>
              <primitive object={obj} onPointerDown={onDown} onPointerMove={onMove} />
              <FitCamera stats={stats} />
              <FloorGrid stats={stats} />
            </>
          )}
          <Stamps points={points} radius={radius} />
          {volumes.map((v) => (
            <PrimitivePreview key={v.id} tool={asTool(v)} selected={selected === v.id} mode={gizmo} onSelect={() => { setSelected(v.id); setTool("volume") }}
              onChange={(c) => patch(v.id, { position: c.position ?? v.position, quaternion: c.quaternion ?? v.quaternion, scale: c.scale ?? v.scale })} />
          ))}
          {/* while brushing, the left button paints: orbit with the right button, pan with the middle one */}
          <OrbitControls makeDefault mouseButtons={brushing
            ? { LEFT: undefined as unknown as THREE.MOUSE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE }
            : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }} />
        </Canvas>
        <div className="absolute top-2 left-2 flex flex-wrap gap-1 rounded-md bg-background/80 p-1 text-xs backdrop-blur">
          <ToolButton active={tool === "brush"} onClick={() => setTool("brush")} icon={<Brush />} label="Pincel" />
          <ToolButton active={tool === "erase"} onClick={() => setTool("erase")} icon={<Eraser />} label="Apagar" />
          <ToolButton active={tool === "volume"} onClick={() => setTool("volume")} icon={<Box />} label="Volume" />
          <ToolButton active={tool === "text"} onClick={() => setTool("text")} icon={<Type />} label="Texto" />
          {tool === "volume" && (["translate", "rotate", "scale"] as const).map((m) => (
            <Button key={m} size="sm" variant={gizmo === m ? "secondary" : "ghost"} onClick={() => setGizmo(m)}>{{ translate: "Mover", rotate: "Girar", scale: "Escalar" }[m]}</Button>
          ))}
        </div>
        {brushing && <p className="absolute bottom-2 left-2 rounded bg-background/80 px-2 py-1 text-[11px] text-muted-foreground">Botão esquerdo pinta · direito gira · meio arrasta · roda aproxima</p>}
        {busy && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
            <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />{busy}</span>
          </div>
        )}
      </div>

      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        <Section title="Detalhar uma região">
          <p className="text-xs text-muted-foreground">
            Marque a área (rosto, mãos, uma arma…). Ela é recortada da imagem de origem, ampliada e gerada de novo com o
            Hunyuan3D só para ela, e depois costurada no modelo. Leva alguns minutos. Cores/texturas precisam ser refeitas depois.
          </p>
          {!hasSource && <p className="text-xs text-amber-500">Este asset não tem imagem de origem: só funciona com modelos gerados por imagem/texto.</p>}
        </Section>

        <Section title="Pincel">
          <label className="flex items-center gap-2 text-xs">
            <span className="w-14 text-muted-foreground">Tamanho</span>
            <input type="range" min={1} max={15} step={0.5} value={radiusPct} onChange={(e) => setRadiusPct(Number(e.target.value))} className="flex-1" />
            <span className="w-10 text-right tabular-nums">{radiusPct}%</span>
          </label>
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">{points.length} pinceladas</span>
            <Button size="sm" variant="ghost" disabled={!points.length} onClick={() => setPoints([])}><Trash2 />Limpar</Button>
          </div>
        </Section>

        <Section title="Volumes">
          <div className="flex gap-1">
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => addVolume("sphere")}><Circle />Esfera</Button>
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => addVolume("box")}><Box />Caixa</Button>
          </div>
          {volumes.map((v, i) => (
            <div key={v.id} className={cn("flex items-center gap-2 rounded-md border px-2 py-1 text-xs", selected === v.id && "border-primary")}>
              <button type="button" className="flex-1 text-left" onClick={() => { setSelected(v.id); setTool("volume") }}>{v.shape === "box" ? "Caixa" : "Esfera"} {i + 1}</button>
              <Button size="icon-xs" variant="ghost" aria-label="Remover" onClick={() => setVolumes((vs) => vs.filter((x) => x.id !== v.id))}><Trash2 /></Button>
            </div>
          ))}
        </Section>

        <Section title="Por texto">
          <Input value={prompt} onChange={(e) => setPrompt(e.target.value)} onFocus={() => setTool("text")} placeholder='ex.: "o rosto", "a mão direita"' />
          <p className="text-[11px] text-muted-foreground">O Claude localiza a parte na imagem de origem (precisa de ANTHROPIC_API_KEY no servidor). Soma-se ao pincel e aos volumes.</p>
        </Section>

        <Section title="Qualidade">
          <label className="flex items-center justify-between gap-2 text-xs">
            <span className="text-muted-foreground">Detalhe da região</span>
            <select className="h-7 rounded-md border bg-background px-1" value={octree} onChange={(e) => setOctree(Number(e.target.value))}>
              <option value={256}>Rápido (256)</option>
              <option value={384}>Alto (384)</option>
              <option value={512}>Máximo (512)</option>
            </select>
          </label>
          <p className="text-[11px] text-muted-foreground">O resultado mantém o número de triângulos do modelo; a região recebe mais deles por ter mais detalhe.</p>
        </Section>

        <Button className="w-full" disabled={!!busy || empty} onClick={submit}><Sparkles />Detalhar região</Button>
      </aside>
    </div>
  )
}

/** Brush stamps: translucent spheres, one draw call. */
function Stamps({ points, radius }: { points: Vec3[]; radius: number }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  useEffect(() => {
    const mesh = ref.current
    if (!mesh) return
    const m = new THREE.Matrix4()
    points.forEach((p, i) => mesh.setMatrixAt(i, m.compose(new THREE.Vector3(...p), new THREE.Quaternion(), new THREE.Vector3(radius, radius, radius))))
    mesh.count = points.length
    mesh.instanceMatrix.needsUpdate = true
  }, [points, radius])
  return (
    <instancedMesh key={Math.max(64, 2 ** Math.ceil(Math.log2(points.length + 1)))} ref={ref}
      args={[undefined, undefined, Math.max(64, 2 ** Math.ceil(Math.log2(points.length + 1)))]} renderOrder={10}>
      <sphereGeometry args={[1, 16, 12]} />
      <meshBasicMaterial color="#f97316" transparent opacity={0.45} depthWrite={false} depthTest={false} />
    </instancedMesh>
  )
}

function ToolButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return <Button size="sm" variant={active ? "secondary" : "ghost"} onClick={onClick}>{icon}{label}</Button>
}

function asTool(v: Volume): PrimitiveTool {
  return { id: v.id, kind: "primitive", shape: v.shape, mode: "intersect", position: v.position, quaternion: v.quaternion, scale: v.scale }
}

function rowMajor(m: THREE.Matrix4): number[][] {
  const e = m.clone().transpose().elements
  return [0, 1, 2, 3].map((r) => [e[r * 4], e[r * 4 + 1], e[r * 4 + 2], e[r * 4 + 3]])
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      {children}
    </section>
  )
}

export default RefineEditor

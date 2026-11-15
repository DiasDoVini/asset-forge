import { OrbitControls } from "@react-three/drei"
import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import { Brush, Check, Loader2, PaintBucket, Pipette, Sparkles, Undo2, Wand2 } from "lucide-react"
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { api } from "@/core/api"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { meshStats } from "../mesh"
import { runJob, splitRef } from "../editor/jobs"
import { useGltf } from "../anim/AnimationViewer"
import { TexturePainter, hexToRgb, paintableImage, rgbToHex, type RGB } from "./painter"
import { StudioEnvironment } from "../environment"

type Tool = "brush" | "fill" | "picker"

export function PaintEditor({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const isAnim = asset.kind === "animation"
  const glb = asset.files.find((f) => f.role === "primary" && /\.glb$/i.test(f.path))
  const loaded = useGltf(glb?.url)
  const gltf = loaded?.gltf ?? null

  const [tool, setTool] = useState<Tool>("brush")
  const [color, setColor] = useState<RGB>([200, 60, 60])
  const [radius, setRadius] = useState(0.04) // fraction of model size
  const [opacity, setOpacity] = useState(0.6)
  const [hardness, setHardness] = useState(0.5)
  const [busy, setBusy] = useState<string | null>(null)
  const [strokes, setStrokes] = useState(0)
  const [lastPainter, setLastPainter] = useState<TexturePainter | null>(null)
  const [aiViews, setAiViews] = useState<string[]>(["back", "left", "right"])
  const [aiMethod, setAiMethod] = useState<"hunyuan3d-paint" | "project">("hunyuan3d-paint")

  // one painter per textured mesh (part name for animation assets, "model" otherwise)
  const painters = useMemo(() => {
    const out = new Map<string, TexturePainter>()
    if (!gltf) return out
    gltf.scene.updateMatrixWorld(true)
    gltf.scene.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return
      const img = paintableImage(o)
      if (!img) return
      const name = isAnim ? partName(o) : "model"
      if (!out.has(name)) out.set(name, new TexturePainter(o, img))
    })
    return out
  }, [gltf, isAnim])
  const byMesh = useMemo(() => new Map([...painters.values()].map((p) => [p.mesh, p])), [painters])
  // a new version (or asset) brings new painters: forget the undo target of the previous ones
  const [paintersSeen, setPaintersSeen] = useState(painters)
  if (paintersSeen !== painters) {
    setPaintersSeen(painters)
    setLastPainter(null)
    setStrokes(0)
  }
  const stats = useMemo(() => (gltf ? meshStats(gltf.scene) : null), [gltf])
  const size = stats ? Math.max(stats.size.x, stats.size.y, stats.size.z) : 1
  const palette = (project.style?.palette ?? []) as string[]

  const openVersion = async (ref: string) => {
    const { slug, version } = splitRef(ref)
    await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
    nav(`/p/${project.slug}/a/${slug}/${version}`)
  }
  const job = async (label: string, pipeline: string, params: Record<string, unknown>) => {
    setBusy(label)
    try {
      const ref = await runJob(project.slug, pipeline, { input_asset: asset.ref, ...params })
      toast.success(`Salvo como ${ref}`)
      await openVersion(ref)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(null)
    }
  }

  const save = async () => {
    setBusy("Salvando pintura…")
    try {
      const textures: Record<string, string> = {}
      for (const [name, p] of painters) {
        if (!p.dirty) continue
        const up = await api.uploadTemp(project.slug, await p.toBlob(), `${name}.png`)
        textures[name] = up.path
      }
      const ref = await runJob(project.slug, "3d.apply_texture", { input_asset: asset.ref, textures })
      toast.success(`Pintura salva como ${ref}`)
      await openVersion(ref)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  if (!glb) return <p className="p-4 text-sm text-muted-foreground">Pintura disponível para modelos GLB.</p>
  const ready = gltf !== null
  const paintable = painters.size > 0
  const anyDirty = strokes > 0 && [...painters.values()].some((p) => p.dirty)

  return (
    <div className="flex h-full min-h-[540px] w-full gap-3">
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-lg bg-muted/40">
        <Canvas dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }}>
          <ambientLight intensity={0.6} />
          <StudioEnvironment />
          <hemisphereLight args={[0xffffff, 0x8888a0, 1.0]} />
          <directionalLight position={[5, 10, 7]} intensity={1.3} />
          <directionalLight position={[-6, 4, -5]} intensity={0.6} />
          {gltf && stats && (
            <>
              <FitCamera stats={stats} />
              <FloorGrid stats={stats} />
              <PaintSurface scene={gltf.scene} painters={byMesh} tool={tool} enabled={paintable && !busy}
                brush={{ radius: radius * size, color, opacity, hardness }}
                onStroke={(p) => { setStrokes((s) => s + 1); setLastPainter(p) }}
                onPick={(c) => { setColor(c); setTool("brush") }} />
            </>
          )}
          <OrbitControls makeDefault />
        </Canvas>
        {busy && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
            <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />{busy}</span>
          </div>
        )}
        {ready && paintable && (
          <div className="absolute top-2 left-2 flex gap-1 rounded-md bg-background/85 p-1 backdrop-blur">
            <ToolBtn on={tool === "brush"} onClick={() => setTool("brush")} label="Pincel"><Brush /></ToolBtn>
            <ToolBtn on={tool === "fill"} onClick={() => setTool("fill")} label={isAnim ? "Balde (pinta a peça inteira)" : "Balde (pinta tudo)"}><PaintBucket /></ToolBtn>
            <ToolBtn on={tool === "picker"} onClick={() => setTool("picker")} label="Conta-gotas"><Pipette /></ToolBtn>
          </div>
        )}
      </div>

      <aside className="w-72 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        {!ready ? (
          <p className="text-xs text-muted-foreground">Carregando…</p>
        ) : !paintable ? (
          <Section title="Preparar para pintura">
            <p className="text-xs text-muted-foreground">
              Este modelo ainda não tem textura com UV. Prepare-o (as cores atuais são mantidas) ou deixe a IA texturizar.
            </p>
            <Button className="w-full" size="sm" disabled={!!busy} onClick={() => job("Abrindo UV…", "3d.unwrap", { resolution: 2048 })}><Wand2 />Preparar para pintura</Button>
          </Section>
        ) : (
          <>
            <Section title="Cor">
              <div className="flex items-center gap-2">
                <input type="color" className="h-9 w-12 cursor-pointer rounded border bg-transparent" value={rgbToHex(color)} onChange={(e) => setColor(hexToRgb(e.target.value))} aria-label="Cor" />
                <span className="font-mono text-xs">{rgbToHex(color)}</span>
              </div>
              {palette.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {palette.map((c) => (
                    <button key={c} title={c} className={cn("size-6 rounded border", rgbToHex(color) === c.toLowerCase() && "ring-2 ring-primary")}
                      style={{ background: c }} onClick={() => setColor(hexToRgb(c))} />
                  ))}
                </div>
              )}
            </Section>
            <Section title="Pincel">
              <Labeled label="Tamanho"><Slider min={2} max={300} value={[Math.round(radius * 1000)]} onValueChange={(v) => setRadius(num(v) / 1000)} /></Labeled>
              <Labeled label="Opacidade"><Slider min={5} max={100} value={[Math.round(opacity * 100)]} onValueChange={(v) => setOpacity(num(v) / 100)} /></Labeled>
              <Labeled label="Dureza"><Slider min={0} max={100} value={[Math.round(hardness * 100)]} onValueChange={(v) => setHardness(num(v) / 100)} /></Labeled>
            </Section>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={!lastPainter?.canUndo} onClick={() => { lastPainter?.undo(); setStrokes((s) => s + 1) }}><Undo2 />Desfazer</Button>
              <Button size="sm" className="flex-1" disabled={!anyDirty || !!busy} onClick={save}><Check />Salvar pintura</Button>
            </div>
            {isAnim && <p className="text-xs text-muted-foreground">Cada peça tem sua textura; o balde pinta a peça clicada. As animações continuam funcionando.</p>}
          </>
        )}

        <Section title="Texturizar com IA">
          <div className="flex gap-1">
            {([["hunyuan3d-paint", "Hunyuan3D-Paint"], ["project", "Projeção"]] as const).map(([id, label]) => (
              <Button key={id} size="sm" className="flex-1" variant={aiMethod === id ? "secondary" : "outline"} onClick={() => setAiMethod(id)}>{label}</Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {aiMethod === "hunyuan3d-paint"
              ? "Hunyuan3D 2.1 gera 6 vistas coerentes a partir da imagem de origem e as aplica na textura. Melhor qualidade, ~1-2 min."
              : "Projeta a imagem de origem e gera as outras vistas com Qwen-Image-Edit. Mais rápido, menos coerente atrás."}
            {" "}Substitui a textura atual.
          </p>
          {aiMethod === "project" && <div className="flex flex-wrap gap-3 text-xs">
            {[["back", "Costas"], ["left", "Lado esq."], ["right", "Lado dir."]].map(([id, label]) => (
              <label key={id} className="flex items-center gap-1">
                <input type="checkbox" checked={aiViews.includes(id)} onChange={(e) => setAiViews((v) => (e.target.checked ? [...v, id] : v.filter((x) => x !== id)))} />{label}
              </label>
            ))}
          </div>}
          <Button size="sm" variant="secondary" className="w-full" disabled={!!busy}
            onClick={() => job("Texturizando com IA… pode levar alguns minutos", "3d.texture_ai", aiMethod === "project" ? { method: "project", views: aiViews } : { method: "hunyuan3d-paint" })}>
            <Sparkles />Texturizar com IA
          </Button>
        </Section>
      </aside>
    </div>
  )
}

function partName(o: THREE.Object3D): string {
  // part nodes are named after the parts; a mesh may be the node itself or a child of it
  let cur: THREE.Object3D | null = o
  while (cur && !cur.name) cur = cur.parent
  return cur?.name || o.uuid
}

const num = (v: number | readonly number[]) => (Array.isArray(v) ? v[0] : (v as number))

function PaintSurface({ scene, painters, tool, enabled, brush, onStroke, onPick }: {
  scene: THREE.Object3D; painters: Map<THREE.Mesh, TexturePainter>; tool: Tool; enabled: boolean
  brush: { radius: number; color: RGB; opacity: number; hardness: number }
  onStroke: (p: TexturePainter) => void; onPick: (c: RGB) => void
}) {
  const controls = useThree((s) => s.controls) as unknown as { enabled: boolean } | null
  const cursor = useRef<THREE.Mesh>(null)
  const active = useRef<TexturePainter | null>(null)

  useEffect(() => {
    const up = () => {
      if (!active.current) return
      const p = active.current
      active.current = null
      if (controls) controls.enabled = true
      onStroke(p)
    }
    window.addEventListener("pointerup", up)
    return () => window.removeEventListener("pointerup", up)
  }, [controls, onStroke])

  const painterOf = (e: ThreeEvent<PointerEvent>) => (e.object instanceof THREE.Mesh ? painters.get(e.object) : undefined)

  return (
    <>
      <primitive
        object={scene}
        onPointerDown={(e: ThreeEvent<PointerEvent>) => {
          if (!enabled || e.button !== 0) return
          const p = painterOf(e)
          if (!p) return
          e.stopPropagation()
          if (tool === "picker") {
            if (e.uv) onPick(p.sample(e.uv))
            return
          }
          p.begin()
          if (tool === "fill") {
            p.fill(brush.color)
            onStroke(p)
            return
          }
          if (controls) controls.enabled = false
          active.current = p
          p.dab(e.point, brush)
        }}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => {
          const c = cursor.current
          if (c && e.face && tool === "brush" && enabled) {
            c.visible = true
            c.position.copy(e.point)
            c.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), e.face.normal.clone().transformDirection(e.object.matrixWorld))
            c.scale.setScalar(brush.radius)
          }
          if (active.current && painterOf(e) === active.current) active.current.dab(e.point, brush)
        }}
        onPointerOut={() => {
          if (cursor.current) cursor.current.visible = false
        }}
      />
      <mesh ref={cursor} visible={false} renderOrder={10}>
        <ringGeometry args={[0.9, 1, 48]} />
        <meshBasicMaterial color={`rgb(${brush.color.join(",")})`} depthTest={false} transparent opacity={0.9} side={THREE.DoubleSide} />
      </mesh>
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      {children}
    </section>
  )
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}

function ToolBtn({ on, onClick, label, children }: { on: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <Button size="icon" variant={on ? "secondary" : "ghost"} className="size-8" title={label} aria-label={label} aria-pressed={on} onClick={onClick}>{children}</Button>
  )
}

export default PaintEditor

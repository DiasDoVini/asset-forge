import { OrbitControls, TransformControls } from "@react-three/drei"
import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import {
  Box, Brush, Check, Circle, Cone, Cylinder, FlipHorizontal2, Loader2, Move3d, Rotate3d, Scale3d, Scissors, Sparkles,
  Trash2, Type, Undo2, Wand2, X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"
import { STLExporter } from "three/examples/jsm/exporters/STLExporter.js"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { Textarea } from "@/components/ui/textarea"
import { api, primaryFile, type AssetVersion } from "@/core/api"
import { AssetPicker } from "@/core/components/AssetPicker"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { Z_UP, extOf, isMeshFile, loadMesh, meshStats } from "../mesh"
import { runJob, splitRef } from "./jobs"
import {
  cutClipPlane, defaultTool, isPlaced, toEditOps, type BoolMode, type MeshTool, type MirrorTool, type Shape, type Tool,
  type Vec3,
} from "./ops"
import { MirrorPreview, ToolPreview, type GizmoMode } from "./Previews"
import { Sculptor, type BrushMode } from "./sculpt"
import { StudioEnvironment } from "../environment"

type Mode = "edit" | "sculpt"

export function MeshEditor({ asset, project }: PanelProps) {
  const file = asset.files.find((f) => f.role === "primary" && isMeshFile(f)) ?? asset.files.find(isMeshFile)
  const nav = useNavigate()
  const qc = useQueryClient()

  const [obj, setObj] = useState<THREE.Object3D | null>(null)
  const [tools, setTools] = useState<Tool[]>([])
  const [selected, setSelected] = useState<string | null>(null) // tool id or "model"
  const [gizmo, setGizmo] = useState<GizmoMode>("translate")
  const [model, setModel] = useState(() => new THREE.Matrix4())
  const [mode, setMode] = useState<Mode>("edit")
  const [busy, setBusy] = useState<string | null>(null)
  const [brush, setBrush] = useState<SculptBrush>({ mode: "inflate", radius: 0.08, strength: 0.5 })
  const [strokes, setStrokes] = useState(0)
  const [saving, setSaving] = useState(false)
  const sculptor = useMemo(() => (mode === "sculpt" && obj ? new Sculptor(obj) : null), [mode, obj])
  const onStroke = useCallback(() => setStrokes((s) => s + 1), [])

  useEffect(() => {
    if (!file) return
    let alive = true
    loadMesh(file.url, extOf(file.path)).then((o) => alive && setObj(o)).catch((e) => toast.error(String(e)))
    return () => {
      alive = false
    }
  }, [file])

  const stats = useMemo(() => (obj ? meshStats(obj) : null), [obj])
  const size = stats ? Math.max(stats.size.x, stats.size.y, stats.size.z) : 1
  const center = useMemo(() => (stats ? stats.min.clone().addScaledVector(stats.size, 0.5) : new THREE.Vector3()), [stats])
  const unit = file && (Z_UP.has(extOf(file.path)) || asset.kind === "print") ? "mm" : ""

  // ---- tools ------------------------------------------------------------------------
  const add = (t: Tool) => {
    setTools((ts) => [...ts, t])
    setSelected(t.id)
  }
  const update = useCallback((id: string, patch: Partial<Tool>) => {
    setTools((ts) => ts.map((t) => (t.id === id ? ({ ...t, ...patch } as Tool) : t)))
  }, [])
  const remove = (id: string) => {
    setTools((ts) => ts.filter((t) => t.id !== id))
    setSelected((s) => (s === id ? null : s))
  }
  const current = tools.find((t) => t.id === selected)
  const mirror = [...tools].reverse().find((t): t is MirrorTool => t.kind === "mirror")
  const dirty = tools.length > 0 || !model.equals(new THREE.Matrix4())

  // live clipping: cuts and symmetrize keep-side
  useEffect(() => {
    if (!obj) return
    const planes = tools.filter((t) => t.kind === "cut").map((t) => cutClipPlane(t as Extract<Tool, { kind: "cut" }>))
    if (mirror && mirror.mode !== "mirror") {
      const axis = "xyz".indexOf(mirror.axis)
      const n = new THREE.Vector3()
      n.setComponent(axis, mirror.mode === "symmetrize_positive" ? 1 : -1)
      planes.push(new THREE.Plane(n, mirror.mode === "symmetrize_positive" ? -mirror.origin : mirror.origin))
    }
    obj.traverse((o) => {
      if (o instanceof THREE.Mesh) for (const m of [o.material].flat() as THREE.Material[]) {
        m.clippingPlanes = planes
        m.side = planes.length ? THREE.DoubleSide : THREE.FrontSide
        m.needsUpdate = true
      }
    })
  }, [obj, tools, mirror])

  // keyboard: W/E/R gizmo, Delete removes, Esc deselects
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el.closest("input, textarea, [contenteditable]")) return
      if (e.key === "w") setGizmo("translate")
      else if (e.key === "e") setGizmo("rotate")
      else if (e.key === "r") setGizmo("scale")
      else if (e.key === "Escape") setSelected(null)
      else if ((e.key === "Delete" || e.key === "Backspace") && selected && selected !== "model") remove(selected)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [selected])

  const openVersion = async (ref: string) => {
    const { slug, version } = splitRef(ref)
    await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
    nav(`/p/${project.slug}/a/${slug}/${version}`)
  }

  const apply = async () => {
    setBusy("Aplicando edições…")
    try {
      const ref = await runJob(project.slug, "3d.edit", { input_asset: asset.ref, ops: toEditOps(model, tools) })
      toast.success(`Salvo como ${ref}`)
      setTools([])
      setModel(new THREE.Matrix4())
      await openVersion(ref)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  if (!file) return <p className="p-4 text-sm text-muted-foreground">Este asset não tem malha 3D para editar.</p>

  return (
    <div className="flex h-full min-h-[520px] w-full gap-3">
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-lg bg-muted/40">
        <Canvas gl={{ localClippingEnabled: true }} dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }} onPointerMissed={() => setSelected(null)}>
          <ambientLight intensity={0.45} />
          <StudioEnvironment />
          <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
          <directionalLight position={[5, 10, 7]} intensity={1.6} />
          <directionalLight position={[-6, 4, -5]} intensity={0.5} />
          {obj && stats && (
            <>
              <FitCamera stats={stats} />
              <FloorGrid stats={stats} />
              {mode === "edit" ? (
                <>
                  <ModelNode obj={obj} matrix={model} hidden={mirror?.mode === "mirror"} selected={selected === "model"} gizmo={gizmo}
                    onSelect={() => setSelected("model")} onChange={setModel} />
                  {mirror && <MirrorPreview tool={mirror} model={obj} size={size} />}
                  {tools.filter(isPlaced).map((t) => (
                    <ToolPreview key={t.id} tool={t} size={size} selected={selected === t.id} mode={gizmo}
                      onSelect={() => setSelected(t.id)} onChange={(patch) => update(t.id, patch)} />
                  ))}
                </>
              ) : (
                sculptor && <SculptLayer sculptor={sculptor} size={size} brush={brush} onStroke={onStroke} />
              )}
            </>
          )}
          <OrbitControls makeDefault />
        </Canvas>
        {mode === "edit" && (
          <div className="absolute top-2 left-2 flex gap-1 rounded-md bg-background/80 p-1 backdrop-blur">
            <IconToggle on={gizmo === "translate"} onClick={() => setGizmo("translate")} label="Mover (W)"><Move3d /></IconToggle>
            <IconToggle on={gizmo === "rotate"} onClick={() => setGizmo("rotate")} label="Girar (E)"><Rotate3d /></IconToggle>
            <IconToggle on={gizmo === "scale"} onClick={() => setGizmo("scale")} label="Escalar (R)"><Scale3d /></IconToggle>
          </div>
        )}
        {mode === "sculpt" && sculptor && (
          <SculptControls brush={brush} setBrush={setBrush} canUndo={strokes > 0 && sculptor.canUndo} canSave={strokes > 0} saving={saving}
            onUndo={() => { sculptor.undo(); setStrokes((s) => s + 1) }}
            onExit={() => { setMode("edit"); setStrokes(0) }}
            onSave={async () => {
              setSaving(true)
              try {
                const ref = await saveSculpt(sculptor, asset, project.slug, extOf(file.path))
                toast.success(`Escultura salva como ${ref}`)
                setMode("edit")
                setStrokes(0)
                await openVersion(ref)
              } catch (e) {
                toast.error(String(e))
              } finally {
                setSaving(false)
              }
            }} />
        )}
        {busy && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
            <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />{busy}</span>
          </div>
        )}
      </div>

      <aside className="w-72 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        {mode === "sculpt" ? (
          <p className="text-xs text-muted-foreground">Arraste sobre a malha para esculpir. Botão direito/scroll movem a câmera.</p>
        ) : (
          <>
            <Section title="Ferramentas">
              <div className="grid grid-cols-3 gap-1">
                <ToolButton icon={<Move3d />} label="Modelo" active={selected === "model"} onClick={() => setSelected("model")} />
                <ToolButton icon={<Scissors />} label="Cortar" onClick={() => add(defaultTool("cut", center, size))} />
                <ToolButton icon={<Cylinder />} label="Forma" onClick={() => add(defaultTool("primitive", center, size))} />
                <ToolButton icon={<Type />} label="Texto" onClick={() => add(defaultTool("text", center.clone().setZ(stats ? stats.min.z + stats.size.z : 0), size))} />
                <ToolButton icon={<FlipHorizontal2 />} label="Espelhar" onClick={() => add(defaultTool("mirror", center, size))} />
                <ToolButton icon={<Brush />} label="Esculpir" disabled={dirty}
                  title={dirty ? "Aplique ou descarte as edições antes de esculpir" : undefined}
                  onClick={() => { setSelected(null); setMode("sculpt") }} />
              </div>
            </Section>

            {selected === "model" && (
              <Section title="Modelo">
                <p className="text-xs text-muted-foreground">Use o gizmo (W/E/R) para mover, girar ou escalar o modelo inteiro.</p>
                <Button size="sm" variant="outline" onClick={() => setModel(new THREE.Matrix4())}>Resetar transformação</Button>
              </Section>
            )}
            {current && <ToolSettings tool={current} unit={unit} size={size} onChange={(p) => update(current.id, p)} />}

            {tools.length > 0 && (
              <Section title={`Edições (${tools.length})`}>
                <ul className="space-y-1">
                  {tools.map((t, i) => (
                    <li key={t.id} className={cn("flex items-center gap-2 rounded px-2 py-1", selected === t.id ? "bg-accent" : "hover:bg-accent/50")}>
                      <button className="flex-1 truncate text-left" onClick={() => setSelected(t.id)}>{i + 1}. {describe(t)}</button>
                      <Button size="icon-xs" variant="ghost" aria-label="Remover" onClick={() => remove(t.id)}><Trash2 /></Button>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <div className="flex gap-2">
              <Button className="flex-1" disabled={!dirty || !!busy} onClick={apply}><Check />Aplicar</Button>
              <Button variant="outline" disabled={!dirty || !!busy} onClick={() => { setTools([]); setModel(new THREE.Matrix4()); setSelected(null) }}><X />Descartar</Button>
            </div>
            <p className="text-xs text-muted-foreground">Aplicar cria uma nova versão; a atual fica no histórico.</p>

            <AISection asset={asset} projectSlug={project.slug} size={size} center={center} busy={!!busy} setBusy={setBusy}
              onPart={(t) => add(t)} onEdited={openVersion} />
          </>
        )}
      </aside>
    </div>
  )
}

// ---- model -----------------------------------------------------------------------------

function ModelNode({ obj, matrix, hidden, selected, gizmo, onSelect, onChange }: {
  obj: THREE.Object3D; matrix: THREE.Matrix4; hidden?: boolean; selected: boolean; gizmo: GizmoMode
  onSelect: () => void; onChange: (m: THREE.Matrix4) => void
}) {
  const [group, setGroup] = useState<THREE.Group | null>(null)
  useEffect(() => {
    if (!group) return
    matrix.decompose(group.position, group.quaternion, group.scale)
  }, [group, matrix])
  return (
    <>
      <group ref={setGroup} visible={!hidden} onClick={(e) => { e.stopPropagation(); onSelect() }}>
        <primitive object={obj} />
      </group>
      {selected && group && (
        <TransformControls object={group} mode={gizmo} onObjectChange={() => { group.updateMatrix(); onChange(group.matrix.clone()) }} />
      )}
    </>
  )
}

// ---- tool settings ----------------------------------------------------------------------

function describe(t: Tool): string {
  const modeName: Record<BoolMode, string> = { add: "somar", subtract: "subtrair", intersect: "interseção" }
  switch (t.kind) {
    case "cut": return `Corte (manter ${t.keep === "back" ? "abaixo" : "acima"})`
    case "primitive": return `${SHAPES.find((s) => s.id === t.shape)?.label} · ${modeName[t.mode]}`
    case "text": return `Texto “${t.text}” · ${t.mode === "subtract" ? "gravado" : "relevo"}`
    case "mirror": return t.mode === "mirror" ? `Espelhar ${t.axis.toUpperCase()}` : `Simetrizar ${t.axis.toUpperCase()}`
    case "mesh": return `Parte ${t.asset} · ${modeName[t.mode]}`
  }
}

const SHAPES: { id: Shape; label: string; icon: ReactNode }[] = [
  { id: "box", label: "Cubo", icon: <Box /> },
  { id: "cylinder", label: "Cilindro", icon: <Cylinder /> },
  { id: "sphere", label: "Esfera", icon: <Circle /> },
  { id: "cone", label: "Cone", icon: <Cone /> },
]

function ToolSettings({ tool, unit, size, onChange }: { tool: Tool; unit: string; size: number; onChange: (p: Partial<Tool>) => void }) {
  const step = niceStep(size)
  switch (tool.kind) {
    case "cut":
      return (
        <Section title="Corte">
          <Segmented value={tool.keep} onChange={(keep) => onChange({ keep })} options={[["back", "Manter abaixo"], ["front", "Manter acima"]]} />
          <p className="text-xs text-muted-foreground">Mova/gire o plano (W/E). A seta indica o lado mantido; a face cortada é fechada.</p>
        </Section>
      )
    case "primitive":
      return (
        <Section title="Forma">
          <div className="grid grid-cols-4 gap-1">
            {SHAPES.map((s) => (
              <IconToggle key={s.id} on={tool.shape === s.id} onClick={() => onChange({ shape: s.id })} label={s.label}>{s.icon}</IconToggle>
            ))}
          </div>
          <ModeToggle value={tool.mode} onChange={(mode) => onChange({ mode })} />
          <VecInput label={`Tamanho${unit ? ` (${unit})` : ""}`} value={tool.scale} step={step} onChange={(scale) => onChange({ scale })} />
        </Section>
      )
    case "text":
      return (
        <Section title="Texto">
          <Textarea rows={2} value={tool.text} onChange={(e) => onChange({ text: e.target.value })} />
          <Segmented value={tool.mode === "subtract" ? "subtract" : "add"} onChange={(mode) => onChange({ mode })} options={[["add", "Relevo"], ["subtract", "Gravado"]]} />
          <NumInput label={`Altura${unit ? ` (${unit})` : ""}`} value={tool.height} step={step} onChange={(height) => onChange({ height })} />
          <NumInput label={`Profundidade${unit ? ` (${unit})` : ""}`} value={tool.depth} step={step / 10} onChange={(depth) => onChange({ depth })} />
          <p className="text-xs text-muted-foreground">O texto sai da face do retângulo na direção da seta azul do gizmo (Z local). Para gravar, afunde-o na superfície.</p>
        </Section>
      )
    case "mirror":
      return (
        <Section title="Espelhar">
          <Segmented value={tool.axis} onChange={(axis) => onChange({ axis })} options={[["x", "X"], ["y", "Y"], ["z", "Z"]]} />
          <Segmented value={tool.mode} onChange={(mode) => onChange({ mode })}
            options={[["symmetrize_positive", "Simetrizar +"], ["symmetrize_negative", "Simetrizar −"], ["mirror", "Espelhar"]]} />
          <NumInput label="Posição do plano" value={tool.origin} step={step} onChange={(origin) => onChange({ origin })} />
        </Section>
      )
    case "mesh":
      return (
        <Section title="Parte">
          <p className="truncate font-mono text-xs">{tool.asset}</p>
          <ModeToggle value={tool.mode} onChange={(mode) => onChange({ mode })} />
          <p className="text-xs text-muted-foreground">Posicione com o gizmo (W/E/R). Ao aplicar, a parte é unida à malha.</p>
        </Section>
      )
  }
}

// ---- AI ------------------------------------------------------------------------------------

function AISection({ asset, projectSlug, size, center, busy, setBusy, onPart, onEdited }: {
  asset: AssetVersion; projectSlug: string; size: number; center: THREE.Vector3; busy: boolean
  setBusy: (s: string | null) => void; onPart: (t: MeshTool) => void; onEdited: (ref: string) => void
}) {
  const [instruction, setInstruction] = useState("")
  const [partPrompt, setPartPrompt] = useState("")
  const [existing, setExisting] = useState<string | undefined>()

  const aiEdit = async () => {
    setBusy("Editando com IA (imagem → 3D)… pode levar ~2 min")
    try {
      const ref = await runJob(projectSlug, "3d.ai_edit", { input_asset: asset.ref, instruction })
      toast.success(`Edição com IA salva como ${ref}`)
      setInstruction("")
      onEdited(ref)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  const addPart = async (ref: string) => {
    const { slug, version } = splitRef(ref.includes("@v") ? ref : `${ref}@v0`)
    const versions = await api.versions(projectSlug, slug)
    const v = (version ? versions.find((x) => x.version === version) : undefined) ?? versions[0]
    const file = v && (primaryFile(v) ?? v.files.find(isMeshFile))
    if (!v || !file || !isMeshFile(file)) throw new Error(`${ref} não tem malha 3D`)
    const part = await loadMesh(file.url, extOf(file.path))
    const s = meshStats(part)
    const k = (size * 0.3) / Math.max(s.size.x, s.size.y, s.size.z, 1e-9) // start at ~30% of the model
    const pos: Vec3 = [center.x, center.y, center.z]
    onPart({ id: `part${Date.now().toString(36)}`, kind: "mesh", asset: v.ref, url: file.url, mode: "add", position: pos, quaternion: [0, 0, 0, 1], scale: [k, k, k] })
  }

  const generatePart = async () => {
    setBusy("Gerando parte com IA… ~2 min")
    try {
      const ref = await runJob(projectSlug, "3d.text_to_3d", { prompt: partPrompt, asset_type: "object" })
      await addPart(ref)
      toast.success(`Parte ${ref} gerada: posicione e aplique`)
      setPartPrompt("")
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section title="Inteligência artificial">
      <div className="space-y-1.5">
        <Label className="text-xs">Editar com IA</Label>
        <Textarea rows={2} placeholder="ex.: abra a tampa · adicione dois chifres · deixe mais alto" value={instruction} onChange={(e) => setInstruction(e.target.value)} />
        <Button size="sm" className="w-full" disabled={busy || !instruction.trim()} onClick={aiEdit}><Wand2 />Editar e regenerar</Button>
        <p className="text-xs text-muted-foreground">Edita a imagem de origem (Qwen-Image-Edit) e gera o 3D de novo com a mesma seed.</p>
      </div>
      <div className="space-y-1.5 pt-2">
        <Label className="text-xs">Gerar parte e encaixar</Label>
        <Input placeholder="ex.: alça de ferro curva" value={partPrompt} onChange={(e) => setPartPrompt(e.target.value)} />
        <Button size="sm" variant="secondary" className="w-full" disabled={busy || !partPrompt.trim()} onClick={generatePart}><Sparkles />Gerar parte</Button>
      </div>
      <div className="space-y-1.5 pt-2">
        <Label className="text-xs">Usar outro asset como parte</Label>
        <AssetPicker project={projectSlug} value={existing} modality="3d" placeholder="Escolher malha" onChange={setExisting} />
        <Button size="sm" variant="outline" className="w-full" disabled={busy || !existing}
          onClick={() => existing && addPart(existing).catch((e) => toast.error(String(e)))}>Adicionar</Button>
      </div>
    </Section>
  )
}

// ---- sculpt ----------------------------------------------------------------------------------

const BRUSHES: { id: BrushMode; label: string }[] = [
  { id: "inflate", label: "Inflar" },
  { id: "deflate", label: "Afundar" },
  { id: "smooth", label: "Suavizar" },
  { id: "flatten", label: "Achatar" },
]

interface SculptBrush {
  mode: BrushMode
  radius: number // fraction of the model size
  strength: number
}

/** In-canvas part of sculpt mode: the editable mesh, pointer strokes and the brush cursor. */
function SculptLayer({ sculptor, size, brush, onStroke }: { sculptor: Sculptor; size: number; brush: SculptBrush; onStroke: () => void }) {
  const controls = useThree((s) => s.controls) as unknown as { enabled: boolean } | null
  const cursor = useRef<THREE.Mesh>(null)
  const drawing = useRef(false)
  const dab = (e: ThreeEvent<PointerEvent>) => sculptor.dab(e.point, { mode: brush.mode, radius: brush.radius * size, strength: brush.strength })
  const placeCursor = (e: ThreeEvent<PointerEvent>) => {
    const c = cursor.current
    if (!c || !e.face) return
    c.visible = true
    c.position.copy(e.point)
    c.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), e.face.normal.clone().transformDirection(sculptor.mesh.matrixWorld))
    c.scale.setScalar(brush.radius * size)
  }
  const setOrbit = (on: boolean) => {
    if (controls) controls.enabled = on
  }
  // end strokes on any pointer release: the pointer is often off the mesh when the button goes up
  useEffect(() => {
    const up = () => {
      if (!drawing.current) return
      drawing.current = false
      if (controls) controls.enabled = true
      sculptor.end()
      onStroke()
    }
    window.addEventListener("pointerup", up)
    return () => window.removeEventListener("pointerup", up)
  }, [sculptor, controls, onStroke])
  return (
    <>
      <primitive
        object={sculptor.mesh}
        onPointerDown={(e: ThreeEvent<PointerEvent>) => {
          if (e.button !== 0) return
          e.stopPropagation()
          setOrbit(false)
          drawing.current = true
          sculptor.begin()
          dab(e)
        }}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => {
          placeCursor(e)
          if (drawing.current) dab(e)
        }}

        onPointerOut={() => {
          if (cursor.current) cursor.current.visible = false
        }}
      />
      <mesh ref={cursor} visible={false} renderOrder={10}>
        <ringGeometry args={[0.92, 1, 48]} />
        <meshBasicMaterial color="#f59e0b" depthTest={false} transparent opacity={0.9} side={THREE.DoubleSide} />
      </mesh>
    </>
  )
}

/** DOM controls of sculpt mode (rendered over the canvas, outside the three.js tree). */
function SculptControls({ brush, setBrush, canUndo, canSave, saving, onUndo, onSave, onExit }: {
  brush: SculptBrush; setBrush: (b: SculptBrush) => void; canUndo: boolean; canSave: boolean; saving: boolean
  onUndo: () => void; onSave: () => void; onExit: () => void
}) {
  const val = (v: number | readonly number[]) => (Array.isArray(v) ? v[0] : (v as number))
  return (
    <div className="absolute bottom-2 left-1/2 z-10 -translate-x-1/2 space-y-2 rounded-lg border bg-background/90 p-2 shadow-lg backdrop-blur">
      <div className="flex flex-wrap items-center gap-1">
        {BRUSHES.map((b) => (
          <Button key={b.id} size="sm" variant={brush.mode === b.id ? "secondary" : "ghost"} onClick={() => setBrush({ ...brush, mode: b.id })}>{b.label}</Button>
        ))}
      </div>
      <div className="flex items-center gap-3 text-xs">
        <span>Raio</span>
        <Slider className="w-28" min={1} max={40} value={[Math.round(brush.radius * 100)]} onValueChange={(v) => setBrush({ ...brush, radius: val(v) / 100 })} />
        <span>Força</span>
        <Slider className="w-28" min={5} max={100} value={[Math.round(brush.strength * 100)]} onValueChange={(v) => setBrush({ ...brush, strength: val(v) / 100 })} />
      </div>
      <div className="flex gap-1">
        <Button size="sm" variant="ghost" disabled={!canUndo || saving} onClick={onUndo}><Undo2 />Desfazer</Button>
        <Button size="sm" disabled={!canSave || saving} onClick={onSave}>{saving ? <Loader2 className="animate-spin" /> : <Check />}Salvar escultura</Button>
        <Button size="sm" variant="outline" disabled={saving} onClick={onExit}><X />Sair</Button>
      </div>
    </div>
  )
}

/** Upload the sculpted mesh as a new version (same format family as the source). */
async function saveSculpt(sculptor: Sculptor, asset: AssetVersion, projectSlug: string, fileExt: string): Promise<string> {
  let blob: Blob
  let filename: string
  if (Z_UP.has(fileExt)) {
    // back to Z-up for STL/3MF/PLY sources (the viewer shows everything Y-up)
    const m = sculptor.mesh.clone()
    m.geometry = sculptor.mesh.geometry.clone().rotateX(Math.PI / 2)
    const data = new STLExporter().parse(m, { binary: true }) as DataView
    blob = new Blob([new Uint8Array(data.buffer, data.byteOffset, data.byteLength).slice()], { type: "model/stl" })
    filename = "model.stl"
  } else {
    const buf = (await new GLTFExporter().parseAsync(sculptor.mesh, { binary: true })) as ArrayBuffer
    blob = new Blob([buf], { type: "model/gltf-binary" })
    filename = "model.glb"
  }
  const v = await api.uploadAsset(projectSlug, blob, { asset: asset.slug, parent: asset.ref, modality: "3d", kind: asset.kind, filename })
  return v.ref
}
// ---- small UI pieces ----------------------------------------------------------------------------


function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      {children}
    </section>
  )
}

function ToolButton({ icon, label, onClick, active, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; active?: boolean; disabled?: boolean; title?: string }) {
  return (
    <Button variant={active ? "secondary" : "outline"} className="h-14 flex-col gap-1 px-1 text-xs" disabled={disabled} title={title} onClick={onClick}>
      {icon}
      {label}
    </Button>
  )
}

function IconToggle({ on, onClick, label, children }: { on: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <Button size="icon" variant={on ? "secondary" : "ghost"} className="size-8" title={label} aria-label={label} aria-pressed={on} onClick={onClick}>
      {children}
    </Button>
  )
}

function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(([v, label]) => (
        <Button key={v} size="sm" variant={value === v ? "secondary" : "outline"} className="flex-1" onClick={() => onChange(v)}>{label}</Button>
      ))}
    </div>
  )
}

function ModeToggle({ value, onChange }: { value: BoolMode; onChange: (m: BoolMode) => void }) {
  return <Segmented value={value} onChange={onChange} options={[["add", "Somar"], ["subtract", "Subtrair"], ["intersect", "Interseção"]]} />
}

function NumInput({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (v: number) => void }) {
  return (
    <label className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <Input type="number" className="h-7 w-24" step={step} value={round(value)} onChange={(e) => {
        const v = parseFloat(e.target.value)
        if (Number.isFinite(v)) onChange(v)
      }} />
    </label>
  )
}

function VecInput({ label, value, step, onChange }: { label: string; value: Vec3; step: number; onChange: (v: Vec3) => void }) {
  return (
    <div className="space-y-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <div className="grid grid-cols-3 gap-1">
        {value.map((c, i) => (
          <Input key={i} type="number" className="h-7" step={step} value={round(Math.abs(c))} aria-label={"XYZ"[i]} onChange={(e) => {
            const v = parseFloat(e.target.value)
            if (!Number.isFinite(v) || v <= 0) return
            const next = [...value] as Vec3
            next[i] = v
            onChange(next)
          }} />
        ))}
      </div>
    </div>
  )
}

const round = (v: number) => Math.round(v * 1000) / 1000
function niceStep(extent: number) {
  return 10 ** Math.floor(Math.log10(Math.max(extent, 1e-6) / 20))
}

export default MeshEditor

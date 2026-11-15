import { OrbitControls, TransformControls } from "@react-three/drei"
import { Canvas } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import { Box, Circle, Cylinder, Loader2, Package, Plus, Scissors, Trash2 } from "lucide-react"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AssetPicker } from "@/core/components/AssetPicker"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { extOf, isMeshFile, loadMesh, meshStats } from "../mesh"
import { runJob, splitRef } from "../editor/jobs"
import { PrimitivePreview, type GizmoMode } from "../editor/Previews"
import type { PrimitiveTool, Quat, Vec3 } from "../editor/ops"
import type { AnimationInfo } from "./clip"
import { StudioEnvironment } from "../environment"

interface RegionPart {
  id: string
  name: string
  shape: "box" | "cylinder" | "sphere"
  position: Vec3
  quaternion: Quat
  scale: Vec3
  pivot: Vec3
  parent: string // "" = default
  fromPart: string // "" = default (root)
}

let seq = 0

export function RigEditor({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const isAnim = asset.kind === "animation"
  const info = (asset.extra?.animation ?? null) as AnimationInfo | null
  const file = isAnim ? asset.files.find((f) => f.role === "primary") : asset.files.find((f) => f.role === "primary" && isMeshFile(f)) ?? asset.files.find(isMeshFile)

  const [obj, setObj] = useState<THREE.Object3D | null>(null)
  const [parts, setParts] = useState<RegionPart[]>([])
  const [props, setProps] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null) // part id, or "pivot:<id>"
  const [gizmo, setGizmo] = useState<GizmoMode>("translate")
  const [busy, setBusy] = useState(false)
  const [picker, setPicker] = useState<string | undefined>()
  const [components, setComponents] = useState(false)

  useEffect(() => {
    if (!file) return
    let alive = true
    loadMesh(file.url, extOf(file.path)).then((o) => alive && setObj(o)).catch((e) => toast.error(String(e)))
    return () => { alive = false }
  }, [file])

  // regions usually sit inside the model: make it see-through while parts are being placed
  useEffect(() => {
    obj?.traverse((o) => {
      if (o instanceof THREE.Mesh) for (const m of [o.material].flat() as THREE.Material[]) {
        m.transparent = parts.length > 0
        m.opacity = parts.length > 0 ? 0.45 : 1
        m.depthWrite = parts.length === 0
        m.needsUpdate = true
      }
    })
  }, [obj, parts.length])

  const stats = useMemo(() => (obj ? meshStats(obj) : null), [obj])
  const size =stats ? Math.max(stats.size.x, stats.size.y, stats.size.z) : 1
  const center = stats ? stats.min.clone().addScaledVector(stats.size, 0.5) : new THREE.Vector3()
  const existing = info?.parts.map((p) => p.name) ?? []
  const allNames = [...existing, ...parts.map((p) => p.name)]

  const add = () => {
    const s = size * 0.25
    const p: RegionPart = {
      id: `r${seq++}`, name: `peca${parts.length + 1}`, shape: "box",
      position: [center.x, center.y, center.z], quaternion: [0, 0, 0, 1], scale: [s, s, s],
      pivot: [center.x, center.y, center.z], parent: "", fromPart: "",
    }
    setParts((ps) => [...ps, p])
    setSelected(p.id)
  }
  const patch = (id: string, change: Partial<RegionPart>) => setParts((ps) => ps.map((p) => (p.id === id ? { ...p, ...change } : p)))
  const current = parts.find((p) => p.id === selected || `pivot:${p.id}` === selected)

  const submit = async () => {
    const body = {
      parts: parts.map((p) => ({
        name: p.name,
        region: {
          shape: p.shape,
          size: p.scale.map(Math.abs),
          matrix: rowMajor(new THREE.Matrix4().compose(new THREE.Vector3(...p.position), new THREE.Quaternion(...p.quaternion), new THREE.Vector3(1, 1, 1))),
        },
        pivot: p.pivot,
        ...(p.parent ? { parent: p.parent } : {}),
        ...(p.fromPart ? { from_part: p.fromPart } : {}),
      })),
      props: props.map((a) => ({ asset: a })),
      components,
    }
    setBusy(true)
    try {
      const ref = await runJob(project.slug, "3d.rig", { input_asset: asset.ref, ...body }, undefined, isAnim ? { asset: asset.slug } : {})
      toast.success(isAnim ? `Peças separadas: ${ref}` : `Asset de animação criado: ${ref}`)
      const { slug, version } = splitRef(ref)
      await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(false)
    }
  }

  if (!file) return <p className="p-4 text-sm text-muted-foreground">Este asset não tem malha 3D.</p>
  const nameError = new Set(allNames).size !== allNames.length ? "nomes de peça repetidos" : parts.some((p) => !/^[\w-]+$/.test(p.name)) ? "use só letras, números, _ e -" : null

  return (
    <div className="flex h-full min-h-[520px] w-full gap-3">
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-lg bg-muted/40">
        <Canvas dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }} onPointerMissed={() => setSelected(null)}>
          <ambientLight intensity={0.45} />
          <StudioEnvironment />
          <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
          <directionalLight position={[5, 10, 7]} intensity={1.6} />
          {obj && stats && (
            <>
              <primitive object={obj} />
              <FitCamera stats={stats} />
              <FloorGrid stats={stats} />
            </>
          )}
          {parts.map((p) => (
            <group key={p.id}>
              <PrimitivePreview tool={asTool(p)} selected={selected === p.id} mode={gizmo} onSelect={() => setSelected(p.id)}
                onChange={(c) => patch(p.id, { position: c.position ?? p.position, quaternion: c.quaternion ?? p.quaternion, scale: c.scale ?? p.scale })} />
              <PivotHandle pivot={p.pivot} size={size} selected={selected === `pivot:${p.id}`} onSelect={() => setSelected(`pivot:${p.id}`)}
                onChange={(v) => patch(p.id, { pivot: v })} />
            </group>
          ))}
          <OrbitControls makeDefault />
        </Canvas>
        <div className="absolute top-2 left-2 flex gap-1 rounded-md bg-background/80 p-1 text-xs backdrop-blur">
          {(["translate", "rotate", "scale"] as const).map((m) => (
            <Button key={m} size="sm" variant={gizmo === m ? "secondary" : "ghost"} onClick={() => setGizmo(m)}>{{ translate: "Mover", rotate: "Girar", scale: "Escalar" }[m]}</Button>
          ))}
        </div>
        {busy && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
            <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />Separando peças…</span>
          </div>
        )}
      </div>

      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        <Section title={isAnim ? "Separar mais peças" : "Preparar para animação"}>
          <p className="text-xs text-muted-foreground">
            Cada peça é tudo o que estiver dentro da forma amarela. A bolinha laranja é o pivô (onde ela gira): arraste-a até a dobradiça,
            o encaixe ou a articulação. {isAnim ? "Os clipes existentes são mantidos." : "O resto vira a peça “body”."}
          </p>
          {isAnim && <p className="text-xs">Peças atuais: {existing.join(", ")}</p>}
          <Button size="sm" variant="secondary" className="w-full" onClick={add}><Plus />Adicionar peça</Button>
        </Section>

        {parts.map((p) => (
          <div key={p.id} className={cn("space-y-2 rounded-md border p-2", current?.id === p.id && "border-primary")}>
            <div className="flex items-center gap-1">
              <Input className="h-7" value={p.name} onChange={(e) => patch(p.id, { name: e.target.value })} onFocus={() => setSelected(p.id)} aria-label="Nome da peça" />
              <Button size="icon-xs" variant="ghost" aria-label="Remover" onClick={() => setParts((ps) => ps.filter((x) => x.id !== p.id))}><Trash2 /></Button>
            </div>
            <div className="flex gap-1">
              {([["box", <Box key="b" />], ["cylinder", <Cylinder key="c" />], ["sphere", <Circle key="s" />]] as const).map(([s, icon]) => (
                <Button key={s} size="icon-sm" variant={p.shape === s ? "secondary" : "ghost"} onClick={() => patch(p.id, { shape: s })} aria-label={s}>{icon}</Button>
              ))}
              <Button size="sm" variant="ghost" className="ml-auto text-xs" onClick={() => setSelected(`pivot:${p.id}`)}>Mover pivô</Button>
            </div>
            <label className="flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground">Presa a</span>
              <select className="h-7 rounded-md border bg-background px-1" value={p.parent} onChange={(e) => patch(p.id, { parent: e.target.value })}>
                <option value="">{isAnim ? "(peça de origem)" : "body"}</option>
                {allNames.filter((n) => n !== p.name).map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            {isAnim && (
              <label className="flex items-center justify-between gap-2 text-xs">
                <span className="text-muted-foreground">Separar de</span>
                <select className="h-7 rounded-md border bg-background px-1" value={p.fromPart} onChange={(e) => patch(p.id, { fromPart: e.target.value })}>
                  <option value="">{existing[0]}</option>
                  {existing.slice(1).map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            )}
          </div>
        ))}

        <Section title="Objetos de cena">
          <p className="text-xs text-muted-foreground">Outros assets que aparecem na animação (ex.: uma arma a ser pega do chão).</p>
          <div className="flex gap-1">
            <div className="min-w-0 flex-1"><AssetPicker project={project.slug} value={picker} modality="3d" placeholder="Escolher asset" onChange={setPicker} /></div>
            <Button size="sm" variant="outline" disabled={!picker} onClick={() => { if (picker) setProps((ps) => [...ps, picker]); setPicker(undefined) }}><Package />Incluir</Button>
          </div>
          {props.map((a) => (
            <div key={a} className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate font-mono">{a}</span>
              <Button size="icon-xs" variant="ghost" onClick={() => setProps((ps) => ps.filter((x) => x !== a))}><Trash2 /></Button>
            </div>
          ))}
        </Section>

        {!isAnim && (
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={components} onChange={(e) => setComponents(e.target.checked)} />
            Cada pedaço solto vira uma peça
          </label>
        )}

        {nameError && <p className="text-xs text-destructive">{nameError}</p>}
        <Button className="w-full" disabled={busy || !!nameError || (isAnim && !parts.length && !props.length)} onClick={submit}>
          <Scissors />{isAnim ? "Separar peças" : "Criar asset de animação"}
        </Button>
      </aside>
    </div>
  )
}

function asTool(p: RegionPart): PrimitiveTool {
  return { id: p.id, kind: "primitive", shape: p.shape, mode: "intersect", position: p.position, quaternion: p.quaternion, scale: p.scale }
}

function PivotHandle({ pivot, size, selected, onSelect, onChange }: { pivot: Vec3; size: number; selected: boolean; onSelect: () => void; onChange: (v: Vec3) => void }) {
  const [obj, setObj] = useState<THREE.Mesh | null>(null)
  return (
    <>
      <mesh ref={setObj} position={pivot} renderOrder={20} onClick={(e) => { e.stopPropagation(); onSelect() }}>
        <sphereGeometry args={[size * 0.015, 16, 12]} />
        <meshBasicMaterial color="#f97316" depthTest={false} />
      </mesh>
      {selected && obj && <TransformControls object={obj} mode="translate" size={0.6} onObjectChange={() => onChange(obj.position.toArray() as Vec3)} />}
    </>
  )
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

export default RigEditor

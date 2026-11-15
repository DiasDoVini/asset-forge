// "Esqueleto": create a skinned skeleton for a mesh (3d.skeleton), and edit its joints afterwards
// (drag, mirror left/right, add/remove/rename bones) - applying recomputes the skin weights, clips are kept.
import { OrbitControls, TransformControls } from "@react-three/drei"
import { Canvas } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import { Bone, FlipHorizontal2, Loader2, Plus, Sparkles, Trash2, Wand2 } from "lucide-react"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { StudioEnvironment } from "../environment"
import { extOf, isMeshFile, loadMesh, meshStats } from "../mesh"
import { runJob, splitRef } from "../editor/jobs"
import type { AnimationInfo, V3 } from "./clip"

interface J {
  name: string
  parent: string | null
  position: V3
  deform: boolean
}

type Template = "auto" | "humanoid" | "creature"

const mirrorName = (n: string) => (n.startsWith("Left") ? `Right${n.slice(4)}` : n.startsWith("Right") ? `Left${n.slice(5)}`
  : /_l$/.test(n) ? `${n.slice(0, -2)}_r` : /_r$/.test(n) ? `${n.slice(0, -2)}_l` : null)
const sideOf = (n: string) => (n.startsWith("Left") || /_l$/.test(n) ? "left" : n.startsWith("Right") || /_r$/.test(n) ? "right" : null)

export function SkeletonEditor({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const info = (asset.extra?.animation ?? null) as AnimationInfo | null
  const isSkeleton = asset.kind === "animation" && info?.type === "skeleton"
  const meshFile = isSkeleton
    ? asset.files.find((f) => f.path.endsWith("skin/mesh.glb"))
    : asset.files.find((f) => f.role === "primary" && isMeshFile(f)) ?? asset.files.find(isMeshFile)

  const [obj, setObj] = useState<THREE.Object3D | null>(null)
  const [joints, setJoints] = useState<J[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [mirror, setMirror] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [template, setTemplate] = useState<Template>("auto")
  const [flip, setFlip] = useState(false)
  const [handle, setHandle] = useState<THREE.Mesh | null>(null)

  useEffect(() => {
    if (!meshFile) return
    let alive = true
    loadMesh(meshFile.url, extOf(meshFile.path)).then((o) => {
      if (!alive) return
      if (isSkeleton) o.traverse((m) => {
        if (m instanceof THREE.Mesh) for (const mat of [m.material].flat() as THREE.Material[]) { mat.transparent = true; mat.opacity = 0.55; mat.depthWrite = false }
      })
      setObj(o)
    }).catch((e) => toast.error(String(e)))
    return () => { alive = false }
  }, [meshFile, isSkeleton])

  const reset = () => {
    setJoints((info?.parts ?? []).filter((p) => p.bone).map((p) => ({ name: p.name, parent: p.parent, position: [...p.pivot] as V3, deform: p.deform !== false })))
    setDirty(false)
  }
  useEffect(reset, [info]) // eslint-disable-line react-hooks/exhaustive-deps

  const stats = useMemo(() => (obj ? meshStats(obj) : null), [obj])
  const size = stats ? Math.max(stats.size.x, stats.size.y, stats.size.z) : 1
  const centerX = (joints.find((j) => j.name === "Hips") ?? joints.find((j) => j.name === "pelvis"))?.position[0] ?? (stats ? stats.min.x + stats.size.x / 2 : 0)
  const current = joints.find((j) => j.name === selected) ?? null

  const move = (name: string, p: V3) => {
    setJoints((js) => js.map((j) => {
      if (j.name === name) return { ...j, position: p }
      if (mirror && j.name === mirrorName(name)) return { ...j, position: [2 * centerX - p[0], p[1], p[2]] }
      return j
    }))
    setDirty(true)
  }
  const rename = (from: string, to: string) => {
    if (!to || joints.some((j) => j.name === to)) return
    setJoints((js) => js.map((j) => ({ ...j, name: j.name === from ? to : j.name, parent: j.parent === from ? to : j.parent })))
    setSelected(to)
    setDirty(true)
  }
  const addChild = () => {
    const base = current ?? joints[0]
    let i = 1
    while (joints.some((j) => j.name === `Bone${i}`)) i++
    const name = `Bone${i}`
    const p: V3 = base ? [base.position[0], base.position[1] + size * 0.08, base.position[2]] : [centerX, size / 2, 0]
    setJoints((js) => [...js, { name, parent: base?.name ?? null, position: p, deform: true }])
    setSelected(name)
    setDirty(true)
  }
  const remove = (name: string) => {
    const gone = joints.find((j) => j.name === name)
    if (!gone) return
    if (!gone.parent && joints.filter((j) => !j.parent).length === 1 && joints.some((j) => j.parent === name)) {
      toast.error("o osso raiz não pode ser removido enquanto tiver filhos")
      return
    }
    setJoints((js) => js.filter((j) => j.name !== name).map((j) => (j.parent === name ? { ...j, parent: gone.parent } : j)))
    setSelected(null)
    setDirty(true)
  }

  const run = async (label: string, params: Record<string, unknown>, sameAsset: boolean) => {
    setBusy(label)
    try {
      const ref = await runJob(project.slug, "3d.skeleton", { input_asset: asset.ref, ...params },
        (job) => setBusy(`${job.message ?? label} ${Math.round((job.progress ?? 0) * 100)}%`), sameAsset ? { asset: asset.slug } : {})
      toast.success(`Esqueleto pronto: ${ref}`)
      const { slug, version } = splitRef(ref)
      await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(null)
    }
  }

  if (!meshFile) return <p className="p-4 text-sm text-muted-foreground">Este asset não tem malha 3D.</p>
  const byName = Object.fromEntries(joints.map((j) => [j.name, j]))
  const r = size * 0.012

  return (
    <div className="flex h-full min-h-[560px] w-full gap-3">
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
          {isSkeleton && (
            <>
              <lineSegments renderOrder={30}>
                <bufferGeometry key={JSON.stringify(joints.map((j) => j.position))}>
                  <bufferAttribute attach="attributes-position" args={[new Float32Array(joints.flatMap((j) => (j.parent && byName[j.parent] ? [...byName[j.parent].position, ...j.position] : []))), 3]} />
                </bufferGeometry>
                <lineBasicMaterial color="#f97316" depthTest={false} />
              </lineSegments>
              {joints.map((j) => (
                <mesh key={j.name} position={j.position} renderOrder={31}
                  ref={(m) => { if (m && j.name === selected && handle !== m) setHandle(m) }}
                  onClick={(e) => { e.stopPropagation(); setSelected(j.name) }}>
                  <sphereGeometry args={[j.name === selected ? r * 1.6 : j.deform ? r : r * 0.6, 12, 8]} />
                  <meshBasicMaterial color={j.name === selected ? "#facc15" : sideOf(j.name) === "left" ? "#ef4444" : sideOf(j.name) === "right" ? "#3b82f6" : "#22c55e"} depthTest={false} />
                </mesh>
              ))}
              {current && handle && (
                <TransformControls object={handle} mode="translate" size={0.7}
                  onObjectChange={() => move(current.name, handle.position.toArray() as V3)} />
              )}
            </>
          )}
          <OrbitControls makeDefault />
        </Canvas>
        {busy && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
            <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />{busy}</span>
          </div>
        )}
      </div>

      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        <Section title={isSkeleton ? "Encaixar de novo" : "Criar esqueleto"}>
          <p className="text-xs text-muted-foreground">
            O esqueleto deforma a malha de forma contínua (ombros, cotovelos, joelhos, coluna). Humanoide usa os nomes do
            Mixamo (compatível com as animações dele e com o retargeting das engines). Criatura detecta patas e cauda.
          </p>
          <div className="grid grid-cols-3 gap-1 rounded-md bg-muted p-1">
            {([["auto", "Automático"], ["humanoid", "Humanoide"], ["creature", "Criatura"]] as const).map(([t, label]) => (
              <button key={t} type="button" onClick={() => setTemplate(t)}
                className={cn("rounded px-2 py-1 text-xs font-medium", template === t ? "bg-background shadow-sm" : "text-muted-foreground")}>{label}</button>
            ))}
          </div>
          {template === "creature" && (
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={flip} onChange={(e) => setFlip(e.target.checked)} />A cabeça está do outro lado</label>
          )}
          <Button className="w-full" disabled={!!busy} onClick={() => run("Criando o esqueleto…", { template, flip }, isSkeleton)}>
            <Wand2 />{isSkeleton ? "Encaixar automaticamente de novo" : "Criar esqueleto"}
          </Button>
        </Section>

        {isSkeleton && (
          <>
            <Section title="Articulações">
              <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} /><FlipHorizontal2 className="size-3.5" />Espelhar esquerda/direita</label>
              <ul className="max-h-64 space-y-0.5 overflow-y-auto">
                {joints.map((j) => (
                  <li key={j.name}>
                    <button className={cn("w-full rounded px-2 py-0.5 text-left text-xs", selected === j.name ? "bg-accent" : "hover:bg-accent/50", !j.deform && "text-muted-foreground")}
                      onClick={() => setSelected(j.name)}>
                      <span style={{ paddingLeft: jointDepth(joints, j.name) * 10 }}>{j.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="flex gap-1">
                <Button size="sm" variant="outline" className="flex-1" onClick={addChild}><Plus />Osso filho</Button>
                <Button size="sm" variant="outline" disabled={!current} onClick={() => current && remove(current.name)}><Trash2 /></Button>
              </div>
            </Section>

            {current && (
              <Section title={`Osso · ${current.name}`}>
                <RenameField key={current.name} name={current.name} onRename={(n) => rename(current.name, n)} />
                <label className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">Preso a</span>
                  <select className="h-7 rounded-md border bg-background px-1" value={current.parent ?? ""}
                    onChange={(e) => { const v = e.target.value || null; setJoints((js) => js.map((j) => (j.name === current.name ? { ...j, parent: v } : j))); setDirty(true) }}>
                    <option value="">(raiz)</option>
                    {joints.filter((j) => j.name !== current.name && !isDescendant(joints, j.name, current.name)).map((j) => <option key={j.name} value={j.name}>{j.name}</option>)}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={current.deform}
                  onChange={(e) => { setJoints((js) => js.map((j) => (j.name === current.name ? { ...j, deform: e.target.checked } : j))); setDirty(true) }} />Deforma a malha</label>
                <p className="text-xs text-muted-foreground">Arraste a articulação no 3D (seta do gizmo). Ela deve ficar dentro do corpo, no centro da junta.</p>
              </Section>
            )}

            <div className="space-y-2">
              <Button className="w-full" disabled={!dirty || !!busy} onClick={() => run("Aplicando e recalculando os pesos…",
                { joints: joints.map((j) => ({ name: j.name, parent: j.parent, position: j.position, deform: j.deform })), keep_clips: true }, true)}>
                <Bone />Aplicar (recalcular pesos)
              </Button>
              {dirty && <Button size="sm" variant="ghost" className="w-full" onClick={reset}>Descartar mudanças</Button>}
              <p className="text-xs text-muted-foreground"><Sparkles className="mr-1 inline size-3" />Os clipes são mantidos. Anime na aba “Animar”.</p>
            </div>
          </>
        )}
      </aside>
    </div>
  )
}

function RenameField({ name, onRename }: { name: string; onRename: (n: string) => void }) {
  const [v, setV] = useState(name)
  return (
    <Input className="h-7" value={v} aria-label="Nome do osso" onChange={(e) => setV(e.target.value.replace(/[^\w\-:.]/g, ""))}
      onBlur={() => v !== name && onRename(v)} onKeyDown={(e) => e.key === "Enter" && v !== name && onRename(v)} />
  )
}

function jointDepth(js: J[], name: string): number {
  let d = 0
  let j = js.find((x) => x.name === name)
  while (j?.parent) { d++; j = js.find((x) => x.name === j!.parent) }
  return d
}

function isDescendant(js: J[], name: string, ancestor: string): boolean {
  let j = js.find((x) => x.name === name)
  while (j?.parent) {
    if (j.parent === ancestor) return true
    j = js.find((x) => x.name === j!.parent)
  }
  return false
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      {children}
    </section>
  )
}

export default SkeletonEditor

import { OrbitControls, TransformControls } from "@react-three/drei"
import { Canvas, useFrame } from "@react-three/fiber"
import { useQueryClient } from "@tanstack/react-query"
import {
  Clapperboard, Diamond, Download, Film, Footprints, Hand, Loader2, Move3d, Pause, Play, Plus, Rotate3d, Save, Scale3d, Sparkles, Trash2,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, downloadBlob } from "@/core/api"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { meshStats } from "../mesh"
import { runJob, splitRef } from "../editor/jobs"
import { MixerDriver, useGltf } from "./AnimationViewer"
import {
  PRESETS, captureRest, emptyClip, keyFromNode, presetKeys, removeKey, toThreeClip, upsertKey, walkCycle,
  type AnimationInfo, type Attach, type Clip, type PresetId,
} from "./clip"
import { renderSheet } from "./render"

type Gizmo = "translate" | "rotate" | "scale"

export function AnimationEditor({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const info = (asset.extra?.animation ?? { parts: [], clips: [], units: "m" }) as AnimationInfo
  const glbFile = asset.files.find((f) => f.role === "primary")
  const clipsFile = asset.files.find((f) => f.path === "clips.json")
  const loaded = useGltf(glbFile?.url)
  const gltf = loaded?.gltf ?? null

  const [clips, setClips] = useState<Clip[]>([])
  const [clip, setClip] = useState<Clip>(() => emptyClip("clip"))
  const [dirty, setDirty] = useState(false)
  const [part, setPart] = useState<string | null>(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [gizmo, setGizmo] = useState<Gizmo>("rotate")
  const [busy, setBusy] = useState<string | null>(null)
  // skeleton rigs: bones are picked through joint handles or the nearest bone to a click on the skin
  const isSkeleton = info.type === "skeleton"
  const listed = isSkeleton ? info.parts.filter((p) => !p.bone || p.deform !== false) : info.parts

  // saved clips
  useEffect(() => {
    if (!clipsFile) return
    fetch(clipsFile.url).then((r) => r.json()).then((d: { clips: Clip[] }) => {
      setClips(d.clips)
      if (d.clips.length) setClip(d.clips[0])
      else setClip(emptyClip("clip"))
      setDirty(false)
    }).catch(() => undefined)
  }, [clipsFile])

  const rest = useMemo(() => (gltf ? captureRest(gltf.scene, info.parts) : {}), [gltf, info.parts])
  const stats = useMemo(() => (gltf ? meshStats(gltf.scene) : null), [gltf])
  const mixer = useMemo(() => (gltf ? new THREE.AnimationMixer(gltf.scene) : null), [gltf])
  const threeClip = useMemo(() => toThreeClip(clip, rest), [clip, rest])
  useEffect(() => {
    if (!mixer) return
    mixer.stopAllAction()
    mixer.uncacheRoot(mixer.getRoot())
    // parts without a track go back to rest
    for (const [name, pos] of Object.entries(rest)) {
      const n = gltf?.scene.getObjectByName(name)
      if (n) { n.position.copy(pos); n.quaternion.identity(); n.scale.set(1, 1, 1) }
    }
    mixer.clipAction(threeClip).play()
    mixer.setTime(time)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mixer, threeClip])

  const node = part && gltf ? gltf.scene.getObjectByName(part) ?? null : null
  const update = (c: Clip) => { setClip(c); setDirty(true) }
  const keyHere = clip.tracks.find((t) => t.part === part)?.keys.some((k) => Math.abs(k.t - time) < 1e-3)

  const setKey = useCallback(() => {
    if (!node || !part) return
    update(upsertKey(clip, part, keyFromNode(node, rest[part], Math.min(time, clip.duration))))
  }, [node, part, clip, rest, time])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable]")) return
      if (e.key === "k") setKey()
      else if (e.key === "w") setGizmo("translate")
      else if (e.key === "e") setGizmo("rotate")
      else if (e.key === "r") setGizmo("scale")
      else if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [setKey])

  const openVersion = async (ref: string) => {
    const { slug, version } = splitRef(ref)
    await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
    nav(`/p/${project.slug}/a/${slug}/${version}`)
  }
  const job = async (label: string, params: Record<string, unknown>, pipeline = "3d.animate") => {
    setBusy(label)
    try {
      const ref = await runJob(project.slug, pipeline, { input_asset: asset.ref, ...params })
      toast.success(`Salvo como ${ref}`)
      setDirty(false)
      await openVersion(ref)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(null)
    }
  }

  const keyTimes = clip.tracks.find((t) => t.part === part)?.keys.map((k) => k.t) ?? []
  const allKeyTimes = [...new Set(clip.tracks.flatMap((t) => t.keys.map((k) => k.t)))]
  const roots = info.parts.filter((p) => p.parent === null)

  if (!glbFile) return <p className="p-4 text-sm text-muted-foreground">Asset sem animated.glb.</p>

  return (
    <div className="flex h-full min-h-[560px] w-full gap-3">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg bg-muted/40">
        <div className="relative min-h-0 flex-1">
          <Canvas dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }} onPointerMissed={() => setPart(null)}>
            <ambientLight intensity={0.45} />
            <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
            <directionalLight position={[5, 10, 7]} intensity={1.6} />
            {gltf && stats && (
              <>
                <primitive object={gltf.scene} onClick={(e: { stopPropagation: () => void; object: THREE.Object3D; point: THREE.Vector3 }) => {
                  e.stopPropagation()
                  let o: THREE.Object3D | null = e.object
                  while (o && !info.parts.some((p) => p.name === o!.name)) o = o.parent
                  if (o) setPart(o.name)
                  else if (isSkeleton) setPart(nearestBone(gltf.scene, listed.filter((p) => p.bone), e.point))
                }} />
                {isSkeleton && <JointHandles scene={gltf.scene} bones={listed.filter((p) => p.bone)} selected={part}
                  radius={Math.max(stats.size.x, stats.size.y, stats.size.z) * 0.012} onSelect={setPart} />}
                <FitCamera stats={stats} />
                <FloorGrid stats={stats} />
              </>
            )}
            {node && !playing && <TransformControls object={node} mode={gizmo} size={0.8} />}
            {mixer && <MixerDriver mixer={mixer} playing={playing} time={time} duration={clip.duration} loop={clip.loop} onTime={(t) => {
              setTime(t)
              if (!clip.loop && t >= clip.duration) setPlaying(false)
            }} />}
            <OrbitControls makeDefault />
          </Canvas>
          <div className="absolute top-2 left-2 flex gap-1 rounded-md bg-background/80 p-1 backdrop-blur">
            <Toggle on={gizmo === "translate"} onClick={() => setGizmo("translate")} label="Mover (W)"><Move3d /></Toggle>
            <Toggle on={gizmo === "rotate"} onClick={() => setGizmo("rotate")} label="Girar (E)"><Rotate3d /></Toggle>
            <Toggle on={gizmo === "scale"} onClick={() => setGizmo("scale")} label="Escalar (R)"><Scale3d /></Toggle>
          </div>
          {busy && (
            <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
              <span className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm shadow"><Loader2 className="size-4 animate-spin" />{busy}</span>
            </div>
          )}
        </div>
        <Timeline time={time} duration={clip.duration} playing={playing} keyTimes={keyTimes} allKeyTimes={allKeyTimes} attach={clip.attach}
          onTime={(t) => { setPlaying(false); setTime(t) }} onPlay={() => setPlaying(!playing)} />
      </div>

      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        <Section title="Clipe">
          <div className="flex gap-1">
            <select className="h-8 flex-1 rounded-md border bg-background px-2" value={clips.some((c) => c.name === clip.name) ? clip.name : "__new"}
              onChange={(e) => {
                const c = clips.find((x) => x.name === e.target.value)
                if (c) { setClip(c); setDirty(false); setTime(0) }
              }}>
              {clips.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              {!clips.some((c) => c.name === clip.name) && <option value="__new">{clip.name} (novo)</option>}
            </select>
            <Button size="icon-sm" variant="outline" title="Novo clipe" onClick={() => { setClip(emptyClip(`clip${clips.length + 1}`)); setDirty(true); setTime(0) }}><Plus /></Button>
          </div>
          <div className="grid grid-cols-[1fr_5rem] gap-2">
            <Input value={clip.name} onChange={(e) => update({ ...clip, name: e.target.value.replace(/[^\w-]/g, "_") })} aria-label="Nome do clipe" />
            <Input type="number" min={0.1} step={0.1} value={clip.duration} aria-label="Duração (s)"
              onChange={(e) => { const v = parseFloat(e.target.value); if (v > 0) update({ ...clip, duration: v }) }} />
          </div>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={clip.loop} onChange={(e) => update({ ...clip, loop: e.target.checked })} />Loop</label>
          <div className="flex gap-2">
            <Button className="flex-1" size="sm" disabled={!dirty || !!busy || !clip.tracks.length && !clip.attach.length}
              onClick={() => job("Salvando clipe…", { clip })}><Save />Salvar clipe</Button>
            <Button size="sm" variant="outline" disabled={!!busy || !clips.some((c) => c.name === clip.name)}
              onClick={() => job("Apagando clipe…", { name: clip.name, delete: true })}><Trash2 /></Button>
          </div>
          {dirty && <p className="text-xs text-amber-600 dark:text-amber-400">Alterações não salvas.</p>}
        </Section>

        {isSkeleton && info.template === "humanoid" && (
          <Section title="Movimentos prontos">
            <Button size="sm" variant="secondary" className="w-full" onClick={() => {
              const names = new Set(info.parts.map((p) => p.name))
              update({ ...clip, loop: true, tracks: [...clip.tracks.filter((t) => !walkCycle(1, 1, names).some((w) => w.part === t.part)),
                ...walkCycle(clip.duration, 1, names)] })
            }}><Footprints />Ciclo de caminhada ({clip.duration}s)</Button>
            <p className="text-xs text-muted-foreground">Pernas, joelhos, balanço dos braços, quadril e tronco, em loop. Ajuste depois com keyframes.</p>
          </Section>
        )}

        <Section title={isSkeleton ? "Ossos" : "Peças"}>
          <ul className="space-y-0.5">
            {listed.map((p) => (
              <li key={p.name}>
                <button className={cn("w-full rounded px-2 py-1 text-left", part === p.name ? "bg-accent" : "hover:bg-accent/50")} onClick={() => setPart(p.name)}>
                  <span style={{ paddingLeft: depth(info, p.name) * 12 }}>{p.name}</span>
                  {p.source.startsWith("asset:") && <span className="ml-1 text-xs text-muted-foreground">(objeto)</span>}
                </button>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            {isSkeleton ? "Clique num ponto laranja ou direto na malha (pega o osso mais próximo). Ajuste as articulações na aba “Esqueleto”."
              : "Falta uma peça (carregador, tampa, braço)? Separe-a na aba “Peças”."}
          </p>
        </Section>

        {part && (
          <Section title={`Keyframes · ${part}`}>
            <div className="flex gap-1">
              <Button size="sm" className="flex-1" onClick={setKey}><Diamond />Gravar em {time.toFixed(2)}s (K)</Button>
              <Button size="sm" variant="outline" disabled={!keyHere} onClick={() => update(removeKey(clip, part, time))}><Trash2 /></Button>
            </div>
            <p className="text-xs text-muted-foreground">Posicione a peça com o gizmo (gira no pivô) e grave. Espaço toca/pausa.</p>
            <PresetForm onApply={(id, axis, amount, distance) => {
              const keys = presetKeys(id, clip.duration, axis, amount, distance)
              update({ ...clip, tracks: [...clip.tracks.filter((t) => t.part !== part), { part, keys }] })
            }} />
          </Section>
        )}

        {roots.length > 1 && (
          <Section title="Pegar / soltar objetos">
            <AttachForm parts={info.parts.map((p) => p.name)} roots={roots.map((p) => p.name)} time={time}
              onAdd={(a) => update({ ...clip, attach: [...clip.attach, a] })} />
            {clip.attach.map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <Hand className="size-3.5" />
                <span className="flex-1">{a.part} → {a.to} ({a.start.toFixed(2)}–{a.end === null ? "fim" : a.end.toFixed(2)}s)</span>
                <Button size="icon-xs" variant="ghost" onClick={() => update({ ...clip, attach: clip.attach.filter((_, j) => j !== i) })}><Trash2 /></Button>
              </div>
            ))}
            <p className="text-xs text-muted-foreground">A pré-visualização dos anexos aparece depois de salvar.</p>
          </Section>
        )}

        <AISection busy={!!busy} clipName={clip.name} duration={clip.duration} loop={clip.loop}
          onRun={(prompt) => job("Planejando a animação com IA…", { prompt, name: clip.name, duration: clip.duration, loop: clip.loop })} />

        <ExportSection assetSlug={asset.slug} version={asset.version} projectSlug={project.slug} glbUrl={glbFile.url}
          clipName={clips.some((c) => c.name === clip.name) ? clip.name : null} dirty={dirty} setBusy={setBusy} />
      </aside>
    </div>
  )
}

/** The bone whose segment (joint to first child) passes closest to a point on the skin. */
function nearestBone(scene: THREE.Object3D, bones: { name: string }[], point: THREE.Vector3): string | null {
  let best: string | null = null
  let bestD = Infinity
  const a = new THREE.Vector3()
  const b = new THREE.Vector3()
  const seg = new THREE.Line3()
  const closest = new THREE.Vector3()
  for (const bone of bones) {
    const node = scene.getObjectByName(bone.name)
    if (!node) continue
    node.getWorldPosition(a)
    const child = node.children.find((c) => (c as THREE.Bone).isBone)
    if (child) child.getWorldPosition(b)
    else b.copy(a)
    seg.set(a, b).closestPointToPoint(point, true, closest)
    const d = closest.distanceTo(point)
    if (d < bestD) { bestD = d; best = bone.name }
  }
  return best
}

/** Clickable joint markers that follow the animated bones; drawn on top of the skin. */
function JointHandles({ scene, bones, selected, radius, onSelect }: {
  scene: THREE.Object3D; bones: { name: string; parent: string | null }[]; selected: string | null; radius: number
  onSelect: (name: string) => void
}) {
  const refs = useRef<Record<string, THREE.Mesh | null>>({})
  const lines = useRef<THREE.LineSegments>(null)
  const tmp = useMemo(() => new THREE.Vector3(), [])
  useFrame(() => {
    const pts: number[] = []
    for (const b of bones) {
      const node = scene.getObjectByName(b.name)
      const m = refs.current[b.name]
      if (!node || !m) continue
      node.getWorldPosition(tmp)
      m.position.copy(tmp)
      const parent = b.parent ? scene.getObjectByName(b.parent) : null
      if (parent && bones.some((x) => x.name === b.parent)) {
        pts.push(tmp.x, tmp.y, tmp.z)
        parent.getWorldPosition(tmp)
        pts.push(tmp.x, tmp.y, tmp.z)
      }
    }
    if (lines.current) {
      lines.current.geometry.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3))
      lines.current.geometry.computeBoundingSphere()
    }
  })
  return (
    <group>
      <lineSegments ref={lines} renderOrder={30}>
        <bufferGeometry />
        <lineBasicMaterial color="#f97316" depthTest={false} transparent opacity={0.7} />
      </lineSegments>
      {bones.map((b) => (
        <mesh key={b.name} ref={(m) => { refs.current[b.name] = m }} renderOrder={31}
          onClick={(e) => { e.stopPropagation(); onSelect(b.name) }}>
          <sphereGeometry args={[selected === b.name ? radius * 1.6 : radius, 12, 8]} />
          <meshBasicMaterial color={selected === b.name ? "#facc15" : "#f97316"} depthTest={false} />
        </mesh>
      ))}
    </group>
  )
}

function depth(info: AnimationInfo, name: string): number {
  let d = 0
  let p = info.parts.find((x) => x.name === name)
  while (p?.parent) { d++; p = info.parts.find((x) => x.name === p!.parent) }
  return d
}

// ---- timeline ---------------------------------------------------------------------------------

function Timeline({ time, duration, playing, keyTimes, allKeyTimes, attach, onTime, onPlay }: {
  time: number; duration: number; playing: boolean; keyTimes: number[]; allKeyTimes: number[]; attach: Attach[]
  onTime: (t: number) => void; onPlay: () => void
}) {
  const pct = (t: number) => `${(Math.min(t, duration) / duration) * 100}%`
  const pick = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    onTime(Math.max(0, Math.min(duration, ((e.clientX - r.left) / r.width) * duration)))
  }
  return (
    <div className="flex items-center gap-2 border-t bg-background/80 px-2 py-2 text-xs">
      <Button size="icon-sm" variant="ghost" aria-label={playing ? "Pausar" : "Tocar"} onClick={onPlay}>{playing ? <Pause /> : <Play />}</Button>
      <div className="relative h-8 flex-1 cursor-pointer rounded bg-muted" onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); pick(e) }}
        onPointerMove={(e) => e.buttons === 1 && pick(e)}>
        {attach.map((a, i) => (
          <div key={i} className="absolute bottom-0 h-1.5 rounded bg-sky-500/60" style={{ left: pct(a.start), width: `calc(${pct(a.end ?? duration)} - ${pct(a.start)})` }} />
        ))}
        {allKeyTimes.map((t) => <div key={`a${t}`} className="absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-muted-foreground/50" style={{ left: pct(t) }} />)}
        {keyTimes.map((t) => <div key={t} className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-amber-500" style={{ left: pct(t) }} />)}
        <div className="absolute inset-y-0 w-0.5 bg-primary" style={{ left: pct(time) }} />
      </div>
      <span className="w-20 text-right font-mono">{time.toFixed(2)} / {duration.toFixed(2)}s</span>
    </div>
  )
}

// ---- forms -----------------------------------------------------------------------------------

function PresetForm({ onApply }: { onApply: (id: PresetId, axis: "x" | "y" | "z", amount: number, distance: number) => void }) {
  const [id, setId] = useState<PresetId>("hinge")
  const [axis, setAxis] = useState<"x" | "y" | "z">("x")
  const [amount, setAmount] = useState(90)
  const [distance, setDistance] = useState(0.1)
  return (
    <div className="space-y-1.5 rounded-md border p-2">
      <Label className="text-xs">Movimento pronto</Label>
      <div className="flex gap-1">
        <select className="h-7 flex-1 rounded-md border bg-background px-1 text-xs" value={id} onChange={(e) => setId(e.target.value as PresetId)}>
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <select className="h-7 rounded-md border bg-background px-1 text-xs" value={axis} onChange={(e) => setAxis(e.target.value as "x" | "y" | "z")} aria-label="Eixo">
          {["x", "y", "z"].map((a) => <option key={a} value={a}>{a.toUpperCase()}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-1 text-xs">
        <label className="space-y-0.5"><span className="text-muted-foreground">Graus / voltas / %</span>
          <Input type="number" className="h-7" value={amount} onChange={(e) => setAmount(parseFloat(e.target.value) || 0)} /></label>
        <label className="space-y-0.5"><span className="text-muted-foreground">Distância</span>
          <Input type="number" className="h-7" step={0.01} value={distance} onChange={(e) => setDistance(parseFloat(e.target.value) || 0)} /></label>
      </div>
      <Button size="sm" variant="secondary" className="w-full" onClick={() => onApply(id, axis, amount, distance)}><Clapperboard />Aplicar na peça</Button>
    </div>
  )
}

function AttachForm({ parts, roots, time, onAdd }: { parts: string[]; roots: string[]; time: number; onAdd: (a: Attach) => void }) {
  const [prop, setProp] = useState(roots[roots.length - 1])
  const [to, setTo] = useState(parts[0])
  const [start, setStart] = useState<number | null>(null)
  return (
    <div className="space-y-1.5 rounded-md border p-2 text-xs">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1">
        <select className="h-7 rounded-md border bg-background px-1" value={prop} onChange={(e) => setProp(e.target.value)}>
          {roots.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <span>preso a</span>
        <select className="h-7 rounded-md border bg-background px-1" value={to} onChange={(e) => setTo(e.target.value)}>
          {parts.filter((p) => p !== prop).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>
      <div className="flex gap-1">
        <Button size="sm" variant="outline" className="flex-1" onClick={() => setStart(time)}>Pegar em {(start ?? time).toFixed(2)}s</Button>
        <Button size="sm" variant="outline" className="flex-1" disabled={start === null || time <= (start ?? 0)}
          onClick={() => { onAdd({ part: prop, to, start: start ?? 0, end: time }); setStart(null) }}>Soltar em {time.toFixed(2)}s</Button>
      </div>
      <Button size="sm" variant="ghost" className="w-full" onClick={() => { onAdd({ part: prop, to, start: start ?? time, end: null }); setStart(null) }}>Pegar e segurar até o fim</Button>
    </div>
  )
}

function AISection({ busy, clipName, duration, loop, onRun }: { busy: boolean; clipName: string; duration: number; loop: boolean; onRun: (prompt: string) => void }) {
  const [prompt, setPrompt] = useState("")
  return (
    <Section title="Animar com IA">
      <Textarea rows={3} placeholder='ex.: "a tampa abre devagar e balança" · "o carregador sai, um novo entra e o ferrolho é puxado" · "pega o @rifle-exercito do chão e coloca nas costas"'
        value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <Button size="sm" className="w-full" disabled={busy || !prompt.trim()} onClick={() => onRun(prompt)}><Sparkles />Gerar clipe “{clipName}”</Button>
      <p className="text-xs text-muted-foreground">
        Duração alvo {duration}s{loop ? ", em loop" : ""}. Mencione outros assets com @nome para incluí-los. Se faltar uma peça separada, a IA avisa qual.
        Requer credencial da API do Claude no servidor.
      </p>
    </Section>
  )
}

function ExportSection({ assetSlug, version, projectSlug, glbUrl, clipName, dirty, setBusy }: {
  assetSlug: string; version: number; projectSlug: string; glbUrl: string; clipName: string | null; dirty: boolean
  setBusy: (s: string | null) => void
}) {
  const [format, setFormat] = useState<"gif" | "mp4" | "webm">("gif")
  const [fps, setFps] = useState(24)
  const [size, setSize] = useState(512)
  const [transparent, setTransparent] = useState(true)

  const exportGlb = async () => {
    try {
      const { blob, filename } = await api.exportAsset(projectSlug, assetSlug, version, "3d.animation_glb", clipName ? { clips: [clipName] } : {})
      downloadBlob(blob, filename)
    } catch (e) { toast.error(String(e)) }
  }
  const exportMedia = async () => {
    if (!clipName) return
    setBusy("Renderizando quadros…")
    try {
      const { sheet, frames, columns } = await renderSheet(glbUrl, {
        clip: clipName, fps, size, transparent: transparent && format !== "mp4", background: "#ffffff",
        onProgress: (d, n) => setBusy(`Renderizando quadros… ${d}/${n}`),
      })
      setBusy("Codificando…")
      const up = await api.uploadTemp(projectSlug, sheet, "frames.png")
      const { blob, filename } = await api.exportAsset(projectSlug, assetSlug, version, "3d.animation_media",
        { sheet: up.path, frames, columns, fps, format, loop: true, name: clipName })
      downloadBlob(blob, filename)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }
  return (
    <Section title="Exportar">
      {dirty && <p className="text-xs text-amber-600 dark:text-amber-400">Salve o clipe antes de exportar.</p>}
      <Button size="sm" variant="outline" className="w-full" disabled={dirty} onClick={exportGlb}><Download />GLB para jogos{clipName ? ` (${clipName})` : ""}</Button>
      <div className="grid grid-cols-3 gap-1 text-xs">
        <select className="h-7 rounded-md border bg-background px-1" value={format} onChange={(e) => setFormat(e.target.value as "gif" | "mp4" | "webm")}>
          <option value="gif">GIF</option><option value="mp4">MP4</option><option value="webm">WebM</option>
        </select>
        <select className="h-7 rounded-md border bg-background px-1" value={fps} onChange={(e) => setFps(Number(e.target.value))}>
          {[12, 15, 24, 30, 60].map((f) => <option key={f} value={f}>{f} fps</option>)}
        </select>
        <select className="h-7 rounded-md border bg-background px-1" value={size} onChange={(e) => setSize(Number(e.target.value))}>
          {[256, 384, 512, 768, 1024].map((s) => <option key={s} value={s}>{s}px</option>)}
        </select>
      </div>
      {format !== "mp4" && (
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={transparent} onChange={(e) => setTransparent(e.target.checked)} />Fundo transparente</label>
      )}
      <Button size="sm" variant="outline" className="w-full" disabled={dirty || !clipName} onClick={exportMedia}><Film />Exportar {format.toUpperCase()}</Button>
    </Section>
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

function Toggle({ on, onClick, label, children }: { on: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <Button size="icon" variant={on ? "secondary" : "ghost"} className="size-8" title={label} aria-label={label} aria-pressed={on} onClick={onClick}>{children}</Button>
  )
}

export default AnimationEditor

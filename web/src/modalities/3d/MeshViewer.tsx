import { Grid, Html, OrbitControls } from "@react-three/drei"
import { Canvas, useThree } from "@react-three/fiber"
import { Box, FlipVertical2, Grid3x3, Palette, Printer, Rotate3d, Ruler } from "lucide-react"
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

import { Z_UP, extOf, formatLength, loadMesh, meshStats, project3d, type MeshStats } from "./mesh"
import { LIGHTS, SWATCHES, ViewerLights, applyLook, useLook, type Look } from "./look"

export interface MeshViewerProps {
  url: string
  file: { path: string; mime?: string | null; role?: string }
  project?: unknown
  /** assets of kind "print" are always in millimeters, whatever the file format */
  asset?: { kind?: string }
  className?: string
}

export function MeshViewer({ url, file, project, asset, className }: MeshViewerProps) {
  const ext = extOf(file.path)
  // load result is tagged with its url so a stale model never shows for a new url
  const [loaded, setLoaded] = useState<{ url: string; obj: THREE.Object3D | null; error: string | null }>()
  const [wireframe, setWireframe] = useState(false)
  const [showGrid, setShowGrid] = useState(true)
  const [showDims, setShowDims] = useState(true)
  const [showVolume, setShowVolume] = useState(true)
  const [autoRotate, setAutoRotate] = useState(false)
  // view-only 180° flip: shows the face lying on the bed (e.g. a split part's cut face with its pin holes)
  const [flipped, setFlipped] = useState(false)
  const [look, setLook] = useLook()
  const [lookOpen, setLookOpen] = useState(false)

  useEffect(() => {
    let alive = true
    let current: THREE.Object3D | null = null
    loadMesh(url, ext)
      .then((o) => {
        current = o
        if (alive) setLoaded({ url, obj: o, error: null })
      })
      .catch((e: unknown) => alive && setLoaded({ url, obj: null, error: e instanceof Error ? e.message : String(e) }))
    return () => {
      alive = false
      current?.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          o.geometry.dispose()
          for (const m of [o.material, o.userData.forgeOriginalMaterial ?? []].flat()) (m as THREE.Material).dispose()
        }
      })
    }
  }, [url, ext])

  const obj = loaded?.url === url ? loaded.obj : null
  const error = loaded?.url === url ? loaded.error : null

  useEffect(() => (obj ? applyLook(obj, look, wireframe) : undefined), [obj, look, wireframe])

  const stats = useMemo(() => {
    if (!obj) return null
    obj.rotation.x = flipped ? Math.PI : 0
    return meshStats(obj)
  }, [obj, flipped])
  const cfg = project3d(project)
  const units = Z_UP.has(ext) || asset?.kind === "print" ? "mm" : (cfg.units ?? "m")
  const volume = units === "mm" ? cfg.printer?.build_volume_mm : undefined

  return (
    <div className={cn("relative h-full min-h-[320px] w-full overflow-hidden rounded-lg bg-muted/40", className)}>
      {error ? (
        <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">Não foi possível abrir o modelo: {error}</div>
      ) : (
        <Canvas shadows dpr={[1, 2]} camera={{ position: [2.5, 1.8, 3], fov: 40, near: 0.001, far: 1e6 }}>
          <ViewerLights look={look} stats={stats} />
          {obj && stats && (
            <>
              <primitive object={obj} />
              <FitCamera stats={stats} />
              {showGrid && <FloorGrid stats={stats} />}
              {showDims && <Dimensions stats={stats} units={units} />}
              {showVolume && volume && <PrintVolume size={volume} />}
            </>
          )}
          {!obj && (
            <Html center>
              <span className="text-xs text-muted-foreground">carregando…</span>
            </Html>
          )}
          <OrbitControls makeDefault autoRotate={autoRotate} autoRotateSpeed={1.5} />
        </Canvas>
      )}

      <div className="absolute top-2 right-2 flex gap-1">
        <Toggle on={wireframe} set={setWireframe} label="Wireframe"><Box /></Toggle>
        <Toggle on={showGrid} set={setShowGrid} label="Grade"><Grid3x3 /></Toggle>
        <Toggle on={showDims} set={setShowDims} label="Medidas"><Ruler /></Toggle>
        {volume && <Toggle on={showVolume} set={setShowVolume} label="Volume da impressora"><Printer /></Toggle>}
        <Toggle on={flipped} set={setFlipped} label="Virar (ver a face de baixo)"><FlipVertical2 /></Toggle>
        <Toggle on={autoRotate} set={setAutoRotate} label="Girar"><Rotate3d /></Toggle>
        <Toggle on={lookOpen} set={setLookOpen} label="Luz e cor da visualização"><Palette /></Toggle>
      </div>
      {lookOpen && <LookPanel look={look} set={setLook} />}

      {stats && (
        <div className="absolute bottom-2 left-2 rounded-md bg-background/80 px-2 py-1 font-mono text-[11px] leading-tight text-muted-foreground backdrop-blur">
          <div>{stats.triangles.toLocaleString()} tris · {stats.vertices.toLocaleString()} verts</div>
          <div>
            {formatLength(stats.size.x, units)} × {formatLength(stats.size.y, units)} × {formatLength(stats.size.z, units)}
          </div>
        </div>
      )}
    </div>
  )
}

function LookPanel({ look, set }: { look: Look; set: (patch: Partial<Look>) => void }) {
  const custom = look.color.startsWith("#") && !SWATCHES.some((s) => s.color === look.color)
  return (
    <div className="absolute top-12 right-2 w-60 space-y-3 rounded-lg border bg-background/95 p-3 text-xs shadow-lg backdrop-blur">
      <div className="space-y-1.5">
        <div className="font-medium">Cor de visualização</div>
        <div className="flex flex-wrap items-center gap-1.5">
          {SWATCHES.map((s) => (
            <button key={s.color} type="button" title={s.label} aria-label={s.label} aria-pressed={look.color === s.color}
              onClick={() => set({ color: s.color })}
              className={cn("size-6 rounded-full border text-[9px] font-semibold", look.color === s.color && "ring-2 ring-primary ring-offset-1 ring-offset-background")}
              style={swatchStyle(s.color)}>
              {s.color === "auto" ? "A" : s.color === "original" ? "O" : ""}
            </button>
          ))}
          <label title="Escolher cor" className={cn("relative size-6 cursor-pointer overflow-hidden rounded-full border", custom && "ring-2 ring-primary ring-offset-1 ring-offset-background")}
            style={{ background: custom ? look.color : "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)" }}>
            <input type="color" className="absolute inset-0 cursor-pointer opacity-0" value={custom ? look.color : "#a8a29a"}
              onChange={(e) => set({ color: e.target.value as Look["color"] })} />
          </label>
        </div>
      </div>
      <div className="space-y-1.5">
        <div className="font-medium">Luz</div>
        <div className="grid grid-cols-3 gap-1">
          {LIGHTS.map((l) => (
            <Button key={l.id} size="sm" variant={look.light === l.id ? "secondary" : "ghost"} className="h-7 text-xs" onClick={() => set({ light: l.id })}>
              {l.label}
            </Button>
          ))}
        </div>
        <label className="flex items-center justify-between pt-1">
          <span>Sombras</span>
          <Switch checked={look.shadows} disabled={look.light === "flat"} onCheckedChange={(v: boolean) => set({ shadows: v })} />
        </label>
      </div>
      <div className="space-y-1.5">
        <div className="flex justify-between font-medium"><span>Exposição</span><span className="font-mono text-muted-foreground">{look.exposure.toFixed(2)}</span></div>
        <Slider min={30} max={180} value={[Math.round(look.exposure * 100)]}
          onValueChange={(v) => set({ exposure: (Array.isArray(v) ? v[0] : (v as number)) / 100 })} />
      </div>
    </div>
  )
}

function swatchStyle(color: Look["color"]): CSSProperties {
  if (color === "auto") return { background: "linear-gradient(135deg, #d6a46a 50%, #a8a29a 50%)" }
  if (color === "original") return { background: "#ffffff", color: "#555" }
  if (color === "normals") return { background: "conic-gradient(#8080ff, #ff8080, #80ff80, #8080ff)" }
  return { background: color }
}

function Toggle({ on, set, label, children }: { on: boolean; set: (v: boolean) => void; label: string; children: ReactNode }) {
  return (
    <Button size="icon" variant={on ? "secondary" : "ghost"} className="size-8" title={label} aria-label={label} aria-pressed={on} onClick={() => set(!on)}>
      {children}
    </Button>
  )
}

/** Frame the model from a fixed three-quarter view, whatever its scale (mm or m). */
export function FitCamera({ stats }: { stats: MeshStats }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update: () => void } | null
  useEffect(() => {
    const center = stats.min.clone().addScaledVector(stats.size, 0.5)
    const radius = Math.max(stats.size.length() / 2, 1e-4)
    const dist = (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.1
    camera.position.copy(center).addScaledVector(new THREE.Vector3(0.9, 0.55, 1.2).normalize(), dist)
    camera.near = dist / 1000
    camera.far = dist * 1000
    camera.updateProjectionMatrix()
    camera.lookAt(center)
    if (controls) {
      controls.target.copy(center)
      controls.update()
    }
  }, [stats, camera, controls])
  return null
}

function niceStep(extent: number): number {
  const raw = Math.max(extent, 1e-6) / 10
  return 10 ** Math.floor(Math.log10(raw))
}

export function FloorGrid({ stats }: { stats: MeshStats }) {
  const extent = Math.max(stats.size.x, stats.size.z, stats.size.y)
  const cell = niceStep(extent)
  return (
    <Grid
      position={[0, stats.min.y - extent * 1e-4, 0]}
      cellSize={cell}
      sectionSize={cell * 10}
      infiniteGrid
      fadeDistance={extent * 8}
      fadeStrength={1.5}
      cellColor="#8a8f98"
      sectionColor="#5b6170"
      cellThickness={0.6}
      sectionThickness={1}
    />
  )
}

function Dimensions({ stats, units }: { stats: MeshStats; units: string }) {
  const box = useMemo(() => {
    const b = new THREE.Box3(stats.min.clone(), stats.min.clone().add(stats.size))
    return new THREE.Box3Helper(b, new THREE.Color("#f59e0b"))
  }, [stats])
  const { min, size } = stats
  const label = "rounded bg-background/85 px-1 font-mono text-[10px] whitespace-nowrap text-foreground shadow-sm"
  return (
    <>
      <primitive object={box} />
      <Html position={[min.x + size.x / 2, min.y, min.z + size.z]} center>
        <span className={label}>X {formatLength(size.x, units)}</span>
      </Html>
      <Html position={[min.x + size.x, min.y + size.y / 2, min.z + size.z]} center>
        <span className={label}>Y {formatLength(size.y, units)}</span>
      </Html>
      <Html position={[min.x + size.x, min.y, min.z + size.z / 2]} center>
        <span className={label}>Z {formatLength(size.z, units)}</span>
      </Html>
    </>
  )
}

/** Printer build volume in mm; the printed model sits on the bed centered at the origin (Y-up in the viewer). */
function PrintVolume({ size }: { size: [number, number, number] }) {
  const [bx, by, bz] = size
  const edges = useMemo(() => new THREE.EdgesGeometry(new THREE.BoxGeometry(bx, bz, by)), [bx, by, bz])
  return (
    <group position={[0, bz / 2, 0]}>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color="#38bdf8" transparent opacity={0.6} />
      </lineSegments>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -bz / 2 + 0.01, 0]}>
        <planeGeometry args={[bx, by]} />
        <meshBasicMaterial color="#38bdf8" transparent opacity={0.06} side={THREE.DoubleSide} />
      </mesh>
    </group>
  )
}

export default MeshViewer

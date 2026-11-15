import { TransformControls } from "@react-three/drei"
import { useEffect, useMemo, useState } from "react"
import * as THREE from "three"

import { loadMesh, extOf } from "../mesh"
import { MODE_COLOR, type CutTool, type MeshTool, type MirrorTool, type PlacedTool, type PrimitiveTool, type Quat, type TextTool, type Vec3 } from "./ops"

export type GizmoMode = "translate" | "rotate" | "scale"

export interface Placement {
  position: Vec3
  quaternion: Quat
  scale: Vec3
}

function readPlacement(o: THREE.Object3D): Placement {
  return {
    position: o.position.toArray() as Vec3,
    quaternion: o.quaternion.toArray() as Quat,
    scale: o.scale.toArray() as Vec3,
  }
}

/** A group positioned from tool state, with a gizmo when selected. */
function Placed({
  position, quaternion, scale = [1, 1, 1], selected, mode, allowScale = true, onSelect, onChange, children,
}: {
  position: Vec3
  quaternion: Quat
  scale?: Vec3
  selected: boolean
  mode: GizmoMode
  allowScale?: boolean
  onSelect: () => void
  onChange: (p: Placement) => void
  children: React.ReactNode
}) {
  const [obj, setObj] = useState<THREE.Group | null>(null)
  return (
    <>
      <group
        ref={setObj}
        position={position}
        quaternion={quaternion}
        scale={scale}
        onClick={(e) => {
          e.stopPropagation()
          onSelect()
        }}
      >
        {children}
      </group>
      {selected && obj && (
        <TransformControls object={obj} mode={!allowScale && mode === "scale" ? "translate" : mode} onObjectChange={() => onChange(readPlacement(obj))} />
      )}
    </>
  )
}

const ghost = (color: string, opacity = 0.42) => (
  <meshStandardMaterial color={color} transparent opacity={opacity} depthWrite={false} side={THREE.DoubleSide} />
)

export function CutPreview({ tool, size, ...p }: { tool: CutTool; size: number; selected: boolean; mode: GizmoMode; onSelect: () => void; onChange: (t: Partial<CutTool>) => void }) {
  const arrow = tool.keep === "front" ? 1 : -1
  return (
    <Placed position={tool.position} quaternion={tool.quaternion} allowScale={false} selected={p.selected} mode={p.mode} onSelect={p.onSelect}
      onChange={(pl) => p.onChange({ position: pl.position, quaternion: pl.quaternion })}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[size * 1.5, size * 1.5]} />
        {ghost("#38bdf8", 0.22)}
      </mesh>
      <lineSegments rotation={[-Math.PI / 2, 0, 0]}>
        <edgesGeometry args={[new THREE.PlaneGeometry(size * 1.5, size * 1.5)]} />
        <lineBasicMaterial color="#38bdf8" />
      </lineSegments>
      {/* arrow towards the side that is kept */}
      <arrowHelper args={[new THREE.Vector3(0, arrow, 0), new THREE.Vector3(), size * 0.35, 0x38bdf8, size * 0.08, size * 0.05]} />
    </Placed>
  )
}

function PrimitiveGeometry({ shape }: { shape: PrimitiveTool["shape"] }) {
  switch (shape) {
    case "box":
      return <boxGeometry args={[1, 1, 1]} />
    case "sphere":
      return <sphereGeometry args={[0.5, 48, 24]} />
    case "cylinder":
      return <cylinderGeometry args={[0.5, 0.5, 1, 48]} />
    case "cone":
      return <cylinderGeometry args={[0, 0.5, 1, 48]} />
  }
}

export function PrimitivePreview({ tool, ...p }: { tool: PrimitiveTool; selected: boolean; mode: GizmoMode; onSelect: () => void; onChange: (t: Partial<PrimitiveTool>) => void }) {
  return (
    <Placed position={tool.position} quaternion={tool.quaternion} scale={tool.scale} selected={p.selected} mode={p.mode} onSelect={p.onSelect}
      onChange={(pl) => p.onChange({ position: pl.position, quaternion: pl.quaternion, scale: pl.scale })}>
      <mesh>
        <PrimitiveGeometry shape={tool.shape} />
        {ghost(MODE_COLOR[tool.mode])}
      </mesh>
    </Placed>
  )
}

/** Text drawn on a canvas texture (system font, no font files to load), sized like the server's extrusion. */
function useTextTexture(text: string) {
  return useMemo(() => {
    const px = 128
    const lines = text.split("\n")
    const canvas = document.createElement("canvas")
    const ctx = canvas.getContext("2d")!
    ctx.font = `bold ${px}px Arial`
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width), 1)
    canvas.width = Math.ceil(w + px * 0.2)
    canvas.height = Math.ceil(px * 1.2 * lines.length)
    ctx.font = `bold ${px}px Arial`
    ctx.fillStyle = "#ffffff"
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"
    lines.forEach((l, i) => ctx.fillText(l, canvas.width / 2, px * 0.6 + i * px * 1.2))
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    return { tex, aspect: canvas.width / canvas.height }
  }, [text])
}

export function TextPreview({ tool, ...p }: { tool: TextTool; selected: boolean; mode: GizmoMode; onSelect: () => void; onChange: (t: Partial<TextTool>) => void }) {
  const { tex, aspect } = useTextTexture(tool.text || " ")
  const h = tool.height * 1.2
  const w = h * aspect
  return (
    <Placed position={tool.position} quaternion={tool.quaternion} allowScale={false} selected={p.selected} mode={p.mode} onSelect={p.onSelect}
      onChange={(pl) => p.onChange({ position: pl.position, quaternion: pl.quaternion })}>
      <mesh position={[0, 0, tool.depth]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={tex} color={MODE_COLOR[tool.mode]} transparent depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[0, 0, tool.depth / 2]}>
        <boxGeometry args={[w, h, tool.depth]} />
        {ghost(MODE_COLOR[tool.mode], 0.12)}
      </mesh>
    </Placed>
  )
}

export function MeshPartPreview({ tool, ...p }: { tool: MeshTool; selected: boolean; mode: GizmoMode; onSelect: () => void; onChange: (t: Partial<MeshTool>) => void }) {
  const [obj, setObj] = useState<THREE.Object3D | null>(null)
  useEffect(() => {
    let alive = true
    loadMesh(tool.url, extOf(tool.url)).then((o) => {
      o.traverse((m) => {
        if (m instanceof THREE.Mesh) m.material = new THREE.MeshStandardMaterial({ color: MODE_COLOR[tool.mode], transparent: true, opacity: 0.7 })
      })
      if (alive) setObj(o)
    })
    return () => {
      alive = false
    }
  }, [tool.url, tool.mode])
  return (
    <Placed position={tool.position} quaternion={tool.quaternion} scale={tool.scale} selected={p.selected} mode={p.mode} onSelect={p.onSelect}
      onChange={(pl) => p.onChange({ position: pl.position, quaternion: pl.quaternion, scale: pl.scale })}>
      {obj && <primitive object={obj} />}
    </Placed>
  )
}

export function MirrorPreview({ tool, model, size }: { tool: MirrorTool; model: THREE.Object3D; size: number }) {
  const axis = "xyz".indexOf(tool.axis)
  // a deep copy with its own materials so the mirrored side can be clipped independently
  const clone = useMemo(() => {
    const c = model.clone(true)
    c.traverse((o) => {
      if (o instanceof THREE.Mesh) o.material = [o.material].flat().map((m: THREE.Material) => m.clone())
    })
    return c
  }, [model])
  const reflect = useMemo(() => {
    const m = new THREE.Matrix4()
    const s = [1, 1, 1]
    s[axis] = -1
    m.makeScale(s[0], s[1], s[2])
    const t = new THREE.Vector3()
    t.setComponent(axis, 2 * tool.origin)
    m.setPosition(t)
    return m
  }, [axis, tool.origin])
  useEffect(() => {
    const keepPositive = tool.mode === "symmetrize_positive"
    const n = new THREE.Vector3()
    n.setComponent(axis, keepPositive ? -1 : 1) // the mirrored copy shows the other side
    const plane = new THREE.Plane(n, keepPositive ? tool.origin : -tool.origin)
    clone.traverse((o) => {
      if (o instanceof THREE.Mesh) for (const m of [o.material].flat() as THREE.Material[]) {
        m.clippingPlanes = tool.mode === "mirror" ? [] : [plane]
        m.side = THREE.DoubleSide
      }
    })
  }, [clone, tool.mode, tool.origin, axis])
  const planeRot: [number, number, number] = axis === 0 ? [0, Math.PI / 2, 0] : axis === 1 ? [Math.PI / 2, 0, 0] : [0, 0, 0]
  const planePos: Vec3 = [0, 0, 0]
  planePos[axis] = tool.origin
  return (
    <>
      <group matrixAutoUpdate={false} matrix={reflect}>
        <primitive object={clone} />
      </group>
      <mesh position={planePos} rotation={planeRot}>
        <planeGeometry args={[size * 1.5, size * 1.5]} />
        {ghost("#a855f7", 0.15)}
      </mesh>
    </>
  )
}

export function ToolPreview(props: { tool: PlacedTool; size: number; selected: boolean; mode: GizmoMode; onSelect: () => void; onChange: (t: Partial<PlacedTool>) => void }) {
  const { tool, ...rest } = props
  switch (tool.kind) {
    case "cut":
      return <CutPreview tool={tool} {...rest} />
    case "primitive":
      return <PrimitivePreview tool={tool} {...rest} />
    case "text":
      return <TextPreview tool={tool} {...rest} />
    case "mesh":
      return <MeshPartPreview tool={tool} {...rest} />
  }
}

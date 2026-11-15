import { Box } from "lucide-react"
import { useEffect, useState } from "react"
import * as THREE from "three"

import type { ThumbnailProps } from "@/core/registry"

import { extOf, loadMesh } from "./mesh"
import { studioEnvironment } from "./environment"

// One shared offscreen renderer for every gallery card: a <Canvas> per card would exhaust
// the browser's WebGL context limit (~16). Renders are serialized and cached by URL.
const SIZE = 256
const cache = new Map<string, Promise<string>>()
let renderer: THREE.WebGLRenderer | null = null
let queue: Promise<unknown> = Promise.resolve()

function getRenderer(): THREE.WebGLRenderer {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(1)
    renderer.setSize(SIZE, SIZE)
    renderer.outputColorSpace = THREE.SRGBColorSpace
  }
  return renderer
}

async function renderThumb(url: string, ext: string): Promise<string> {
  const obj = await loadMesh(url, ext)
  const scene = new THREE.Scene()
  scene.environment = studioEnvironment(getRenderer())
  scene.environmentIntensity = 0.55
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444455, 1.1))
  const key = new THREE.DirectionalLight(0xffffff, 1.8)
  key.position.set(3, 5, 4)
  scene.add(key)
  scene.add(obj)

  const box = new THREE.Box3().setFromObject(obj)
  const center = box.getCenter(new THREE.Vector3())
  const radius = box.getBoundingSphere(new THREE.Sphere()).radius || 1
  const camera = new THREE.PerspectiveCamera(35, 1, radius / 100, radius * 100)
  const dist = radius / Math.sin(THREE.MathUtils.degToRad(35 / 2)) * 1.05
  camera.position.copy(center).add(new THREE.Vector3(0.9, 0.55, 1.2).normalize().multiplyScalar(dist))
  camera.lookAt(center)

  const r = getRenderer()
  r.setClearColor(0x000000, 0)
  r.render(scene, camera)
  const data = r.domElement.toDataURL("image/png")
  obj.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.geometry.dispose()
      for (const m of [o.material].flat()) (m as THREE.Material).dispose()
    }
  })
  return data
}

function thumbnail(url: string): Promise<string> {
  let p = cache.get(url)
  if (!p) {
    const ext = extOf(url)
    p = queue.then(() => renderThumb(url, ext))
    queue = p.catch(() => undefined)
    cache.set(url, p)
    p.catch(() => cache.delete(url))
  }
  return p
}

export function MeshThumb({ url, asset }: ThumbnailProps) {
  // result is tagged with its url so a stale render never shows for a new url
  const [result, setResult] = useState<{ url: string; src: string | null }>()

  useEffect(() => {
    let alive = true
    thumbnail(url)
      .then((src) => alive && setResult({ url, src }))
      .catch(() => alive && setResult({ url, src: null }))
    return () => {
      alive = false
    }
  }, [url])

  const current = result?.url === url ? result : undefined
  const src = current?.src
  const failed = current !== undefined && current.src === null

  if (src) return <img src={src} alt={asset.name} className="max-h-full max-w-full object-contain" />
  return <Box className={failed ? "size-8 text-muted-foreground" : "size-8 animate-pulse text-muted-foreground"} />
}

export default MeshThumb

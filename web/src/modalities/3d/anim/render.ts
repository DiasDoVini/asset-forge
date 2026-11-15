import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"

export interface RenderOptions {
  clip: string
  fps: number
  size: number // square frame, px
  transparent: boolean
  background: string
  onProgress?: (done: number, total: number) => void
}

/**
 * Render a clip of an animated GLB frame by frame into a sprite sheet (PNG blob).
 * The server turns the sheet into GIF / MP4 / WebM (3d.animation_media exporter).
 */
export async function renderSheet(url: string, o: RenderOptions): Promise<{ sheet: Blob; frames: number; columns: number }> {
  const gltf = await new GLTFLoader().loadAsync(url)
  const clip = gltf.animations.find((a) => a.name === o.clip)
  if (!clip) throw new Error(`clip ${o.clip} não encontrado no GLB`)
  const scene = new THREE.Scene()
  scene.add(new THREE.AmbientLight(0xffffff, 0.5))
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8888a0, 1.1))
  const key = new THREE.DirectionalLight(0xffffff, 1.6)
  key.position.set(5, 10, 7)
  scene.add(key)
  scene.add(gltf.scene)
  const mixer = new THREE.AnimationMixer(gltf.scene)
  mixer.clipAction(clip).play()

  const frames = Math.max(1, Math.round(clip.duration * o.fps))
  // frame the whole motion: union of the bounds over the clip
  const box = new THREE.Box3()
  for (let i = 0; i <= 8; i++) {
    mixer.setTime((clip.duration * i) / 8)
    gltf.scene.updateMatrixWorld(true)
    box.union(new THREE.Box3().setFromObject(gltf.scene))
  }
  const center = box.getCenter(new THREE.Vector3())
  const radius = box.getBoundingSphere(new THREE.Sphere()).radius || 1
  const camera = new THREE.PerspectiveCamera(35, 1, radius / 100, radius * 100)
  const dist = (radius / Math.sin(THREE.MathUtils.degToRad(35 / 2))) * 0.8 // bounding sphere is loose; frame tighter
  camera.position.copy(center).add(new THREE.Vector3(0.9, 0.55, 1.2).normalize().multiplyScalar(dist))
  camera.lookAt(center)

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setPixelRatio(1)
  renderer.setSize(o.size, o.size)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setClearColor(new THREE.Color(o.background), o.transparent ? 0 : 1)

  const columns = Math.ceil(Math.sqrt(frames))
  const rows = Math.ceil(frames / columns)
  const sheet = document.createElement("canvas")
  sheet.width = columns * o.size
  sheet.height = rows * o.size
  const ctx = sheet.getContext("2d")!
  try {
    for (let i = 0; i < frames; i++) {
      mixer.setTime((clip.duration * i) / frames)
      renderer.render(scene, camera)
      ctx.drawImage(renderer.domElement, (i % columns) * o.size, Math.floor(i / columns) * o.size)
      o.onProgress?.(i + 1, frames)
      if (i % 8 === 7) await new Promise((r) => setTimeout(r)) // keep the UI responsive
    }
  } finally {
    renderer.dispose()
  }
  const blob = await new Promise<Blob>((res, rej) => sheet.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), "image/png"))
  return { sheet: blob, frames, columns }
}

// Soft studio reflections (three's RoomEnvironment through PMREM) as scene.environment: PBR materials need
// something to reflect - without it metallic parts render near-black and roughness has no visible effect.
import { useThree } from "@react-three/fiber"
import { useEffect } from "react"
import * as THREE from "three"
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js"

const cache = new WeakMap<THREE.WebGLRenderer, THREE.Texture>()

/** The environment texture for a renderer (built once per renderer). */
export function studioEnvironment(gl: THREE.WebGLRenderer): THREE.Texture {
  let tex = cache.get(gl)
  if (!tex) {
    const pmrem = new THREE.PMREMGenerator(gl)
    tex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    pmrem.dispose()
    cache.set(gl, tex)
  }
  return tex
}

/** Drop inside a <Canvas>: lights the scene's PBR materials with the studio environment (not shown as background). */
export function StudioEnvironment({ intensity = 0.55 }: { intensity?: number }) {
  const gl = useThree((s) => s.gl)
  const scene = useThree((s) => s.scene)
  useEffect(() => {
    scene.environment = studioEnvironment(gl)
    scene.environmentIntensity = intensity
    return () => {
      scene.environment = null
    }
  }, [gl, scene, intensity])
  return null
}

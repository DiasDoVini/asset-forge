import { OrbitControls } from "@react-three/drei"
import { Canvas, useFrame } from "@react-three/fiber"
import { Pause, Play } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import * as THREE from "three"
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js"

import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import type { ViewerProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { FitCamera, FloorGrid } from "../MeshViewer"
import { meshStats } from "../mesh"
import { StudioEnvironment } from "../environment"

export function useGltf(url: string | undefined) {
  const [state, setState] = useState<{ url: string; gltf: GLTF | null; error: string | null }>()
  useEffect(() => {
    if (!url) return
    let alive = true
    new GLTFLoader()
      .loadAsync(url)
      .then((g) => alive && setState({ url, gltf: g, error: null }))
      .catch((e: unknown) => alive && setState({ url, gltf: null, error: String(e) }))
    return () => {
      alive = false
    }
  }, [url])
  return state?.url === url ? state : undefined
}

/** Advances (or pins) an AnimationMixer from inside the canvas. */
export function MixerDriver({ mixer, playing, time, duration, loop, onTime }: {
  mixer: THREE.AnimationMixer; playing: boolean; time: number; duration: number; loop: boolean; onTime: (t: number) => void
}) {
  useEffect(() => {
    if (!playing) mixer.setTime(time)
  }, [mixer, playing, time])
  useFrame((_, dt) => {
    if (!playing) return
    let t = time + dt
    if (t > duration) t = loop ? t % duration : duration
    mixer.setTime(t)
    onTime(t)
  })
  return null
}

export function AnimationViewer({ url, className }: ViewerProps & { className?: string }) {
  const loaded = useGltf(url)
  const gltf = loaded?.gltf ?? null
  const [clipName, setClipName] = useState<string>()
  const [playing, setPlaying] = useState(true)
  const [time, setTime] = useState(0)
  const mixer = useMemo(() => (gltf ? new THREE.AnimationMixer(gltf.scene) : null), [gltf])
  const clip = gltf?.animations.find((a) => a.name === clipName) ?? gltf?.animations[0]
  const stats = useMemo(() => (gltf ? meshStats(gltf.scene) : null), [gltf])

  useEffect(() => {
    if (!mixer || !clip) return
    mixer.stopAllAction()
    mixer.clipAction(clip).play()
    mixer.setTime(0)
  }, [mixer, clip])

  return (
    <div className={cn("relative flex h-full min-h-[320px] w-full flex-col overflow-hidden rounded-lg bg-muted/40", className)}>
      <div className="relative min-h-0 flex-1">
        {loaded?.error ? (
          <p className="p-6 text-sm text-destructive">Não foi possível abrir a animação: {loaded.error}</p>
        ) : (
          <Canvas dpr={[1, 2]} camera={{ fov: 40, near: 0.001, far: 1e6 }}>
            <ambientLight intensity={0.45} />
            <StudioEnvironment />
            <hemisphereLight args={[0xffffff, 0x8888a0, 1.1]} />
            <directionalLight position={[5, 10, 7]} intensity={1.6} />
            {gltf && stats && (
              <>
                <primitive object={gltf.scene} />
                <FitCamera stats={stats} />
                <FloorGrid stats={stats} />
              </>
            )}
            {mixer && clip && <MixerDriver mixer={mixer} playing={playing} time={time} duration={clip.duration} loop onTime={setTime} />}
            <OrbitControls makeDefault />
          </Canvas>
        )}
      </div>
      {gltf && (
        <div className="flex items-center gap-2 border-t bg-background/80 px-2 py-1.5 text-xs">
          <Button size="icon-sm" variant="ghost" aria-label={playing ? "Pausar" : "Tocar"} onClick={() => setPlaying(!playing)} disabled={!clip}>
            {playing ? <Pause /> : <Play />}
          </Button>
          {gltf.animations.length ? (
            <>
              <select className="h-7 rounded-md border bg-background px-1" value={clip?.name} onChange={(e) => { setClipName(e.target.value); setTime(0) }}>
                {gltf.animations.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
              </select>
              <Slider className="flex-1" min={0} max={Math.round((clip?.duration ?? 1) * 100)} value={[Math.round(time * 100)]}
                onValueChange={(v) => { setPlaying(false); setTime((Array.isArray(v) ? v[0] : (v as number)) / 100) }} />
              <span className="w-20 text-right font-mono">{time.toFixed(2)} / {clip?.duration.toFixed(2)}s</span>
            </>
          ) : (
            <span className="text-muted-foreground">Sem clipes ainda: crie um na aba Animar.</span>
          )}
        </div>
      )}
    </div>
  )
}

export default AnimationViewer

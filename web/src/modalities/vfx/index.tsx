import { Sparkles } from 'lucide-react'
import { registerModality } from '@/core/registry'
import { ParticleEditor, ParticleViewer } from './ParticleView'
import { ShaderEditor } from './ShaderEditor'
import { ShaderViewer } from './ShaderView'

registerModality({
  name: 'vfx',
  title: 'VFX',
  icon: Sparkles,
  viewers: [
    { id: 'shader', match: (f) => f.role === 'primary' && f.path === 'shader.json', component: ShaderViewer, priority: 20 },
    { id: 'particles', match: (f) => f.role === 'primary' && f.path === 'particles.json', component: ParticleViewer, priority: 20 },
  ],
  panels: [
    { id: 'shader-code', title: 'Código', placement: 'tab', component: ShaderEditor, when: (a) => a.modality === 'vfx' && a.kind === 'shader' },
    { id: 'particles-json', title: 'JSON', placement: 'tab', component: ParticleEditor, when: (a) => a.modality === 'vfx' && a.kind === 'particles' },
  ],
})

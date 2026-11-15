import { Image as ImageIcon } from 'lucide-react'
import { registerModality, type PanelProps } from '@/core/registry'
import { ImageEditor } from './ImageEditor'
import { AnimationsPanel, AnimationViewer, ViewsPanel } from './animation'
import { CheckpointField, LorasField } from './ModelFields'
import { SliceEditor } from './slice/SliceEditor'
import { SpriteEditor } from './sprite/SpriteEditor'

function PalettePanel({ asset }: PanelProps) {
  const px = (asset.extra?.pixel ?? {}) as { palette?: string[]; art_size?: number[]; colors_used?: number; grid_cell?: number | null }
  if (!px.art_size) return <p className="text-xs text-muted-foreground">Não é um sprite pixelizado.</p>
  return (
    <div className="space-y-2 text-xs">
      <p>
        {px.art_size[0]}×{px.art_size[1]} px de arte · {px.colors_used} cores
        {px.grid_cell ? ` · grade ${px.grid_cell}px` : ''}
      </p>
      {px.palette && (
        <div className="flex flex-wrap gap-1">
          {px.palette.map((c) => (
            <button key={c} className="size-5 rounded-sm border" style={{ background: c }} title={c} onClick={() => navigator.clipboard.writeText(c)} />
          ))}
        </div>
      )}
    </div>
  )
}

const isImage = (a: PanelProps['asset']) => a.modality === '2d' && (a.kind === 'image' || a.kind === 'sprite')

registerModality({
  name: '2d',
  title: '2D',
  icon: ImageIcon,
  viewers: [{ id: 'animation', match: (f) => f.path === 'animation.json', component: AnimationViewer, priority: 10 }],
  fields: {
    checkpoint: (ctx) => <CheckpointField {...ctx} />,
    loras: (ctx) => <LorasField {...ctx} />,
  },
  panels: [
    { id: 'edit', title: 'Editar', placement: 'tab', component: ImageEditor, when: (a) => isImage(a) && a.files.some((f) => f.role === 'primary' && f.mime.startsWith('image/')) },
    { id: 'sprite', title: 'Sprite', placement: 'tab', component: SpriteEditor, when: (a) => a.modality === '2d' && ['image', 'sprite', 'animation'].includes(a.kind) },
    { id: 'slice', title: 'Recortar', placement: 'tab', component: SliceEditor, when: (a) => isImage(a) && a.files.some((f) => f.role === 'primary' && f.mime.startsWith('image/')) },
    { id: 'animations', title: 'Animações', placement: 'inspector', component: AnimationsPanel, when: isImage },
    { id: 'views', title: 'Direções', placement: 'inspector', component: ViewsPanel, when: isImage },
    { id: 'palette', title: 'Pixel art', placement: 'inspector', component: PalettePanel, when: (a) => a.kind === 'sprite' },
  ],
})

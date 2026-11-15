// Modalidade "pixel" (Pixel Studio): sprites em canvas exato, editados por mapa de caracteres + paleta do projeto.
import { Link } from 'react-router'
import { Grid2x2, Grid2x2Plus } from 'lucide-react'
import { registerModality } from '@/core/registry'
import { Button } from '@/components/ui/button'
import { NewPixelPage } from './NewPixelPage'
import { PixelEditorTab, ReportPanel } from './PixelEditorTab'
import { PixelSpriteViewer } from './PixelSpriteViewer'

export { PixelEditor } from './PixelEditor'

registerModality({
  name: 'pixel',
  title: 'Pixel art',
  icon: Grid2x2,
  viewers: [{ id: 'pixel-sprite', match: (f) => f.role === 'primary' && f.path === 'sprite.png', component: PixelSpriteViewer, priority: 20 }],
  panels: [
    { id: 'pixel-edit', title: 'Editar pixels', placement: 'tab', component: PixelEditorTab, when: (a) => a.modality === 'pixel' },
    { id: 'pixel-report', title: 'Validadores', placement: 'inspector', component: ReportPanel, when: (a) => a.modality === 'pixel' },
  ],
  routes: [{ path: '/p/:project/pixel/new', element: <NewPixelPage /> }],
})

/** Botão que abre o Pixel Studio para criar um asset novo (opcionalmente já numa pasta/spec). */
export function NewPixelButton({ project, folder, spec, className, size = 'sm', variant = 'outline' }: {
  project: string
  folder?: string
  spec?: string
  className?: string
  size?: 'sm' | 'default' | 'xs'
  variant?: 'outline' | 'default' | 'secondary' | 'ghost'
}) {
  const qs = new URLSearchParams()
  if (folder) qs.set('folder', folder)
  if (spec) qs.set('spec', spec)
  const s = qs.toString()
  return (
    <Button size={size} variant={variant} className={className} nativeButton={false} render={<Link to={`/p/${encodeURIComponent(project)}/pixel/new${s ? `?${s}` : ''}`} />}>
      <Grid2x2Plus /> Nova pixel art
    </Button>
  )
}

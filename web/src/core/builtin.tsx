// Viewers genéricos do core (prioridade baixa; modalidades podem sobrepor).
import { registerModality } from './registry'
import { ImageViewer } from './components/ImageViewer'

registerModality({
  name: 'core',
  viewers: [{ id: 'image', match: (f) => f.mime.startsWith('image/'), component: ImageViewer, priority: -10 }],
})

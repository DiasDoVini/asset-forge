import { Clapperboard, PackageOpen } from 'lucide-react'
import { registerModality, type PanelProps, type ThumbnailProps } from '@/core/registry'
import { ExportDialog } from '@/core/components/ExportDialog'
import { Button } from '@/components/ui/button'
import { VideoViewer } from './VideoViewer'

function VideoInfoPanel({ asset, project }: PanelProps) {
  const i = (asset.extra?.video ?? {}) as { width?: number; height?: number; fps?: number; duration?: number; frames?: number; has_audio?: boolean }
  const ex = asset.extra ?? {}
  return (
    <div className="space-y-3 text-xs">
      {i.width ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          <span className="text-muted-foreground">Resolução</span><span>{i.width}×{i.height}</span>
          <span className="text-muted-foreground">Duração</span><span>{i.duration?.toFixed(2)} s · {i.frames} quadros</span>
          <span className="text-muted-foreground">FPS</span><span>{i.fps}</span>
          <span className="text-muted-foreground">Áudio</span><span>{i.has_audio ? 'sim' : 'não'}</span>
          {ex.engine ? <><span className="text-muted-foreground">Motor</span><span>{String(ex.engine)}</span></> : null}
        </div>
      ) : (
        <p className="text-muted-foreground">Vídeo importado: qualquer edição calcula os metadados.</p>
      )}
      <ExportDialog asset={asset} project={project} initial="video.file" trigger={<Button size="sm"><PackageOpen /> Exportar</Button>} />
    </div>
  )
}

function VideoThumb({ url }: ThumbnailProps) {
  return (
    <video
      src={url}
      muted
      loop
      playsInline
      preload="metadata"
      className="max-h-full max-w-full"
      onMouseEnter={(e) => void e.currentTarget.play()}
      onMouseLeave={(e) => e.currentTarget.pause()}
    />
  )
}

registerModality({
  name: 'video',
  title: 'Vídeo',
  icon: Clapperboard,
  viewers: [{ id: 'video', match: (f) => f.role === 'primary' && f.mime.startsWith('video/'), component: VideoViewer, priority: 10 }],
  thumbnail: VideoThumb,
  panels: [{ id: 'video-info', title: 'Vídeo', placement: 'inspector', component: VideoInfoPanel }],
})

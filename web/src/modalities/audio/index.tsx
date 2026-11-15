import { useEffect, useRef, useState } from 'react'
import { AudioLines, PackageOpen, Pause, Play, Shuffle } from 'lucide-react'
import { registerModality, type PanelProps, type ThumbnailProps } from '@/core/registry'
import { ExportDialog } from '@/core/components/ExportDialog'
import { useJobNav } from '@/core/useJobNav'
import { Button } from '@/components/ui/button'
import { AudioViewer, audioContext, decode } from './AudioViewer'

type Stats = { duration: number; sample_rate: number; channels: number; peak_db: number; rms_db: number; lufs: number; clipped: number }

function AudioInfoPanel({ asset, project }: PanelProps) {
  const st = asset.extra?.audio as Stats | undefined
  const spec = asset.files.find((f) => f.path === 'spectrogram.png')
  const { submit, busy } = useJobNav(project, 'Gerando variações…')
  const ex = asset.extra ?? {}
  const rows: [string, unknown][] = [
    ['Tipo', ex.sound_type ?? asset.kind],
    ['BPM', ex.bpm],
    ['Tom', ex.key],
    ['Loop', ex.loop ? 'sim' : null],
    ['Preset', ex.preset],
    ['Modelo', ex.model],
  ]
  return (
    <div className="space-y-3 text-xs">
      {st ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          <span className="text-muted-foreground">Duração</span><span>{st.duration.toFixed(2)} s</span>
          <span className="text-muted-foreground">Loudness</span><span>{st.lufs} LUFS</span>
          <span className="text-muted-foreground">Pico</span><span className={st.peak_db > -0.1 ? 'text-amber-400' : ''}>{st.peak_db} dBFS</span>
          <span className="text-muted-foreground">Formato</span><span>{st.sample_rate} Hz · {st.channels === 1 ? 'mono' : 'estéreo'}</span>
          {rows.filter(([, v]) => v != null && v !== '').map(([k, v]) => [
            <span key={k} className="text-muted-foreground">{k}</span>,
            <span key={k + 'v'} className="truncate">{String(v)}</span>,
          ])}
        </div>
      ) : (
        <p className="text-muted-foreground">Sem estatísticas (arquivo importado). Qualquer edição as calcula.</p>
      )}
      {spec && <img src={spec.url} alt="espectrograma" className="w-full rounded" title="espectrograma (agudos em cima)" />}
      <div className="flex flex-wrap gap-2">
        <ExportDialog asset={asset} project={project} initial="audio.file" trigger={<Button size="sm"><PackageOpen /> Exportar</Button>} />
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          title="3 variações parecidas (áudio → áudio)"
          onClick={() => submit('audio.variation', { input_asset: `${asset.slug}@v${String(asset.version).padStart(3, '0')}`, strength: 0.35, variations: 3 })}
        >
          <Shuffle /> Variações
        </Button>
      </div>
    </div>
  )
}

/** Thumbnail para áudios sem waveform.png (importados): ícone + play no hover. */
function AudioThumb({ url }: ThumbnailProps) {
  const [playing, setPlaying] = useState(false)
  const src = useRef<AudioBufferSourceNode | null>(null)
  useEffect(() => () => src.current?.stop(), [])
  const toggle = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (playing) {
      src.current?.stop()
      return
    }
    const buf = await decode(url)
    const ctx = audioContext()
    const s = ctx.createBufferSource()
    s.buffer = buf
    s.connect(ctx.destination)
    s.onended = () => setPlaying(false)
    s.start()
    src.current = s
    setPlaying(true)
  }
  return (
    <button onClick={toggle} className="group flex size-full flex-col items-center justify-center gap-1 text-muted-foreground hover:text-foreground">
      <AudioLines className="size-10" />
      {playing ? <Pause className="size-4" /> : <Play className="size-4 opacity-0 group-hover:opacity-100" />}
    </button>
  )
}

registerModality({
  name: 'audio',
  title: 'Áudio',
  icon: AudioLines,
  viewers: [{ id: 'audio', match: (f) => f.role === 'primary' && f.mime.startsWith('audio/'), component: AudioViewer, priority: 10 }],
  thumbnail: AudioThumb,
  panels: [{ id: 'audio-info', title: 'Áudio', placement: 'inspector', component: AudioInfoPanel }],
})

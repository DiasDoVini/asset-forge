// Importação de arquivos externos com opções por formato. Cada arquivo mostra o importador que o servidor vai usar
// (pela extensão, de /api/import/formats) e, se ele tiver opções, o formulário delas. Imagens podem ser importadas
// como estão ou como folha de sprites (vira animação; recortes avulsos ficam na aba Recortar).
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { FileUp, Loader2, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type ImporterInfo, type Project } from '../api'
import { SchemaForm, type Values } from '../SchemaForm'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

const IMAGE = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']
const LAYERED = ['.pxo', '.psd', '.kra'] // lidos no navegador, viram .aseprite com as camadas
const ext = (name: string) => (name.includes('.') ? '.' + name.split('.').pop()!.toLowerCase() : '')

const RAW_LABEL: Record<string, string> = {
  '.png': 'Imagem', '.jpg': 'Imagem', '.jpeg': 'Imagem', '.webp': 'Imagem', '.gdshader': 'Shader Godot (arquivo)', '.fbx': 'Arquivo (sem conversão)',
}

type Entry = { file: File; importer: string; values: Values }

/** Importadores que podem tratar o arquivo: o primeiro é o que o servidor usa sem opções. */
function candidates(formats: ImporterInfo[], name: string): ImporterInfo[] {
  const e = ext(name)
  return formats.filter((f) => f.exts.includes(e))
}

function defaultImporter(list: ImporterInfo[], name: string): string {
  const e = ext(name)
  // folha de sprites só com opções; arquivos .png/.gif estáticos caem na cópia/conversão
  const auto = list.filter((f) => f.id !== '2d.spritesheet')
  if (['.png', '.jpg', '.jpeg', '.webp'].includes(e)) return 'auto'
  if (LAYERED.includes(e)) return 'layers'
  return auto[0]?.id ?? 'auto'
}

export function ImportDialog({ project, folder, files, onClose, initialImporter }: {
  project: Project
  folder?: string
  files: File[]
  onClose: () => void
  /** importador pré-escolhido para todos (ex.: 'slice-editor' vindo do botão Recortar folha) */
  initialImporter?: string
}) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const formats = useQuery({ queryKey: ['import-formats'], queryFn: api.importFormats, staleTime: 60_000 })
  const [entries, setEntries] = useState<Entry[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const list: Entry[] = useMemo(() => {
    if (entries) return entries
    if (!formats.data) return []
    return files.map((file) => ({
      file,
      importer: initialImporter && (initialImporter !== 'slice-editor' || IMAGE.includes(ext(file.name))) ? initialImporter : defaultImporter(candidates(formats.data!, file.name), file.name),
      values: {},
    }))
  }, [entries, files, formats.data, initialImporter])
  const update = (i: number, patch: Partial<Entry>) => setEntries(list.map((e, k) => (k === i ? { ...e, ...patch } : e)))
  const remove = (i: number) => {
    const next = list.filter((_, k) => k !== i)
    if (!next.length) onClose()
    setEntries(next)
  }

  const run = async () => {
    const failed: Entry[] = []
    let openEditor: string | null = null
    for (const [i, e] of list.entries()) {
      setBusy(`${i + 1}/${list.length}: ${e.file.name}`)
      let options: Record<string, unknown> = { ...e.values }
      if (e.importer === '2d.spritesheet' && !['mode', 'columns', 'rows', 'cell_width', 'cell_height', 'rects', 'descriptor'].some((k) => k in options))
        options = { ...options, mode: 'auto' }
      if (e.importer === 'auto' || e.importer === 'slice-editor' || e.importer === 'layers') options = {}
      try {
        let file = e.file
        if (e.importer === 'layers') {
          const { layeredUpload } = await import('@/modalities/2d/sprite/formats/layered')
          const r = await layeredUpload(file)
          if (r.note) toast.warning(r.note)
          file = r.file
        }
        const av = await api.uploadAsset(project.slug, file, { folder: folder || undefined, options })
        if (e.importer === 'slice-editor') openEditor = `/p/${project.slug}/a/${av.slug}/${av.version}?tab=slice`
        const how = (av.extra?.import as { importer?: string } | undefined)?.importer
        toast.success(`${e.file.name} → ${av.slug}`, { description: how ? `${how} · ${av.kind}` : `${av.modality}/${av.kind}` })
      } catch (err) {
        toast.error(`${e.file.name}: ${String(err)}`)
        failed.push(e)
      }
    }
    setBusy(null)
    qc.invalidateQueries({ queryKey: ['assets', project.slug] })
    qc.invalidateQueries({ queryKey: ['folders', project.slug] })
    if (!failed.length) onClose()
    if (openEditor && !failed.length) nav(openEditor)
    else setEntries(failed) // só os que falharam ficam para tentar de novo
  }

  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Importar {list.length} arquivo(s){folder ? ` em ${folder}` : ''}</DialogTitle>
          <DialogDescription>
            Formatos com conversão: Aseprite, Pixelorama, Krita e PSD em camadas, GIF/APNG/WebP animados, folhas de sprites, BMP/TIFF/TGA/PSD, malhas 3D, áudio, vídeo,
            shaders GLSL e partículas JSON. O resto entra como veio.
          </DialogDescription>
        </DialogHeader>
        {formats.isLoading && <p className="text-sm text-muted-foreground">Carregando formatos…</p>}
        <div className="grid gap-3">
          {list.map((e, i) => {
            const cands = candidates(formats.data ?? [], e.file.name)
            const current = cands.find((c) => c.id === e.importer)
            const choices = [
              ...(LAYERED.includes(ext(e.file.name)) ? [{ id: 'layers', title: 'Em camadas (editável no editor de sprite)', description: 'Lido no navegador: camadas, grupos, opacidade, modos de mistura e (no .pxo/.kra) quadros e tags viram um .aseprite; a imagem/animação é gerada dele.' }] : []),
              ...(IMAGE.includes(ext(e.file.name)) || !cands.length ? [{ id: 'auto', title: RAW_LABEL[ext(e.file.name)] ?? 'Automático' }] : []),
              ...(IMAGE.includes(ext(e.file.name)) ? [{ id: 'slice-editor', title: 'Folha de sprites → recortar no editor', description: 'Importa a imagem e abre o editor de recorte (grade, detecção automática ou retângulos): cada sprite vira um asset e/ou uma animação.' }] : []),
              ...cands.filter((c) => c.id !== '2d.animated_image' || ext(e.file.name) !== '.png'),
            ]
            return (
              <div key={i} className="rounded-md border p-3">
                <div className="mb-2 flex items-center gap-2">
                  <FileUp className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate text-sm font-medium" title={e.file.name}>{e.file.name}</span>
                  <span className="text-xs text-muted-foreground">{(e.file.size / 1024).toFixed(0)} KB</span>
                  <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={() => remove(i)} disabled={!!busy} title="Tirar da lista"><X /></Button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {choices.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => update(i, { importer: c.id, values: {} })}
                      className={cn('rounded-md border px-2 py-0.5 text-xs', e.importer === c.id ? 'border-primary bg-primary/10' : 'hover:bg-muted')}
                      title={'description' in c ? (c as { description?: string }).description : undefined}
                    >
                      {c.id === '2d.spritesheet' ? 'Folha de sprites → animação' : c.title}
                    </button>
                  ))}
                </div>
                {e.importer === 'layers' && <p className="mt-2 text-xs text-muted-foreground">{choices[0].description}</p>}
            {(current?.description || e.importer === 'slice-editor') && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {e.importer === 'slice-editor' ? 'Importa a imagem e abre o editor de recorte: defina a grade (ou detecte), escolha as células e gere um asset por sprite e/ou uma animação.' : current?.description}
                  </p>
                )}
                {current?.options_schema && (
                  <SchemaForm
                    className="mt-2"
                    schema={current.options_schema}
                    value={e.values}
                    onChange={(v) => update(i, { values: v })}
                    hidden={['descriptor', 'descriptor_file', 'rects', 'auto_descriptor']}
                  />
                )}
              </div>
            )
          })}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={!!busy}>Cancelar</Button>
          <Button onClick={run} disabled={!!busy || !list.length}>
            {busy ? <Loader2 className="animate-spin" /> : <FileUp />} {busy ?? 'Importar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

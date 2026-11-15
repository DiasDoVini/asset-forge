// Opções > Animações 3D: pastas da biblioteca de animações (GLBs com esqueleto e clipes, ex.: Universal Animation
// Library 1 e 2 da Quaternius) que o pipeline de personagem aplica nos modelos.
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FolderOpen, Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ErrorState, LoadingState } from '../components/PageState'

export function AnimLibraryTab() {
  const qc = useQueryClient()
  const lib = useQuery({ queryKey: ['animation-library'], queryFn: api.animationLibrary })
  const [folders, setFolders] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (lib.data) setFolders(lib.data.folders.length ? lib.data.folders : ['']) }, [lib.data])

  if (lib.isError) return <ErrorState error={lib.error} retry={() => lib.refetch()} />
  if (!lib.data) return <LoadingState label="Lendo a biblioteca…" />
  const d = lib.data
  const dirty = JSON.stringify(folders.filter(Boolean)) !== JSON.stringify(d.folders)
  const clips = d.files.filter((f) => !f.root_motion).reduce((n, f) => n + f.clips.length, 0)

  const save = async () => {
    setSaving(true)
    try {
      const out = await api.setAnimationLibraryFolders(folders.map((f) => f.trim()).filter(Boolean))
      qc.setQueryData(['animation-library'], out)
      toast.success(`Biblioteca: ${out.files.length} arquivo(s)`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ''))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <h2 className="mb-1 text-sm font-medium">Biblioteca de animações</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Pastas com arquivos <span className="font-mono">.glb</span> que têm esqueleto humanoide e clipes (por exemplo a
          Universal Animation Library 1 e 2, pasta <span className="font-mono">Unreal-Godot</span>). O pipeline
          “Personagem → animável” encaixa esse esqueleto no seu modelo e aplica os clipes. Arquivos terminados em
          <span className="font-mono"> _RM</span> são as versões com deslocamento (root motion). Subpastas são lidas também.
        </p>
        <div className="space-y-2">
          {folders.map((f, i) => (
            <div key={i} className="flex gap-2">
              <FolderOpen className="mt-2 size-4 shrink-0 text-muted-foreground" />
              <Input value={f} placeholder="C:\caminho\para\a\biblioteca" aria-label={`Pasta ${i + 1}`}
                onChange={(e) => setFolders((fs) => fs.map((x, j) => (j === i ? e.target.value : x)))} />
              <Button size="icon-sm" variant="ghost" aria-label="Remover pasta" title="Remover pasta"
                onClick={() => setFolders((fs) => (fs.length > 1 ? fs.filter((_, j) => j !== i) : ['']))}><Trash2 /></Button>
            </div>
          ))}
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setFolders((fs) => [...fs, ''])}><Plus /> Outra pasta</Button>
            <Button size="sm" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 className="animate-spin" /> : <Check />} Salvar</Button>
          </div>
        </div>
        <ul className="mt-3 space-y-0.5 text-xs text-muted-foreground">
          {d.searched.map((s) => (
            <li key={s.path} className="font-mono">{s.exists ? '✓' : '✗'} {s.path}</li>
          ))}
        </ul>
      </section>

      <section className="rounded-lg border bg-card p-4">
        <h2 className="mb-1 text-sm font-medium">Encontrado</h2>
        {d.files.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nenhum arquivo de animação nas pastas acima.</p>
        ) : (
          <>
            <p className="mb-3 text-sm text-muted-foreground">{d.files.length} arquivo(s), {clips} clipes sem root motion.</p>
            <div className="space-y-3">
              {d.files.map((f) => (
                <details key={f.id} className="rounded-md border p-2">
                  <summary className="cursor-pointer text-sm">
                    <span className="font-medium">{f.id}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{f.clips.length} clipes · {f.joints} ossos</span>
                    {f.root_motion && <Badge variant="secondary" className="ml-2">root motion</Badge>}
                  </summary>
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={f.path}>{f.path}</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {f.clips.map((c) => <Badge key={c.name} variant="outline" className="font-mono text-[11px]">{c.name} · {c.duration.toFixed(1)}s</Badge>)}
                  </div>
                </details>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { NsfwSettings } from './Nsfw'
import { SoulAttach } from './SoulAttach'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ImagePlus, Plus, Save, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Category, type Project, type Style } from '../api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Separator } from '@/components/ui/separator'

const CORE_KEYS = new Set(['slug', 'path', 'name', 'description', 'created', 'style', 'folders', 'categories'])

export function StyleSettings({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description)
  const [style, setStyle] = useState<Style>(project.style)
  const [paletteText, setPaletteText] = useState(project.style.palette.join(', '))
  const [cats, setCats] = useState<Category[]>(project.categories ?? [])
  const blocks = Object.fromEntries(Object.entries(project).filter(([k, v]) => !CORE_KEYS.has(k) && v && typeof v === 'object'))
  const [blocksText, setBlocksText] = useState(JSON.stringify(blocks, null, 2))
  const [blocksErr, setBlocksErr] = useState<string | null>(null)

  useEffect(() => {
    setName(project.name)
    setDescription(project.description)
    setStyle(project.style)
    setPaletteText(project.style.palette.join(', '))
    setCats(project.categories ?? [])
  }, [project])

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['project', project.slug] })
    qc.invalidateQueries({ queryKey: ['projects'] })
  }

  const save = useMutation({
    mutationFn: () => {
      let modalities: Record<string, Record<string, unknown>> | undefined
      try {
        modalities = JSON.parse(blocksText || '{}')
        setBlocksErr(null)
      } catch (e) {
        setBlocksErr(String(e))
        throw e
      }
      const palette = paletteText.split(/[,\s]+/).map((s) => s.trim()).filter((s) => /^#?[0-9a-f]{3,6}$/i.test(s)).map((s) => (s.startsWith('#') ? s : `#${s}`))
      const categories = cats.filter((c) => c.name.trim()).map((c) => ({ ...c, id: c.id || c.name.trim().toLowerCase().normalize('NFD').replace(/[^a-z0-9]+/g, '-') }))
      return api.patchProject(project.slug, { name, description, style: { ...style, palette }, modalities, categories })
    },
    onSuccess: () => {
      refresh()
      toast.success('Projeto salvo')
    },
    onError: (e) => toast.error(String(e)),
  })

  const addRefs = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.multiple = true
    inp.accept = 'image/*'
    inp.onchange = async () => {
      for (const f of inp.files ?? []) await api.uploadRef(project.slug, f)
      refresh()
    }
    inp.click()
  }

  const palette = paletteText.split(/[,\s]+/).filter((s) => /^#?[0-9a-f]{3,6}$/i.test(s))

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6">
      <section className="grid gap-4">
        <h2 className="text-lg font-semibold">Projeto</h2>
        <div className="grid grid-cols-2 gap-4">
          <div className="grid gap-1.5"><Label>Nome</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="grid gap-1.5"><Label>Descrição</Label><Input value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        </div>
        <p className="text-xs text-muted-foreground">Pasta: <code>{project.path}</code></p>
      </section>

      <Separator />

      <SoulAttach project={project} />

      <Separator />

      <NsfwSettings project={project} />

      <Separator />

      <section className="grid gap-4">
        <div>
          <h2 className="text-lg font-semibold">Estilo global</h2>
          <p className="text-sm text-muted-foreground">Aplicado automaticamente em todos os pipelines (2D e 3D).</p>
        </div>
        <div className="grid gap-1.5">
          <Label>Prompt de estilo</Label>
          <Textarea rows={3} value={style.prompt} onChange={(e) => setStyle({ ...style, prompt: e.target.value })} placeholder="16-bit pixel art, dark fantasy, muted palette, side view" />
        </div>
        <div className="grid gap-1.5">
          <Label>Negative prompt</Label>
          <Textarea rows={2} value={style.negative} onChange={(e) => setStyle({ ...style, negative: e.target.value })} placeholder="blurry, text, watermark" />
        </div>
        <div className="grid gap-1.5">
          <Label>System prompt <span className="font-normal text-muted-foreground">(instruções para agentes de IA ao criar prompts neste projeto)</span></Label>
          <Textarea rows={4} value={style.system_prompt} onChange={(e) => setStyle({ ...style, system_prompt: e.target.value })} placeholder="Todos os itens são vistos de lado, a 45°. Armas apontam para cima-direita. Personagens têm 2 cabeças de altura..." />
        </div>
        <div className="grid gap-1.5">
          <Label>Paleta <span className="font-normal text-muted-foreground">(usada pelo Pixelizer)</span></Label>
          <Input value={paletteText} onChange={(e) => setPaletteText(e.target.value)} placeholder="#1a1c2c, #5d275d, #b13e53, ..." />
          {palette.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {palette.map((c, i) => <span key={i} className="size-6 rounded border" style={{ background: c.startsWith('#') ? c : `#${c}` }} title={c} />)}
            </div>
          )}
        </div>
      </section>

      <section className="grid gap-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold">Referências de estilo</h3>
            <p className="text-sm text-muted-foreground">Usadas pelo Image → 2D (até 2) como guia visual.</p>
          </div>
          <Button variant="outline" size="sm" onClick={addRefs}><ImagePlus /> Adicionar</Button>
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(110px,1fr))] gap-3">
          {project.style.refs.map((r) => (
            <div key={r} className="group relative overflow-hidden rounded-lg border">
              <img src={`/files/${project.slug}/refs/${encodeURIComponent(r)}`} alt={r} className="aspect-square w-full object-cover" />
              <button
                className="absolute top-1 right-1 hidden rounded bg-black/70 p-0.5 group-hover:block"
                onClick={async () => {
                  await api.deleteRef(project.slug, r)
                  refresh()
                }}
              >
                <X className="size-3.5" />
              </button>
              <div className="truncate px-1.5 py-1 text-xs">{r}</div>
            </div>
          ))}
        </div>
      </section>

      <Separator />

      <section className="grid gap-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold">Categorias</h3>
            <p className="text-sm text-muted-foreground">Cada asset pode ter uma categoria (filtro e cor na galeria). Pastas organizam por objeto/personagem.</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setCats([...cats, { id: '', name: '', color: '#94a3b8' }])}><Plus /> Categoria</Button>
        </div>
        <div className="grid gap-1.5">
          {cats.map((c, i) => (
            <div key={i} className="flex items-center gap-2">
              <input type="color" value={c.color} onChange={(e) => setCats(cats.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))}
                className="size-8 shrink-0 cursor-pointer rounded border bg-transparent" />
              <Input value={c.name} placeholder="Nome" onChange={(e) => setCats(cats.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <code className="w-28 shrink-0 truncate text-xs text-muted-foreground" title="id usado pela CLI/MCP">{c.id || 'novo'}</code>
              <Button size="icon-sm" variant="ghost" onClick={() => setCats(cats.filter((_, j) => j !== i))} title="Remover categoria"><Trash2 /></Button>
            </div>
          ))}
        </div>
      </section>

      <Separator />

      <section className="grid gap-2">
        <h3 className="font-semibold">Configurações por modalidade</h3>
        <p className="text-sm text-muted-foreground">
          Blocos <code>2d</code>, <code>3d</code>… do project.yaml. Ex.: <code>{'{"2d": {"pixel_art": true, "pixel": {"size": 32, "colors": 16}}}'}</code>
        </p>
        <Textarea rows={10} className="font-mono text-xs" value={blocksText} onChange={(e) => setBlocksText(e.target.value)} />
        {blocksErr && <p className="text-xs text-destructive">{blocksErr}</p>}
      </section>

      <div className="sticky bottom-0 flex justify-end border-t bg-background/80 py-3 backdrop-blur">
        <Button onClick={() => save.mutate()} disabled={save.isPending}><Save /> Salvar</Button>
      </div>
    </div>
  )
}

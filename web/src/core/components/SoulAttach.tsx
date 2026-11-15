import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Project } from '../api'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'

/** Seção da aba Estilo: Creator Souls anexadas ao projeto (força, ligar/desligar). */
export function SoulAttach({ project }: { project: Project }) {
  const qc = useQueryClient()
  const souls = useQuery({ queryKey: ['souls'], queryFn: api.souls })
  const attached = project.souls ?? []
  const refresh = () => qc.invalidateQueries({ queryKey: ['project', project.slug] })
  const set = async (id: string, patch: { strength?: number; enabled?: boolean }) => {
    const cur = attached.find((s) => s.id === id)
    try {
      await api.attachSoul(project.slug, { id, strength: patch.strength ?? cur?.strength ?? 0.8, enabled: patch.enabled ?? cur?.enabled ?? true })
      refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }
  const available = (souls.data ?? []).filter((s) => s.status === 'ready' && !attached.some((a) => a.id === s.id))
  return (
    <section className="grid gap-3">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold"><Sparkles className="size-5" /> Creator Souls</h2>
        <p className="text-sm text-muted-foreground">
          Estilo, paleta, tamanho de pixel e referências automáticas de uma coleção. Use “Gerar com Soul” para o efeito
          completo. <Link to="/souls" className="underline">Gerenciar Souls</Link>
        </p>
      </div>
      {attached.map((a) => {
        const s = souls.data?.find((x) => x.id === a.id)
        return (
          <div key={a.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
            {s?.has_cover && <img src={`/api/souls/${a.id}/cover.png?v=${s.version}`} alt="" className="h-10 rounded" style={{ imageRendering: 'pixelated' }} />}
            <Link to={`/souls/${a.id}`} className="font-medium hover:underline">{s?.name ?? a.id}</Link>
            <div className="flex min-w-48 flex-1 items-center gap-2 text-xs">
              <span className="text-muted-foreground">força</span>
              <Slider min={0} max={1} step={0.05} value={[a.strength]} onValueCommitted={(v) => set(a.id, { strength: Array.isArray(v) ? v[0] : v })} />
              <span className="w-8 font-mono">{a.strength.toFixed(2)}</span>
            </div>
            <Switch checked={a.enabled} onCheckedChange={(v) => set(a.id, { enabled: v })} />
            <Button size="icon-sm" variant="ghost" onClick={async () => { await api.detachSoul(project.slug, a.id); refresh() }}><X /></Button>
          </div>
        )
      })}
      {available.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {available.map((s) => (
            <Button key={s.id} variant="outline" size="sm" onClick={() => set(s.id, { strength: 0.8, enabled: true })}>+ {s.name}</Button>
          ))}
        </div>
      )}
      {souls.data?.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma Soul na biblioteca ainda.</p>}
    </section>
  )
}

/** Faixa no formulário de geração: referências que as Souls do projeto vão usar para o prompt atual. */
export function SoulRefsStrip({ project, prompt, onPick, picked }: {
  project: Project
  prompt: string
  onPick?: (ref: string) => void
  picked?: string[]
}) {
  const enabled = (project.souls ?? []).some((s) => s.enabled)
  const q = useQuery({
    queryKey: ['soul-refs', project.slug, prompt],
    queryFn: () => api.projectSoulRefs(project.slug, prompt, 6),
    enabled: enabled && prompt.trim().length > 2,
    staleTime: 30_000,
  })
  if (!enabled || !prompt.trim()) return null
  return (
    <div className="rounded-lg border p-2">
      <p className="mb-1.5 text-xs text-muted-foreground">Referências da Soul para este prompt {onPick && '(clique para escolher; vazio = as 2 primeiras)'}</p>
      <div className="flex gap-1.5 overflow-x-auto">
        {(q.data ?? []).map((r) => (
          <button key={r.ref} onClick={() => onPick?.(r.ref)} title={`${r.name || r.stem} · ${r.category}`}
            className={`flex size-16 shrink-0 items-center justify-center rounded border bg-[#202228] ${picked?.includes(r.ref) ? 'ring-2 ring-primary' : ''}`}>
            <img src={r.url} alt={r.name} className="max-h-[85%] max-w-[85%]" style={{ imageRendering: 'pixelated' }} />
          </button>
        ))}
        {q.data?.length === 0 && <span className="text-xs text-muted-foreground">nenhuma parecida</span>}
      </div>
    </div>
  )
}

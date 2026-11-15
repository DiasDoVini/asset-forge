import { useQuery } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'
import { api } from '@/core/api'
import type { FieldCtx } from '@/core/SchemaForm'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

function useModels(folder: string) {
  return useQuery({ queryKey: ['comfy-models', folder], queryFn: () => api.comfyModels(folder), staleTime: 30_000 })
}

/** Seletor de checkpoint instalado no ComfyUI (models/checkpoints). */
export function CheckpointField({ value, set }: FieldCtx) {
  const q = useModels('checkpoints')
  return (
    <div>
      <Label className="mb-1.5">Checkpoint <span className="text-xs font-normal text-muted-foreground">arquivos em models/checkpoints</span></Label>
      <select value={(value as string) ?? ''} onChange={(e) => set(e.target.value || undefined)} className="h-8 w-full rounded-md border bg-background px-2 text-sm">
        <option value="">(padrão do projeto: 2d.checkpoint)</option>
        {(q.data ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
      {q.data && q.data.length === 0 && <p className="mt-1 text-xs text-muted-foreground">Nenhum checkpoint instalado.</p>}
    </div>
  )
}

type Lora = { name: string; strength: number }

/** Lista de LoRAs (models/loras) com força. */
export function LorasField({ value, set }: FieldCtx) {
  const q = useModels('loras')
  const list = (value as Lora[] | undefined) ?? []
  const update = (i: number, patch: Partial<Lora>) => set(list.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between">
        <Label>LoRAs</Label>
        <Button size="xs" variant="outline" disabled={!q.data?.length} onClick={() => set([...list, { name: q.data![0], strength: 0.8 }])}>
          <Plus /> LoRA
        </Button>
      </div>
      {list.map((l, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <select value={l.name} onChange={(e) => update(i, { name: e.target.value })} className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs">
            {(q.data ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <Input type="number" step={0.05} min={-2} max={2} value={l.strength} onChange={(e) => update(i, { strength: Number(e.target.value) })} className="h-8 w-20" />
          <Button size="icon-sm" variant="ghost" onClick={() => set(list.filter((_, j) => j !== i))}><X /></Button>
        </div>
      ))}
    </div>
  )
}

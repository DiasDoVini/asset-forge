// Diálogo de atalhos: clique em "+" e aperte a combinação; conflitos ficam em vermelho. Salvo no navegador.
import { useEffect, useMemo, useState } from 'react'
import { Plus, RotateCcw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { ACTIONS, comboOf, conflicts, defaults, type Bindings } from './shortcuts'

export function ShortcutsDialog({ bindings, onChange, onClose }: { bindings: Bindings; onChange: (b: Bindings) => void; onClose: () => void }) {
  const [capture, setCapture] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const clash = useMemo(() => conflicts(bindings), [bindings])
  const title = (id: string) => ACTIONS.find((a) => a.id === id)?.title ?? id

  useEffect(() => {
    if (!capture) return
    const on = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') return setCapture(null)
      const c = comboOf(e)
      if (!c) return
      const cur = bindings[capture] ?? []
      if (!cur.includes(c)) onChange({ ...bindings, [capture]: [...cur, c] })
      setCapture(null)
    }
    window.addEventListener('keydown', on, true)
    return () => window.removeEventListener('keydown', on, true)
  }, [capture, bindings, onChange])

  const groups = useMemo(() => {
    const f = q.trim().toLowerCase()
    const m = new Map<string, typeof ACTIONS>()
    for (const a of ACTIONS) {
      if (f && !a.title.toLowerCase().includes(f) && !(bindings[a.id] ?? []).some((k) => k.toLowerCase().includes(f))) continue
      m.set(a.group, [...(m.get(a.group) ?? []), a])
    }
    return [...m]
  }, [q, bindings])

  return (
    <Dialog open onOpenChange={(o) => !o && !capture && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Atalhos do editor</DialogTitle>
          <DialogDescription>
            Clique em + e aperte a combinação (Esc cancela). Fixos: Espaço (arrastar a vista), Esc, Enter (fecha polígono/aplica transformação),
            Delete, Tab (tiles).
          </DialogDescription>
        </DialogHeader>
        <Input placeholder="Buscar ação ou tecla…" value={q} onChange={(e) => setQ(e.target.value)} className="h-8" />
        {clash.size > 0 && (
          <p className="text-xs text-destructive">
            Conflitos: {[...clash].map(([k, ids]) => `${k} (${ids.map(title).join(', ')})`).join('; ')}. Vale a primeira ação da lista.
          </p>
        )}
        <div className="space-y-3 text-sm">
          {groups.map(([g, acts]) => (
            <div key={g}>
              <div className="mb-1 text-xs font-medium text-muted-foreground">{g}</div>
              {acts.map((a) => {
                const keys = bindings[a.id] ?? []
                const changed = JSON.stringify(keys) !== JSON.stringify(a.keys)
                return (
                  <div key={a.id} className="flex items-center gap-1 border-b py-1 last:border-0">
                    <span className={cn('mr-auto truncate', changed && 'text-primary')} title={a.title}>{a.title}</span>
                    {keys.map((k) => (
                      <span key={k} className={cn('inline-flex items-center gap-0.5 rounded border px-1.5 font-mono text-xs', clash.has(k) && 'border-destructive text-destructive')}>
                        {k}
                        <button title="Tirar" onClick={() => onChange({ ...bindings, [a.id]: keys.filter((x) => x !== k) })}><X className="size-3" /></button>
                      </span>
                    ))}
                    <Button size="icon-xs" variant={capture === a.id ? 'secondary' : 'ghost'} title="Adicionar combinação" onClick={() => setCapture(capture === a.id ? null : a.id)}>
                      {capture === a.id ? <span className="px-1 text-[10px]">aperte…</span> : <Plus />}
                    </Button>
                    <Button size="icon-xs" variant="ghost" title="Voltar ao padrão" disabled={!changed} onClick={() => onChange({ ...bindings, [a.id]: [...a.keys] })}><RotateCcw /></Button>
                  </div>
                )
              })}
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onChange(defaults())}>Restaurar todos</Button>
          <Button onClick={onClose}>Fechar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Sparkles, Undo2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

export function targetFor(pipeline: string): string {
  if (pipeline.startsWith('video.')) return 'video'
  if (pipeline === 'audio.music') return 'music'
  if (pipeline.startsWith('audio.')) return 'sfx'
  if (pipeline.endsWith('edit')) return 'edit'
  return 'image'
}

/** Campo de prompt com “Melhorar” via LLM local (Ollama) — respeita a política de conteúdo do projeto. */
export function PromptField({ project, pipeline, value, set, label, description, required }: {
  project: string
  pipeline: string
  value: string
  set: (v: string) => void
  label: string
  description?: string
  required?: boolean
}) {
  const llm = useQuery({ queryKey: ['local-llm'], queryFn: api.localLlm, staleTime: 60_000 })
  const [busy, setBusy] = useState(false)
  const [prev, setPrev] = useState<string | null>(null)
  const improve = async () => {
    setBusy(true)
    try {
      const r = await api.promptAssist(project, value, targetFor(pipeline))
      setPrev(value)
      set(r.prompt)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <Label>{label}{required && <span className="text-destructive"> *</span>}</Label>
        <div className="flex gap-1">
          {prev !== null && (
            <Button size="xs" variant="ghost" onClick={() => { set(prev); setPrev(null) }} title="desfazer"><Undo2 /></Button>
          )}
          <Button size="xs" variant="outline" disabled={!value.trim() || busy || !llm.data?.available}
            title={llm.data?.available ? `reescreve com ${llm.data.model} (local)` : llm.data?.detail ?? 'LLM local indisponível'}
            onClick={improve}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Melhorar
          </Button>
        </div>
      </div>
      <Textarea rows={3} value={value} onChange={(e) => { set(e.target.value); setPrev(null) }} placeholder={description} />
    </div>
  )
}

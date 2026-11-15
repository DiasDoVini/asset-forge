// AI Assistant de um job com falha: chama o diagnóstico (provedor/modelo das Opções, trocável aqui), mostra causa e
// passos e aplica a correção sugerida (repetir com parâmetros, instalar, subir/reiniciar instância).
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2, Wand2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Diagnosis, type Job } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ModelPicker, type ModelChoice } from './ModelPicker'
import { pipelineTitle } from '../i18n/pipelines'

export function DiagnoseButton({ job }: { job: Job }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="xs" variant="outline" onClick={() => setOpen(true)} title="Pedir à IA a causa e uma correção">
        <Bot /> Diagnosticar com IA
      </Button>
      {open && <DiagnoseDialog job={job} onClose={() => setOpen(false)} />}
    </>
  )
}

function DiagnoseDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const info = useQuery({ queryKey: ['ai'], queryFn: api.ai })
  const [choice, setChoice] = useState<ModelChoice | null>(null)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<Diagnosis | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const cur: ModelChoice = choice ?? (info.data ? { provider: info.data.assistant.provider, model: info.data.assistant.model, effort: info.data.assistant.effort } : { provider: 'local', model: '', effort: 'medium' })

  const run = async () => {
    setBusy(true)
    setErr(null)
    setRes(null)
    try {
      setRes(await api.diagnoseJob(job.id, choice ? { provider: cur.provider === 'auto' ? undefined : cur.provider, model: cur.model || undefined } : undefined))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const apply = async (d: Diagnosis) => {
    const f = d.fix
    try {
      if (f.kind === 'retry' || f.kind === 'retry_with_params') {
        const n = await api.retryJob(job.id, { params: f.kind === 'retry_with_params' ? f.params : undefined })
        toast.success('Job reenviado', { description: n.id })
        qc.invalidateQueries({ queryKey: ['jobs'] })
        onClose()
      } else if (f.kind === 'install') {
        nav('/setup')
        onClose()
      } else if (f.kind === 'start_instance' || f.kind === 'restart_instance') {
        await api.comfyAction(f.instance || 'desktop', f.kind === 'start_instance' ? 'start' : 'restart')
        toast.success(`${f.instance}: ${f.kind === 'start_instance' ? 'iniciando' : 'reiniciando'}`, { description: 'Repita o job quando ela estiver online.' })
        nav('/options?tab=comfy')
        onClose()
      }
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e))
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Bot className="size-4" /> AI Assistant</DialogTitle>
          <DialogDescription>
            {pipelineTitle(job.request.pipeline)} · {job.request.project} · job {job.id}
          </DialogDescription>
        </DialogHeader>
        {job.error && <pre className="max-h-32 overflow-auto rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap text-red-300">{job.error}</pre>}
        <div className="flex flex-wrap items-center gap-2">
          <ModelPicker info={info.data} value={cur} onChange={setChoice} size="sm" />
          <Button size="sm" className="ml-auto" onClick={run} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Wand2 />} {res ? 'Diagnosticar de novo' : 'Diagnosticar'}
          </Button>
        </div>
        {busy && <p className="text-sm text-muted-foreground">Juntando o log, o workflow e o estado das instâncias e perguntando ao modelo…</p>}
        {err && <p className="text-sm text-destructive">{err}</p>}
        {res && (
          <div className="space-y-3 text-sm">
            <p className="font-medium">{res.summary}</p>
            <p className="text-muted-foreground">{res.cause}</p>
            {res.steps.length > 0 && (
              <ol className="list-decimal space-y-1 pl-5">
                {res.steps.map((s, i) => <li key={i}>{s}</li>)}
              </ol>
            )}
            <div className="flex flex-wrap items-center gap-2 border-t pt-3">
              {res.fix.kind !== 'none' && (
                <Button onClick={() => apply(res)}>
                  <Wand2 /> {res.fix.label || 'Aplicar correção'}
                </Button>
              )}
              {res.fix.kind === 'retry_with_params' && res.fix.params && (
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{JSON.stringify(res.fix.params)}</code>
              )}
              <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="secondary">confiança {res.confidence}</Badge>
                {res.model} · {res.seconds}s
              </span>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

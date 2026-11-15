import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { api, type Project } from './api'
import { onForgeEvent } from './events'

/** Envia um job e, quando termina, abre a versão resultante (toasts de progresso/erro). */
export function useJobNav(project: Project, message = 'Gerando…') {
  const nav = useNavigate()
  const [pending, setPending] = useState<string | null>(null)
  useEffect(
    () =>
      onForgeEvent((e) => {
        if (e.type !== 'job' || e.job.id !== pending) return
        if (e.job.status === 'done' && e.job.result) {
          setPending(null)
          const [slug, v] = e.job.result.split('@v')
          nav(`/p/${project.slug}/a/${slug}/${parseInt(v, 10)}`)
          toast.success(`Pronto: ${e.job.result}`)
        } else if (e.job.status === 'error' || e.job.status === 'cancelled') {
          setPending(null)
          toast.error(e.job.error ?? 'job cancelado')
        }
      }),
    [pending, nav, project.slug],
  )
  const submit = async (pipeline: string, params: Record<string, unknown>, asset?: string, msg?: string) => {
    try {
      const j = await api.submitJob({ project: project.slug, pipeline, params, asset })
      setPending(j.id)
      toast.message(msg ?? message, { description: `job ${j.id}` })
    } catch (e) {
      toast.error(String(e))
    }
  }
  return { submit, busy: pending !== null }
}

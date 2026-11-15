import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../api'
import { JobList, type JobFilter } from '../components/JobList'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/native-select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageBody, PageHeader } from '../components/PageHeader'

const FILTERS: { id: JobFilter; title: string }[] = [
  { id: 'all', title: 'Todos' },
  { id: 'failed', title: 'Falhas' },
  { id: 'done', title: 'Concluídos' },
  { id: 'active', title: 'Em andamento' },
]

export default function JobsPage() {
  const [filter, setFilter] = useState<JobFilter>('all')
  const [project, setProject] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs({ limit: 300 }) })
  const qc = useQueryClient()
  const failedCount = (jobs.data ?? []).filter((j) => (j.status === 'error' || j.status === 'cancelled') && (!project || j.request.project === project)).length

  const clear = async (status: string) => {
    const r = await api.clearJobs({ status, project: project || undefined })
    qc.invalidateQueries({ queryKey: ['jobs'] })
    toast.success(`${r.deleted} jobs removidos do histórico`)
  }

  return (
    <PageBody>
        <PageHeader
          title="Histórico de jobs"
          description={<>Jobs da UI, da CLI e de agentes (MCP), guardados mesmo após reiniciar o servidor. Falhou? “Tentar de novo” repete com os
            mesmos parâmetros; “Editar e repetir” abre o formulário preenchido.</>}
        />
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Tabs value={filter} onValueChange={(v) => setFilter(v as JobFilter)}>
            <TabsList>
              {FILTERS.map((f) => (
                <TabsTrigger key={f.id} value={f.id}>
                  {f.title}{f.id === 'failed' && failedCount > 0 ? ` (${failedCount})` : ''}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <NativeSelect aria-label="Projeto" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">Todos os projetos</option>
            {(projects.data ?? []).map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
          </NativeSelect>
          <div className="ml-auto flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => clear('done')}>Limpar concluídos</Button>
            <Button size="sm" variant="ghost" onClick={() => clear('error,cancelled')}>Limpar falhas</Button>
          </div>
        </div>
        <JobList filter={filter} project={project || undefined} />
    </PageBody>
  )
}

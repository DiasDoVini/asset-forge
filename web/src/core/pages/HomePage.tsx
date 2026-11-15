import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, FolderPlus, Plus } from 'lucide-react'
import { api } from '../api'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CreateProjectDialog } from '../components/CreateProjectDialog'
import { PageBody, PageHeader } from '../components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

export default function HomePage() {
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const setup = useQuery({ queryKey: ['setup'], queryFn: api.setup, staleTime: 60_000 })

  return (
    <PageBody>
        <PageHeader
          title="Projetos"
          description="Cada projeto tem seu estilo, referências e assets (2D, 3D, áudio, VFX e vídeo)."
          actions={<CreateProjectDialog trigger={<Button><Plus /> Novo projeto</Button>} />}
        />

        {(setup.data?.items ?? []).some((i) => i.required && i.state !== 'ok') && (
          <div className="mb-4 flex items-start gap-3 rounded-lg border border-sky-500/40 bg-sky-500/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 text-sky-400" />
            <div>
              Faltam itens essenciais: {setup.data!.items.filter((i) => i.required && i.state !== 'ok').map((i) => i.title).join(', ')}.{' '}
              <Link to="/setup" className="underline underline-offset-2">Abrir Instalação</Link>
            </div>
          </div>
        )}
        {health.data && !health.data.comfy.online && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 text-amber-500" />
            <div>
              ComfyUI offline em <code>{health.data.comfy.url}</code>. Pipelines de GPU vão falhar até ele subir.{' '}
              <Link to="/options?tab=comfy" className="underline underline-offset-2">Iniciar e ver o status em Opções</Link>
            </div>
          </div>
        )}
        {health.data && Object.keys(health.data.modality_errors).length > 0 && (
          <div className="mb-6 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
            Modalidades com erro: {Object.entries(health.data.modality_errors).map(([k, v]) => `${k}: ${v}`).join('; ')}
          </div>
        )}

        {projects.isLoading && <LoadingState />}
        {projects.isError && <ErrorState error={projects.error} retry={() => projects.refetch()} />}
        {projects.data?.length === 0 && (
          <EmptyState
            icon={<FolderPlus />}
            title="Nenhum projeto ainda"
            description="Crie um projeto para definir o estilo do jogo e começar a gerar assets."
            action={<CreateProjectDialog trigger={<Button><Plus /> Novo projeto</Button>} />}
          />
        )}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(projects.data ?? []).map((p) => (
            <Link key={p.slug} to={`/p/${p.slug}`}>
              <Card className="h-full transition-colors hover:bg-muted/40">
                <CardHeader>
                  <CardTitle>{p.name}</CardTitle>
                  <CardDescription className="line-clamp-3">{p.style.prompt || p.description || 'sem estilo definido'}</CardDescription>
                  {p.style.palette.length > 0 && (
                    <div className="mt-2 flex gap-0.5">
                      {p.style.palette.slice(0, 16).map((c) => (
                        <span key={c} className="size-3 rounded-sm" style={{ background: c }} />
                      ))}
                    </div>
                  )}
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>
    </PageBody>
  )
}

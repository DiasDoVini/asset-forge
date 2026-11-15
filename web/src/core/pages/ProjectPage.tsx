import { useNavigate, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AssetGallery } from '../components/AssetGallery'
import { GeneratePanel } from '../components/GeneratePanel'
import { StyleSettings } from '../components/StyleSettings'
import { JobList } from '../components/JobList'
import { RefinePanel } from '../components/RefinePanel'
import { NsfwBadge } from '../components/Nsfw'
import { ChatPanel } from '../components/ChatPanel'
import { ErrorState, LoadingState } from '../components/PageState'

const TABS = ['assets', 'chat', 'gerar', 'refinar', 'estilo', 'jobs'] as const

export default function ProjectPage() {
  const { project = '', tab = 'assets' } = useParams()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['project', project], queryFn: () => api.project(project) })
  const p = q.data

  if (q.isError) return <ErrorState error={q.error} back={{ to: '/', label: 'Projetos' }} retry={() => q.refetch()} />
  if (!p) return <LoadingState label="Abrindo o projeto…" />

  return (
    <Tabs value={TABS.includes(tab as never) ? tab : 'assets'} onValueChange={(v) => nav(`/p/${project}/${v}`)} className="flex h-full flex-col gap-0">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b px-4 py-3 md:px-6">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 truncate text-xl font-semibold tracking-tight">{p.name} <NsfwBadge project={p} /></h1>
          <p className="truncate text-xs text-muted-foreground" title={p.style.prompt}>{p.style.prompt || 'sem estilo global'}</p>
        </div>
        <TabsList className="ml-auto">
          <TabsTrigger value="assets">Assets</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="gerar">Gerar</TabsTrigger>
          <TabsTrigger value="refinar">Refinar</TabsTrigger>
          <TabsTrigger value="estilo">Estilo</TabsTrigger>
          <TabsTrigger value="jobs">Jobs</TabsTrigger>
        </TabsList>
      </header>
      <TabsContent value="assets" className="min-h-0 flex-1">
        <AssetGallery project={p} />
      </TabsContent>
      <TabsContent value="chat" className="min-h-0 flex-1">
        <ChatPanel project={p} />
      </TabsContent>
      <TabsContent value="gerar" className="min-h-0 flex-1">
        <GeneratePanel project={p} />
      </TabsContent>
      <TabsContent value="refinar" className="min-h-0 flex-1">
        <RefinePanel project={p} />
      </TabsContent>
      <TabsContent value="estilo" className="min-h-0 flex-1 overflow-auto">
        <StyleSettings project={p} />
      </TabsContent>
      <TabsContent value="jobs" className="min-h-0 flex-1 overflow-auto p-6">
        <JobList project={p.slug} />
      </TabsContent>
    </Tabs>
  )
}

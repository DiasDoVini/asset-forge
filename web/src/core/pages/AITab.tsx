// Opções › Assistente de IA: credencial do Claude e provedor/modelo padrão do AI Assistant (jobs) e do chat.
import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, KeyRound, MessagesSquare, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AIInfo, type AIRole } from '../api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ModelPicker } from '../components/ModelPicker'
import { LoadingState } from '../components/PageState'

export function AITab() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['ai'], queryFn: api.ai })
  const d = q.data
  if (!d) return <LoadingState />
  const save = async (role: 'assistant' | 'chat', v: { provider: string; model: string; effort: string }) => {
    try {
      qc.setQueryData(['ai'], await api.aiSetRole(role, v))
      toast.success('Salvo')
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e))
    }
  }
  return (
    <div className="space-y-4">
      <ClaudeKey d={d} />
      <RoleCard
        icon={<Bot className="size-4" />}
        title="AI Assistant (jobs com falha)"
        description="Lê o erro, o log, o workflow rejeitado pelo ComfyUI e o estado das instâncias, explica a causa e propõe uma correção que você aplica com um clique (botão “Diagnosticar com IA” nos jobs que falharam)."
        role={d.assistant}
        info={d}
        onSave={(v) => save('assistant', v)}
      />
      <RoleCard
        icon={<MessagesSquare className="size-4" />}
        title="Chat do projeto (padrão)"
        description="Modelo usado nas conversas novas da aba Chat de cada projeto; dá para trocar por conversa. O Claude usa as ferramentas melhor; modelos locais funcionam offline (prefira um com ferramentas, como o Qwen3.8)."
        role={d.chat}
        info={d}
        onSave={(v) => save('chat', v)}
      />
    </div>
  )
}

function RoleCard({ icon, title, description, role, info, onSave }: {
  icon: ReactNode
  title: string
  description: string
  role: AIRole
  info: AIInfo
  onSave: (v: { provider: string; model: string; effort: string }) => void
}) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-medium">{icon} {title}</h2>
      <p className="mb-3 max-w-prose text-xs text-muted-foreground">{description}</p>
      <ModelPicker
        info={info}
        allowAuto
        value={{ provider: role.configured === 'auto' ? 'auto' : role.provider, model: role.model, effort: role.effort }}
        onChange={(v) => onSave(v)}
      />
      {role.configured === 'auto' && (
        <p className="mt-2 text-xs text-muted-foreground">Automático: usa o Claude quando há credencial, senão o modelo local (agora: {role.provider === 'claude' ? 'Claude' : 'local'}).</p>
      )}
    </section>
  )
}

function ClaudeKey({ d }: { d: AIInfo }) {
  const qc = useQueryClient()
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <section className="rounded-lg border bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-medium"><KeyRound className="size-4" /> Claude (API da Anthropic)</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        {d.claude.available ? `Disponível (${d.claude.detail}).` : 'Sem credencial.'} A chave fica em <code>settings.json</code> ao lado da pasta de
        projetos, só nesta máquina. Também funcionam <code>ANTHROPIC_API_KEY</code> e <code>ant auth login</code>.
      </p>
      <form
        className="flex max-w-xl items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault()
          setBusy(true)
          try {
            qc.setQueryData(['ai'], await api.aiSetKey(key))
            setKey('')
            toast.success('Chave conferida e salva')
          } catch (err) {
            toast.error(String(err instanceof Error ? err.message : err))
          } finally {
            setBusy(false)
          }
        }}
      >
        <Input type="password" autoComplete="off" placeholder={d.claude.key_saved ? 'chave salva — cole outra para trocar' : 'sk-ant-…'} value={key} onChange={(e) => setKey(e.target.value)} className="h-8" />
        <Button size="sm" type="submit" disabled={busy || key.trim().length < 10}>Salvar</Button>
        {d.claude.key_saved && (
          <Button size="sm" type="button" variant="ghost" aria-label="Remover a chave salva" title="Remover a chave salva"
            onClick={async () => { qc.setQueryData(['ai'], await api.aiDeleteKey()); toast.success('Chave removida') }}>
            <Trash2 />
          </Button>
        )}
      </form>
    </section>
  )
}

// Escolha de provedor (local x Claude), modelo e esforço — usado nas Opções (padrões) e no chat (por conversa).
import { Cloud, HardDrive } from 'lucide-react'
import type { AIInfo } from '../api'
import { NativeSelect } from '@/components/ui/native-select'
import { cn } from '@/lib/utils'

export type ModelChoice = { provider: 'local' | 'claude' | 'auto'; model: string; effort: string }

const EFFORT_LABEL: Record<string, string> = { low: 'rápido', medium: 'médio', high: 'alto', xhigh: 'muito alto', max: 'máximo' }

export function ModelPicker({ info, value, onChange, allowAuto = false, size = 'default', className }: {
  info: AIInfo | undefined
  value: ModelChoice
  onChange: (v: ModelChoice) => void
  allowAuto?: boolean
  size?: 'sm' | 'default'
  className?: string
}) {
  const provider = value.provider
  const models = provider === 'claude' ? info?.claude.models ?? [] : provider === 'local' ? info?.local.models ?? [] : []
  const unavailable = provider === 'claude' ? info && !info.claude.available : provider === 'local' ? info && !info.local.available : false
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      <div className="inline-flex rounded-lg border p-0.5" role="radiogroup" aria-label="Provedor">
        {(allowAuto ? (['auto', 'local', 'claude'] as const) : (['local', 'claude'] as const)).map((p) => (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={provider === p}
            onClick={() => onChange({ provider: p, model: '', effort: value.effort })}
            className={cn('inline-flex items-center gap-1 rounded-md px-2 text-xs', size === 'sm' ? 'h-6' : 'h-7',
              provider === p ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}
          >
            {p === 'local' ? <HardDrive className="size-3" /> : p === 'claude' ? <Cloud className="size-3" /> : null}
            {p === 'auto' ? 'Automático' : p === 'local' ? 'Local' : 'Claude'}
          </button>
        ))}
      </div>
      {provider !== 'auto' && (
        <NativeSelect size={size === 'sm' ? 'sm' : 'default'} aria-label="Modelo" value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })}>
          <option value="">
            {provider === 'claude' ? 'claude-opus-5-5 (padrão)' : `padrão${info?.local.default ? ` (${info.local.default})` : ''}`}
          </option>
          {models.filter((m) => !(provider === 'claude' && m === 'claude-opus-5-5')).map((m) => <option key={m} value={m}>{m}</option>)}
        </NativeSelect>
      )}
      {provider === 'claude' && (
        <NativeSelect size={size === 'sm' ? 'sm' : 'default'} aria-label="Esforço" value={value.effort || 'medium'} onChange={(e) => onChange({ ...value, effort: e.target.value })}>
          {(info?.claude.efforts ?? ['low', 'medium', 'high', 'xhigh', 'max']).map((e) => <option key={e} value={e}>esforço {EFFORT_LABEL[e] ?? e}</option>)}
        </NativeSelect>
      )}
      {unavailable && (
        <span className="text-xs text-amber-300">
          {provider === 'claude' ? 'sem credencial do Claude (Opções › Assistente de IA)' : 'Ollama fora do ar'}
        </span>
      )}
    </div>
  )
}

// Cabeçalho padrão das páginas de nível superior (Projetos, Jobs, Souls, Instalação, Opções, Workflows).
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function PageHeader({ title, description, actions, className }: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('mb-6 flex flex-wrap items-start gap-3', className)}>
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 max-w-prose text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/** Moldura de conteúdo das páginas de nível superior (rolagem + largura máxima + margens). */
export function PageBody({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="h-full overflow-auto">
      <div className={cn('mx-auto w-full p-4 md:p-6', wide ? 'max-w-6xl' : 'max-w-5xl')}>{children}</div>
    </div>
  )
}

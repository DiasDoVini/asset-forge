// Estados padrão de página/seção: carregando, erro (com voltar/tentar de novo) e vazio.
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { AlertTriangle, ArrowLeft, Loader2, RotateCcw } from 'lucide-react'
import { Button, buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function LoadingState({ label = 'Carregando…', className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cn('flex h-full min-h-32 items-center justify-center gap-2 p-6 text-sm text-muted-foreground', className)}>
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  )
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^Error:\s*/, '')

export function ErrorState({ error, back, retry, className }: {
  error: unknown
  back?: { to: string; label: string }
  retry?: () => void
  className?: string
}) {
  return (
    <div role="alert" className={cn('flex h-full min-h-40 flex-col items-center justify-center gap-3 p-6 text-center', className)}>
      <AlertTriangle className="size-8 text-destructive/80" />
      <p className="max-w-md text-sm text-destructive">{message(error)}</p>
      <div className="flex gap-2">
        {back && (
          <Link to={back.to} className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            <ArrowLeft /> {back.label}
          </Link>
        )}
        {retry && (
          <Button variant="outline" size="sm" onClick={retry}>
            <RotateCcw /> Tentar de novo
          </Button>
        )}
      </div>
    </div>
  )
}

export function EmptyState({ icon, title, description, action, className }: {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-10 text-center', className)}>
      {icon && <div className="text-muted-foreground/70 [&_svg]:size-8">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

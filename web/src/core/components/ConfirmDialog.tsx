// Confirmação e pergunta com o diálogo do app (no lugar de window.confirm/prompt): tema escuro, foco, Esc cancela,
// Enter confirma. Uso: const { confirm, prompt } = useConfirm(); if (!(await confirm({ title: '…' }))) return
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type ConfirmOpts = { title: string; description?: ReactNode; confirmLabel?: string; cancelLabel?: string; destructive?: boolean }
type PromptOpts = { title: string; label?: string; description?: ReactNode; defaultValue?: string; placeholder?: string; confirmLabel?: string }
type Api = { confirm: (o: ConfirmOpts) => Promise<boolean>; prompt: (o: PromptOpts) => Promise<string | null> }

const Ctx = createContext<Api | null>(null)

type State =
  | { kind: 'confirm'; opts: ConfirmOpts; resolve: (v: boolean) => void }
  | { kind: 'prompt'; opts: PromptOpts; resolve: (v: string | null) => void }

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State | null>(null)
  const [value, setValue] = useState('')
  const settled = useRef(false)

  const confirm = useCallback((opts: ConfirmOpts) => new Promise<boolean>((resolve) => {
    settled.current = false
    setState({ kind: 'confirm', opts, resolve })
  }), [])
  const prompt = useCallback((opts: PromptOpts) => new Promise<string | null>((resolve) => {
    settled.current = false
    setValue(opts.defaultValue ?? '')
    setState({ kind: 'prompt', opts, resolve })
  }), [])

  const finish = (ok: boolean) => {
    if (!state || settled.current) return
    settled.current = true
    if (state.kind === 'confirm') state.resolve(ok)
    else state.resolve(ok ? value : null)
    setState(null)
  }

  return (
    <Ctx.Provider value={{ confirm, prompt }}>
      {children}
      <Dialog open={!!state} onOpenChange={(o) => !o && finish(false)}>
        {state && (
          <DialogContent className="sm:max-w-md">
            <form
              onSubmit={(e) => {
                e.preventDefault()
                finish(true)
              }}
              className="grid gap-4"
            >
              <DialogHeader>
                <DialogTitle>{state.opts.title}</DialogTitle>
                {state.opts.description && <DialogDescription render={<div />}>{state.opts.description}</DialogDescription>}
              </DialogHeader>
              {state.kind === 'prompt' && (
                <div className="grid gap-1.5">
                  {state.opts.label && <Label htmlFor="confirm-prompt">{state.opts.label}</Label>}
                  <Input id="confirm-prompt" autoFocus value={value} placeholder={state.opts.placeholder} onChange={(e) => setValue(e.target.value)} />
                </div>
              )}
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => finish(false)}>
                  {state.kind === 'confirm' ? state.opts.cancelLabel ?? 'Cancelar' : 'Cancelar'}
                </Button>
                <Button
                  type="submit"
                  autoFocus={state.kind === 'confirm'}
                  variant={state.kind === 'confirm' && state.opts.destructive ? 'destructive' : 'default'}
                  disabled={state.kind === 'prompt' && !value.trim()}
                >
                  {state.opts.confirmLabel ?? 'Confirmar'}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        )}
      </Dialog>
    </Ctx.Provider>
  )
}

export function useConfirm(): Api {
  const api = useContext(Ctx)
  if (!api) throw new Error('useConfirm fora do ConfirmProvider')
  return api
}

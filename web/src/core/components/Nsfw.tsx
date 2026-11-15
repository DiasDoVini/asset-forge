import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Project } from '../api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

const KEY = (slug: string) => `forge.nsfw.reveal.${slug}`

function readReveal(slug: string) {
  try {
    return localStorage.getItem(KEY(slug)) === '1'
  } catch {
    return false
  }
}

/** Desfoca miniaturas (via [data-nsfw=hidden] no <html>) enquanto a página de um projeto +18 está aberta. */
export function useNsfwBlur(project: Project | undefined) {
  const [reveal, setReveal] = useState(() => (project ? readReveal(project.slug) : false))
  useEffect(() => {
    if (project) setReveal(readReveal(project.slug))
  }, [project?.slug])
  useEffect(() => {
    const el = document.documentElement
    if (project?.nsfw) el.dataset.nsfw = reveal ? 'shown' : 'hidden'
    else delete el.dataset.nsfw
    return () => {
      delete el.dataset.nsfw
    }
  }, [project?.nsfw, reveal])
  const toggle = () => {
    if (!project) return
    const v = !reveal
    setReveal(v)
    try {
      localStorage.setItem(KEY(project.slug), v ? '1' : '0')
    } catch {
      /* sem storage */
    }
  }
  return { reveal, toggle }
}

export function NsfwBadge({ project }: { project: Project }) {
  const { reveal, toggle } = useNsfwBlur(project)
  if (!project.nsfw) return null
  return (
    <button onClick={toggle} title={reveal ? 'ocultar miniaturas' : 'mostrar miniaturas'}
      className="inline-flex items-center gap-1 rounded bg-rose-600/90 px-1.5 py-0.5 text-xs font-semibold text-white">
      +18 {reveal ? <Eye className="size-3" /> : <EyeOff className="size-3" />}
    </button>
  )
}

/** Seção das configurações do projeto: liga/desliga o modo +18 com confirmação de maioridade. */
export function NsfwSettings({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [ask, setAsk] = useState(false)
  const [adult, setAdult] = useState(false)
  const m = useMutation({
    mutationFn: (enabled: boolean) => api.setNsfw(project.slug, enabled, enabled && adult),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['project', project.slug] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success(p.nsfw ? 'Modo +18 habilitado' : 'Modo +18 desabilitado')
      setAsk(false)
      setAdult(false)
    },
    onError: (e) => toast.error(String(e)),
  })
  return (
    <section className="grid gap-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold"><ShieldAlert className="size-5" /> Conteúdo adulto (+18)</h2>
          <p className="text-sm text-muted-foreground">
            Permite gerar conteúdo adulto neste projeto. Miniaturas ficam desfocadas por padrão, o crítico automático
            (Claude API) e as prévias para agentes ficam desativados. Referências a menores de idade são sempre recusadas.
          </p>
        </div>
        <Switch checked={Boolean(project.nsfw)} disabled={m.isPending} onCheckedChange={(v) => (v ? setAsk(true) : m.mutate(false))} />
      </div>
      <Dialog open={ask} onOpenChange={setAsk}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Habilitar conteúdo +18?</DialogTitle>
            <DialogDescription>
              Este projeto passará a aceitar prompts com conteúdo adulto. Todos os personagens devem ser adultos — qualquer
              referência a menores é recusada. Não use imagens de pessoas reais sem consentimento.
            </DialogDescription>
          </DialogHeader>
          <Label className="flex items-center gap-2 text-sm">
            <Checkbox checked={adult} onCheckedChange={(v) => setAdult(Boolean(v))} /> Confirmo que tenho 18 anos ou mais.
          </Label>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAsk(false)}>Cancelar</Button>
            <Button variant="destructive" disabled={!adult || m.isPending} onClick={() => m.mutate(true)}>Habilitar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

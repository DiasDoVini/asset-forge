import { useState, type ReactElement } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

export function CreateProjectDialog({ trigger }: { trigger: ReactElement }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [style, setStyle] = useState('')
  const [description, setDescription] = useState('')
  const qc = useQueryClient()
  const nav = useNavigate()
  const create = useMutation({
    mutationFn: () => api.createProject({ slug: name, name, description, style: { prompt: style } }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      setOpen(false)
      setName('')
      setStyle('')
      setDescription('')
      nav(`/p/${p.slug}`)
    },
    onError: (e) => toast.error(String(e)),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Novo projeto</DialogTitle>
          <DialogDescription>O estilo do projeto é aplicado a toda geração (2D e 3D). Dá para refinar depois na aba Estilo.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) create.mutate()
          }}
        >
          <div className="grid gap-1.5">
            <Label htmlFor="pname">Nome</Label>
            <Input id="pname" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Meu Jogo" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="pstyle">Estilo visual</Label>
            <Textarea id="pstyle" rows={3} value={style} onChange={(e) => setStyle(e.target.value)} placeholder="16-bit pixel art, dark fantasy, muted palette, top-down view" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="pdesc">Descrição</Label>
            <Input id="pdesc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="opcional" />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!name.trim() || create.isPending}>Criar</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// "Novo sprite": cria um asset 2D em branco (tamanho e nº de quadros) e abre no editor de sprite.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { Loader2, PenTool } from 'lucide-react'
import { toast } from 'sonner'
import type { Project } from '@/core/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { blankDoc } from './doc'
import { saveAsNew } from './io'

const SIZES = [16, 32, 48, 64, 128, 256]

export function NewSpriteButton({ project, folder, className }: { project: Project; folder?: string; className?: string }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('sprite')
  const [w, setW] = useState(32)
  const [h, setH] = useState(32)
  const [frames, setFrames] = useState(1)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const nav = useNavigate()

  const create = async () => {
    setBusy(true)
    try {
      const v = await saveAsNew(project, blankDoc(w, h, frames), name.trim() || 'sprite', folder)
      qc.invalidateQueries({ queryKey: ['assets', project.slug] })
      setOpen(false)
      nav(`/p/${project.slug}/a/${v.slug}/${v.version}?tab=sprite`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const clamp = (v: string) => Math.max(1, Math.min(2048, Math.round(Number(v) || 1)))

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" variant="outline" className={className} title="Sprite em branco com camadas e quadros (editor estilo Aseprite)" />}>
        <PenTool /> Novo sprite
      </DialogTrigger>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Novo sprite</DialogTitle>
          <DialogDescription>Abre no editor com camadas, quadros e linha do tempo.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 text-sm">
          <label className="grid gap-1">Nome<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div className="grid grid-cols-3 gap-2">
            <label className="grid gap-1">Largura<Input type="number" value={w} onChange={(e) => setW(clamp(e.target.value))} /></label>
            <label className="grid gap-1">Altura<Input type="number" value={h} onChange={(e) => setH(clamp(e.target.value))} /></label>
            <label className="grid gap-1">Quadros<Input type="number" value={frames} onChange={(e) => setFrames(Math.min(256, clamp(e.target.value)))} /></label>
          </div>
          <div className="flex flex-wrap gap-1">
            {SIZES.map((s) => (
              <Button key={s} size="xs" variant={w === s && h === s ? 'secondary' : 'outline'} onClick={() => { setW(s); setH(s) }}>{s}×{s}</Button>
            ))}
          </div>
        </div>
        <DialogFooter>
          <Button onClick={create} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <PenTool />} Criar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

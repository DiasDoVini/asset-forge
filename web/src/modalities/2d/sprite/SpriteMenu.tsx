// Menu "Sprite": tamanho do canvas (com âncora), escalar, aparar ao conteúdo, cortar à seleção, espelhar e girar tudo.
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { anchorOffset, contentBounds, cropDoc, resizeCanvas, type SpriteDoc } from './doc'
import type { Box } from './selection'
import { flipSprite, rotateSprite, scaleSprite } from './spriteOps'

const MAX = 4096

export function SpriteMenu({ doc, commit, selection }: { doc: SpriteDoc; commit: (d: SpriteDoc) => void; selection: Box | null }) {
  const [dialog, setDialog] = useState<'canvas' | 'scale' | null>(null)
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button size="sm" variant="ghost">Sprite <ChevronDown /></Button>} />
        <DropdownMenuContent align="start" className="w-60">
          <DropdownMenuItem onClick={() => setDialog('canvas')}>Tamanho do canvas…</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setDialog('scale')}>Escalar sprite…</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => {
            const b = contentBounds(doc)
            if (!b) return toast.info('O sprite está vazio')
            commit(cropDoc(doc, b))
          }}>Aparar ao conteúdo</DropdownMenuItem>
          <DropdownMenuItem disabled={!selection} onClick={() => selection && commit(cropDoc(doc, selection))}>Cortar à seleção</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => commit(flipSprite(doc, 'h'))}>Espelhar na horizontal</DropdownMenuItem>
          <DropdownMenuItem onClick={() => commit(flipSprite(doc, 'v'))}>Espelhar na vertical</DropdownMenuItem>
          <DropdownMenuItem onClick={() => commit(rotateSprite(doc, 1))}>Girar 90° horário</DropdownMenuItem>
          <DropdownMenuItem onClick={() => commit(rotateSprite(doc, -1))}>Girar 90° anti-horário</DropdownMenuItem>
          <DropdownMenuItem onClick={() => commit(rotateSprite(doc, 2))}>Girar 180°</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {dialog === 'canvas' && <CanvasDialog doc={doc} onClose={() => setDialog(null)} onApply={(d) => { commit(d); setDialog(null) }} />}
      {dialog === 'scale' && <ScaleDialog doc={doc} onClose={() => setDialog(null)} onApply={(d) => { commit(d); setDialog(null) }} />}
    </>
  )
}

const clampSize = (v: string) => Math.max(1, Math.min(MAX, Math.round(Number(v) || 1)))

function CanvasDialog({ doc, onClose, onApply }: { doc: SpriteDoc; onClose: () => void; onApply: (d: SpriteDoc) => void }) {
  const [w, setW] = useState(doc.w)
  const [h, setH] = useState(doc.h)
  const [anchor, setAnchor] = useState<[number, number]>([1, 1])
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Tamanho do canvas</DialogTitle>
          <DialogDescription>Atual: {doc.w}×{doc.h}. Os pixels não são escalados; a âncora diz de que lado o canvas cresce ou é cortado.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-[1fr_auto] items-start gap-4 text-sm">
          <div className="grid gap-2">
            <label className="grid gap-1">Largura<Input type="number" value={w} onChange={(e) => setW(clampSize(e.target.value))} /></label>
            <label className="grid gap-1">Altura<Input type="number" value={h} onChange={(e) => setH(clampSize(e.target.value))} /></label>
          </div>
          <div>
            <div className="mb-1 text-xs text-muted-foreground">Âncora</div>
            <div className="grid grid-cols-3 gap-1">
              {[0, 1, 2].flatMap((ay) => [0, 1, 2].map((ax) => (
                <button
                  key={`${ax}${ay}`}
                  className={cn('size-7 rounded border', anchor[0] === ax && anchor[1] === ay ? 'border-primary bg-primary/40' : 'hover:bg-muted')}
                  onClick={() => setAnchor([ax, ay])}
                  title={['esquerda', 'centro', 'direita'][ax] + ' / ' + ['topo', 'meio', 'base'][ay]}
                />
              )))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => onApply(resizeCanvas(doc, w, h, ...anchorOffset(doc, w, h, anchor[0], anchor[1])))}>Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ScaleDialog({ doc, onClose, onApply }: { doc: SpriteDoc; onClose: () => void; onApply: (d: SpriteDoc) => void }) {
  const [w, setW] = useState(doc.w)
  const [h, setH] = useState(doc.h)
  const [lock, setLock] = useState(true)
  const setWidth = (v: number) => {
    setW(v)
    if (lock) setH(Math.max(1, Math.round((v * doc.h) / doc.w)))
  }
  const setHeight = (v: number) => {
    setH(v)
    if (lock) setW(Math.max(1, Math.round((v * doc.w) / doc.h)))
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Escalar sprite</DialogTitle>
          <DialogDescription>Todas as camadas e quadros, pelo vizinho mais próximo (sem borrar). Para pixel art, prefira múltiplos inteiros.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <label className="grid gap-1">Largura<Input type="number" value={w} onChange={(e) => setWidth(clampSize(e.target.value))} /></label>
            <label className="grid gap-1">Altura<Input type="number" value={h} onChange={(e) => setHeight(clampSize(e.target.value))} /></label>
          </div>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={lock} onChange={(e) => setLock(e.target.checked)} /> Manter proporção</label>
          <div className="flex flex-wrap gap-1">
            {[0.5, 2, 3, 4].map((k) => (
              <Button key={k} size="xs" variant="outline" onClick={() => { setW(Math.max(1, Math.round(doc.w * k))); setH(Math.max(1, Math.round(doc.h * k))) }}>{k * 100}%</Button>
            ))}
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => onApply(scaleSprite(doc, w, h))}>Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

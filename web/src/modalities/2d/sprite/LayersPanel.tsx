// Painel de camadas: visibilidade, bloqueio, renomear, grupos, ordem, opacidade, modo de mistura, mesclar.
import { useState } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Copy, Eye, EyeOff, FolderPlus, Grid2x2, Lock, LockOpen, Merge, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import {
  addLayer, BLENDS, depthOf, displayOrder, duplicateLayer, mergeDown, moveLayer, removeLayer, setParent, updateLayer,
  type Blend, type SpriteDoc,
} from './doc'
import { addTilemapLayer, convertToImage, convertToTilemap } from './tilemap'

export function LayersPanel({ doc, layer, onLayer, commit }: {
  doc: SpriteDoc
  layer: number
  onLayer: (id: number) => void
  commit: (d: SpriteDoc) => void
}) {
  const [renaming, setRenaming] = useState<number | null>(null)
  const [opacity, setOpacity] = useState<number | null>(null) // valor durante o arraste (entra no histórico ao soltar)
  const rows = displayOrder(doc)
  const cur = doc.layers.find((l) => l.id === layer)
  const groups = doc.layers.filter((l) => l.kind === 'group' && l.id !== layer)

  const add = (kind: 'image' | 'group') => {
    const r = addLayer(doc, layer, kind)
    commit(r.doc)
    onLayer(r.id)
  }

  return (
    <div className="flex shrink-0 flex-col gap-2">
      <div className="flex items-center gap-0.5">
        <span className="mr-auto text-xs font-medium">Camadas</span>
        <Button size="icon-xs" variant="ghost" title="Nova camada (Shift+N)" onClick={() => add('image')}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" title="Novo grupo" onClick={() => add('group')}><FolderPlus /></Button>
        <Button size="icon-xs" variant="ghost" title={`Nova camada de tilemap (tiles ${doc.grid.w}×${doc.grid.h}; mude em Exibir → Configurar grade)`} onClick={() => {
          const r = addTilemapLayer(doc, layer, doc.grid.w, doc.grid.h)
          commit(r.doc)
          onLayer(r.id)
        }}><Grid2x2 /></Button>
        <Button size="icon-xs" variant="ghost" title="Duplicar camada" disabled={!cur || cur.kind === 'group'} onClick={() => {
          const r = duplicateLayer(doc, layer)
          commit(r.doc)
          onLayer(r.id)
        }}><Copy /></Button>
        <Button size="icon-xs" variant="ghost" title="Subir" onClick={() => commit(moveLayer(doc, layer, 1))}><ArrowUp /></Button>
        <Button size="icon-xs" variant="ghost" title="Descer" onClick={() => commit(moveLayer(doc, layer, -1))}><ArrowDown /></Button>
        <Button size="icon-xs" variant="ghost" title="Mesclar com a de baixo" disabled={cur?.kind !== 'image'} onClick={() => commit(mergeDown(doc, layer))}><Merge /></Button>
        <Button size="icon-xs" variant="ghost" title="Apagar camada" onClick={() => {
          const next = removeLayer(doc, layer)
          if (next === doc) return
          commit(next)
          const first = displayOrder(next).find((l) => l.kind === 'image')
          if (first) onLayer(first.id)
        }}><Trash2 /></Button>
      </div>

      <div className="max-h-56 min-h-14 overflow-y-auto rounded-md border">
        {rows.map((l) => (
          <div
            key={l.id}
            onClick={() => onLayer(l.id)}
            className={cn('flex h-7 cursor-pointer items-center gap-1 border-b px-1 text-xs last:border-b-0', l.id === layer ? 'bg-primary/15' : 'hover:bg-muted/60')}
            style={{ paddingLeft: 4 + depthOf(doc, l) * 12 }}
          >
            {l.kind === 'group' ? (
              <button className="text-muted-foreground" onClick={(e) => { e.stopPropagation(); commit(updateLayer(doc, l.id, { collapsed: !l.collapsed })) }}>
                {l.collapsed ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
              </button>
            ) : <span className="w-3.5" />}
            <button title={l.visible ? 'Ocultar' : 'Mostrar'} className={cn(!l.visible && 'text-muted-foreground')} onClick={(e) => { e.stopPropagation(); commit(updateLayer(doc, l.id, { visible: !l.visible })) }}>
              {l.visible ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
            </button>
            <button title={l.locked ? 'Desbloquear' : 'Bloquear'} className={cn(!l.locked && 'text-muted-foreground/50')} onClick={(e) => { e.stopPropagation(); commit(updateLayer(doc, l.id, { locked: !l.locked })) }}>
              {l.locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
            </button>
            {renaming === l.id ? (
              <Input
                autoFocus
                defaultValue={l.name}
                className="h-5 flex-1 px-1 text-xs"
                onClick={(e) => e.stopPropagation()}
                onBlur={(e) => { commit(updateLayer(doc, l.id, { name: e.target.value.trim() || l.name })); setRenaming(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setRenaming(null) }}
              />
            ) : (
              <span className={cn('flex-1 truncate', l.kind === 'group' && 'font-medium')} onDoubleClick={() => setRenaming(l.id)} title="Duplo clique para renomear">
                {l.name}
              </span>
            )}
            {l.kind === 'tilemap' && <span className="text-[10px] text-sky-400" title="Camada de tilemap">▦</span>}
            {l.clip && <span className="text-[10px] text-amber-400" title="Máscara de recorte na camada de baixo">↧</span>}
            {!!l.effects?.some((e) => e.enabled) && <span className="text-[10px] text-fuchsia-400" title="Tem efeitos">fx</span>}
            {l.opacity < 255 && <span className="text-[10px] text-muted-foreground tabular-nums">{Math.round((l.opacity / 255) * 100)}%</span>}
            {l.blend !== 'normal' && <span className="text-[10px] text-muted-foreground">{BLENDS.find((b) => b.id === l.blend)?.title}</span>}
          </div>
        ))}
      </div>

      {cur && (
        <div className="space-y-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="w-16 text-muted-foreground">Opacidade</span>
            <Slider
              className="flex-1"
              min={0}
              max={255}
              value={[opacity ?? cur.opacity]}
              onValueChange={(v) => setOpacity(Array.isArray(v) ? v[0] : (v as number))}
              onValueCommitted={(v) => {
                commit(updateLayer(doc, cur.id, { opacity: Array.isArray(v) ? v[0] : (v as number) }))
                setOpacity(null)
              }}
            />
            <span className="w-8 text-right tabular-nums">{Math.round(((opacity ?? cur.opacity) / 255) * 100)}%</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-16 text-muted-foreground">Mistura</span>
            <select className="h-7 flex-1 rounded-md border bg-transparent px-1" value={cur.blend} onChange={(e) => commit(updateLayer(doc, cur.id, { blend: e.target.value as Blend }))}>
              {BLENDS.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
            </select>
          </div>
          {cur.kind === 'image' && (
            <Button size="xs" variant="ghost" className="w-full justify-start" title={`Recorta a camada em tiles ${doc.grid.w}×${doc.grid.h} (grade do sprite), reaproveitando os iguais`} onClick={() => commit(convertToTilemap(doc, cur.id, doc.grid.w, doc.grid.h))}>
              <Grid2x2 /> Converter em tilemap ({doc.grid.w}×{doc.grid.h})
            </Button>
          )}
          {cur.kind === 'tilemap' && (
            <Button size="xs" variant="ghost" className="w-full justify-start" onClick={() => commit(convertToImage(doc, cur.id))}>Converter em imagem</Button>
          )}
          <div className="flex items-center gap-2">
            <span className="w-16 text-muted-foreground">Grupo</span>
            <select className="h-7 flex-1 rounded-md border bg-transparent px-1" value={cur.parent ?? ''} onChange={(e) => commit(setParent(doc, cur.id, e.target.value ? Number(e.target.value) : null))}>
              <option value="">(raiz)</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </div>
        </div>
      )}
    </div>
  )
}

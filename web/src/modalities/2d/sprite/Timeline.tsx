// Linha do tempo: quadros (colunas) × camadas (linhas), cels (● com conteúdo, ○ vazio, ligados unidos por uma barra),
// tags coloridas acima dos quadros, duração por quadro, ligar/desligar cels, play/loop e onion skin.
import { useState } from 'react'
import {
  ChevronLeft, ChevronRight, Copy, Layers2, Link2, Link2Off, Pause, Play, Plus, Repeat, SkipBack, SkipForward, Tag as TagIcon, Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  addTag, displayOrder, getCel, insertFrame, isEmptyCel, linkCels, moveFrame, removeFrame, removeTag, setDuration, unlinkCel,
  updateTag, type SpriteDoc, type TagDir,
} from './doc'

export type Onion = { on: boolean; before: number; after: number; opacity: number }

const DIR_TITLES: Record<TagDir, string> = { forward: 'Para frente', reverse: 'Ao contrário', pingpong: 'Ping-pong', pingpong_reverse: 'Ping-pong ao contrário' }

export function Timeline({
  doc, layer, frame, range, onSelect, commit, playing, onPlay, loopTag, setLoopTag, onion, setOnion, paintFollow, setPaintFollow,
}: {
  doc: SpriteDoc
  layer: number
  frame: number
  range: [number, number] // quadros selecionados (inclusivo)
  onSelect: (layer: number, frame: number, extend?: boolean) => void
  commit: (d: SpriteDoc, frame?: number) => void
  playing: boolean
  onPlay: () => void
  loopTag: number | null
  setLoopTag: (i: number | null) => void
  onion: Onion
  setOnion: (o: Onion) => void
  paintFollow?: boolean
  setPaintFollow?: (b: boolean) => void
}) {
  const rows = displayOrder(doc)
  const [lo, hi] = [Math.min(...range), Math.max(...range)]
  const selFrames = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  const [editTag, setEditTag] = useState<number | null>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const totalMs = doc.frames.reduce((s, f) => s + f.duration, 0)
  const CELL = 26

  return (
    <div className="flex min-h-0 flex-col border-t text-xs">
      {/* barra de controles */}
      <div className="flex flex-wrap items-center gap-0.5 border-b px-2 py-1">
        <Button size="icon-xs" variant="ghost" title="Primeiro quadro (Home)" onClick={() => onSelect(layer, 0)}><SkipBack /></Button>
        <Button size="icon-xs" variant="ghost" title="Quadro anterior (,)" onClick={() => onSelect(layer, Math.max(0, frame - 1))}><ChevronLeft /></Button>
        <Button size="icon-xs" variant={playing ? 'secondary' : 'ghost'} title="Tocar/pausar (Enter)" onClick={onPlay}>{playing ? <Pause /> : <Play />}</Button>
        <Button size="icon-xs" variant="ghost" title="Próximo quadro (.)" onClick={() => onSelect(layer, Math.min(doc.frames.length - 1, frame + 1))}><ChevronRight /></Button>
        <Button size="icon-xs" variant="ghost" title="Último quadro (End)" onClick={() => onSelect(layer, doc.frames.length - 1)}><SkipForward /></Button>
        {setPaintFollow && (
          <Button
            size="xs"
            variant={paintFollow ? 'secondary' : 'ghost'}
            title={paintFollow ? 'Tocando: o traço continua em cada quadro que passa' : 'Tocando: o traço fica no quadro onde começou (clique para pintar em cada quadro)'}
            onClick={() => setPaintFollow(!paintFollow)}
          >
            ✎ {paintFollow ? 'cada quadro' : '1 quadro'}
          </Button>
        )}
        <select className="ml-1 h-6 rounded border bg-transparent px-1" value={loopTag ?? ''} onChange={(e) => setLoopTag(e.target.value === '' ? null : Number(e.target.value))} title="O que tocar">
          <option value="">Tocar tudo</option>
          {doc.tags.map((t, i) => <option key={i} value={i}>Tag: {t.name}</option>)}
        </select>

        <span className="mx-2 h-4 border-l" />
        <Button size="icon-xs" variant="ghost" title="Novo quadro vazio (Alt+N)" onClick={() => commit(insertFrame(doc, frame, 'empty'), frame + 1)}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" title="Duplicar quadro (Alt+D)" onClick={() => commit(insertFrame(doc, frame, 'duplicate'), frame + 1)}><Copy /></Button>
        <Button size="icon-xs" variant="ghost" title="Duplicar como cels ligados" onClick={() => commit(insertFrame(doc, frame, 'linked'), frame + 1)}><Repeat /></Button>
        <Button size="icon-xs" variant="ghost" title="Apagar quadro" disabled={doc.frames.length <= 1} onClick={() => commit(removeFrame(doc, frame), Math.max(0, frame - 1))}><Trash2 /></Button>
        <Button size="icon-xs" variant="ghost" title="Mover quadro para a esquerda" disabled={frame === 0} onClick={() => commit(moveFrame(doc, frame, frame - 1), frame - 1)}><ChevronLeft className="opacity-60" /></Button>
        <Button size="icon-xs" variant="ghost" title="Mover quadro para a direita" disabled={frame >= doc.frames.length - 1} onClick={() => commit(moveFrame(doc, frame, frame + 1), frame + 1)}><ChevronRight className="opacity-60" /></Button>

        <span className="mx-2 h-4 border-l" />
        <Button size="icon-xs" variant="ghost" title="Ligar os cels da camada nos quadros selecionados (Shift+clique para selecionar vários)" disabled={selFrames.length < 2} onClick={() => commit(linkCels(doc, layer, selFrames))}><Link2 /></Button>
        <Button size="icon-xs" variant="ghost" title="Desligar o cel" onClick={() => commit(unlinkCel(doc, layer, frame))}><Link2Off /></Button>
        <Button size="icon-xs" variant="ghost" title="Nova tag com os quadros selecionados" onClick={() => { commit(addTag(doc, lo, hi)); setEditTag(doc.tags.length) }}><TagIcon /></Button>

        <span className="mx-2 h-4 border-l" />
        <label className="flex items-center gap-1" title="Duração dos quadros selecionados">
          <span className="text-muted-foreground">Duração</span>
          <Input
            key={`${lo}-${hi}-${doc.frames[lo]?.duration}`}
            type="number"
            min={1}
            className="h-6 w-16 px-1 text-xs"
            defaultValue={doc.frames[lo]?.duration}
            onBlur={(e) => commit(setDuration(doc, selFrames, Number(e.target.value) || 100))}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          />
          <span className="text-muted-foreground">ms</span>
        </label>

        <span className="mx-2 h-4 border-l" />
        <Button size="xs" variant={onion.on ? 'secondary' : 'ghost'} title="Onion skin (F3)" onClick={() => setOnion({ ...onion, on: !onion.on })}><Layers2 /> Onion</Button>
        {onion.on && (
          <span className="flex items-center gap-1 text-muted-foreground">
            <span className="text-red-400">◀</span>
            <Input type="number" min={0} max={8} className="h-6 w-10 px-1 text-xs" value={onion.before} onChange={(e) => setOnion({ ...onion, before: Math.max(0, Math.min(8, Number(e.target.value) || 0)) })} />
            <Input type="number" min={0} max={8} className="h-6 w-10 px-1 text-xs" value={onion.after} onChange={(e) => setOnion({ ...onion, after: Math.max(0, Math.min(8, Number(e.target.value) || 0)) })} />
            <span className="text-sky-400">▶</span>
            <Input type="number" min={5} max={90} className="h-6 w-12 px-1 text-xs" value={Math.round(onion.opacity * 100)} onChange={(e) => setOnion({ ...onion, opacity: Math.max(0.05, Math.min(0.9, (Number(e.target.value) || 30) / 100)) })} title="Opacidade %" />
          </span>
        )}
        <span className="ml-auto text-muted-foreground tabular-nums">
          {frame + 1}/{doc.frames.length} · {(totalMs / 1000).toFixed(2)} s
        </span>
      </div>

      {editTag !== null && doc.tags[editTag] && (
        <div className="flex items-center gap-2 border-b bg-muted/30 px-2 py-1">
          <span className="text-muted-foreground">Tag</span>
          <TagEditor
            key={editTag}
            tag={doc.tags[editTag]}
            max={doc.frames.length - 1}
            onChange={(patch) => commit(updateTag(doc, editTag, patch))}
            onRemove={() => { commit(removeTag(doc, editTag)); setEditTag(null); if (loopTag === editTag) setLoopTag(null) }}
          />
          <Button size="xs" variant="ghost" className="ml-auto" onClick={() => setEditTag(null)}>Fechar</Button>
        </div>
      )}

      {/* grade */}
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="inline-grid min-w-full" style={{ gridTemplateColumns: `160px repeat(${doc.frames.length}, ${CELL}px)` }}>
          {/* tags */}
          <div className="sticky left-0 z-10 border-b bg-background px-2 py-0.5 text-[10px] text-muted-foreground">Tags</div>
          <div className="relative h-5 border-b" style={{ gridColumn: `2 / span ${doc.frames.length}` }}>
            {doc.tags.map((t, i) => (
              <button
                key={i}
                className="absolute top-0.5 h-4 truncate rounded px-1 text-left text-[10px] font-medium text-black"
                style={{ left: t.from * CELL + 1, width: (t.to - t.from + 1) * CELL - 2, background: t.color }}
                title={`${t.name} (${DIR_TITLES[t.direction]}) — clique para editar`}
                onClick={() => setEditTag(i)}
              >
                {t.name}
              </button>
            ))}
          </div>

          {/* números dos quadros */}
          <div className="sticky left-0 z-10 border-b bg-background px-2 py-0.5 text-[10px] text-muted-foreground">Quadro</div>
          {doc.frames.map((f, i) => (
            <button
              key={i}
              draggable
              onDragStart={() => setDrag(i)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => { if (drag !== null && drag !== i) commit(moveFrame(doc, drag, i), i); setDrag(null) }}
              onClick={(e) => onSelect(layer, i, e.shiftKey)}
              className={cn('border-b border-l py-0.5 text-center text-[10px] tabular-nums', i >= lo && i <= hi ? 'bg-primary/20' : 'hover:bg-muted/60', i === frame && 'font-bold text-primary')}
              title={`${f.duration} ms — arraste para mover`}
            >
              {i + 1}
            </button>
          ))}

          {/* camadas × quadros */}
          {rows.map((l) => (
            <Row key={l.id} doc={doc} layerId={l.id} active={l.id === layer} frame={frame} lo={lo} hi={hi} onSelect={onSelect} />
          ))}
        </div>
      </div>
    </div>
  )
}

function Row({ doc, layerId, active, frame, lo, hi, onSelect }: {
  doc: SpriteDoc; layerId: number; active: boolean; frame: number; lo: number; hi: number
  onSelect: (layer: number, frame: number, extend?: boolean) => void
}) {
  const l = doc.layers.find((x) => x.id === layerId)!
  const depth = (() => { let d = 0, p = l.parent; while (p !== null) { d++; p = doc.layers.find((x) => x.id === p)?.parent ?? null } return d })()
  return (
    <>
      <div
        className={cn('sticky left-0 z-10 flex h-6 cursor-pointer items-center truncate border-b bg-background px-2', active && 'bg-primary/15 font-medium', !l.visible && 'text-muted-foreground')}
        style={{ paddingLeft: 8 + depth * 10 }}
        onClick={() => onSelect(l.id, frame)}
      >
        {l.kind === 'group' ? '▸ ' : ''}{l.name}
      </div>
      {doc.frames.map((_, f) => {
        if (l.kind === 'group') return <div key={f} className="h-6 border-b border-l bg-muted/20" />
        const c = getCel(doc, l.id, f)
        const prev = f > 0 ? getCel(doc, l.id, f - 1) : undefined
        const next = f < doc.frames.length - 1 ? getCel(doc, l.id, f + 1) : undefined
        const linkL = !!c && c === prev
        const linkR = !!c && c === next
        const empty = isEmptyCel(c)
        return (
          <button
            key={f}
            onClick={(e) => onSelect(l.id, f, e.shiftKey)}
            className={cn(
              'relative flex h-6 items-center justify-center border-b border-l',
              f >= lo && f <= hi && active ? 'bg-primary/25' : f >= lo && f <= hi ? 'bg-primary/10' : 'hover:bg-muted/60',
              f === frame && active && 'ring-1 ring-inset ring-primary',
            )}
            title={c ? (linkL || linkR ? 'Cel ligado' : 'Cel') : 'Vazio'}
          >
            {(linkL || linkR) && <span className={cn('absolute top-1/2 h-0.5 -translate-y-1/2 bg-primary/70', linkL ? 'left-0' : 'left-1/2', linkR ? 'right-0' : 'right-1/2')} />}
            <span className={cn('relative size-2 rounded-full', empty ? 'border border-muted-foreground/60' : 'bg-foreground')} />
          </button>
        )
      })}
    </>
  )
}

function TagEditor({ tag, max, onChange, onRemove }: {
  tag: SpriteDoc['tags'][number]; max: number
  onChange: (p: Partial<SpriteDoc['tags'][number]>) => void; onRemove: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Input className="h-6 w-32 px-1 text-xs" defaultValue={tag.name} onBlur={(e) => onChange({ name: e.target.value.trim() || tag.name })} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} title="Nome" />
      <label className="flex items-center gap-1 text-muted-foreground">De
        <Input key={`f${tag.from}`} type="number" min={1} max={max + 1} className="h-6 w-12 px-1 text-xs" defaultValue={tag.from + 1} onBlur={(e) => onChange({ from: Math.max(0, Math.min(max, (Number(e.target.value) || 1) - 1)) })} />
      </label>
      <label className="flex items-center gap-1 text-muted-foreground">até
        <Input key={`t${tag.to}`} type="number" min={1} max={max + 1} className="h-6 w-12 px-1 text-xs" defaultValue={tag.to + 1} onBlur={(e) => onChange({ to: Math.max(0, Math.min(max, (Number(e.target.value) || 1) - 1)) })} />
      </label>
      <select className="h-6 rounded-md border bg-transparent px-1" value={tag.direction} onChange={(e) => onChange({ direction: e.target.value as TagDir })}>
        {(Object.keys(DIR_TITLES) as TagDir[]).map((d) => <option key={d} value={d}>{DIR_TITLES[d]}</option>)}
      </select>
      <label className="flex items-center gap-1 text-muted-foreground" title="0 = infinito">Repetir
        <Input type="number" min={0} className="h-6 w-12 px-1 text-xs" defaultValue={tag.repeat} onBlur={(e) => onChange({ repeat: Math.max(0, Number(e.target.value) || 0) })} />
      </label>
      <input type="color" className="h-6 w-7 cursor-pointer rounded border bg-transparent" value={tag.color} onChange={(e) => onChange({ color: e.target.value })} title="Cor" />
      <Button size="xs" variant="ghost" className="text-destructive" onClick={onRemove}><Trash2 /> Remover</Button>
    </div>
  )
}

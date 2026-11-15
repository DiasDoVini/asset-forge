// Painéis laterais da Fase 5: fatias (9-slice, pivô) e tileset da camada de tilemap (tiles, modo de edição, exportar).
import { useEffect, useRef, useState } from 'react'
import { Copy, Download, Trash2 } from 'lucide-react'
import { downloadBlob } from '@/core/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { Layer, Slice, SpriteDoc, Tileset } from './doc'
import { pruneTileset, replaceTileset, type PixelMode } from './tilemap'

// ---------------------------------------------------------------- fatias

export function SlicePanel({ doc, commit, selected, setSelected }: {
  doc: SpriteDoc
  commit: (d: SpriteDoc) => void
  selected: number | null
  setSelected: (i: number | null) => void
}) {
  const s = selected !== null ? doc.slices[selected] : undefined
  const patch = (p: Partial<Slice>) => selected !== null && commit({ ...doc, slices: doc.slices.map((x, i) => (i === selected ? { ...x, ...p } : x)) })
  const n = (v: string) => Math.round(Number(v) || 0)
  const box = (label: string, val: number, on: (v: number) => void) => (
    <label className="grid gap-0.5 text-[10px] text-muted-foreground">{label}
      <Input key={`${label}${val}`} type="number" className="h-6 px-1 text-xs" defaultValue={val} onBlur={(e) => on(n(e.target.value))} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
    </label>
  )
  return (
    <div className="shrink-0 space-y-2 border-t pt-2">
      <div className="flex items-center text-xs font-medium">Fatias <span className="ml-1 font-normal text-muted-foreground">({doc.slices.length})</span></div>
      {doc.slices.length === 0 && <p className="text-[11px] text-muted-foreground">Use a ferramenta Fatia (Shift+C) e arraste no canvas.</p>}
      <div className="space-y-0.5">
        {doc.slices.map((sl, i) => (
          <button key={i} className={cn('flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-xs', i === selected ? 'bg-primary/15' : 'hover:bg-muted/60')} onClick={() => setSelected(i)}>
            <span className="size-2.5 rounded-sm" style={{ background: sl.color }} />
            <span className="flex-1 truncate">{sl.name}</span>
            <span className="text-[10px] text-muted-foreground tabular-nums">{sl.w}×{sl.h}{sl.center ? ' · 9' : ''}{sl.pivot ? ' · ⊕' : ''}</span>
          </button>
        ))}
      </div>
      {s && (
        <div className="space-y-1.5 rounded-md border p-2 text-xs">
          <div className="flex items-center gap-1">
            <Input key={s.name} className="h-6 flex-1 px-1 text-xs" defaultValue={s.name} onBlur={(e) => patch({ name: e.target.value.trim() || s.name })} />
            <input type="color" className="h-6 w-7 rounded border bg-transparent" value={s.color} onChange={(e) => patch({ color: e.target.value })} />
            <Button size="icon-xs" variant="ghost" title="Apagar fatia" onClick={() => { commit({ ...doc, slices: doc.slices.filter((_, i) => i !== selected) }); setSelected(null) }}><Trash2 /></Button>
          </div>
          <div className="grid grid-cols-4 gap-1">
            {box('X', s.x, (x) => patch({ x }))}
            {box('Y', s.y, (y) => patch({ y }))}
            {box('L', s.w, (w) => patch({ w: Math.max(1, w) }))}
            {box('A', s.h, (h) => patch({ h: Math.max(1, h) }))}
          </div>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={!!s.center} onChange={(e) => patch({ center: e.target.checked ? { x: Math.floor(s.w / 3), y: Math.floor(s.h / 3), w: Math.max(1, s.w - 2 * Math.floor(s.w / 3)), h: Math.max(1, s.h - 2 * Math.floor(s.h / 3)) } : undefined })} />
            9-slice (centro esticável)
          </label>
          {s.center && (
            <div className="grid grid-cols-4 gap-1">
              {box('cX', s.center.x, (x) => patch({ center: { ...s.center!, x } }))}
              {box('cY', s.center.y, (y) => patch({ center: { ...s.center!, y } }))}
              {box('cL', s.center.w, (w) => patch({ center: { ...s.center!, w: Math.max(1, w) } }))}
              {box('cA', s.center.h, (h) => patch({ center: { ...s.center!, h: Math.max(1, h) } }))}
            </div>
          )}
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={!!s.pivot} onChange={(e) => patch({ pivot: e.target.checked ? { x: Math.floor(s.w / 2), y: s.h - 1 } : undefined })} />
            Pivô
          </label>
          {s.pivot && (
            <div className="grid grid-cols-4 gap-1">
              {box('pX', s.pivot.x, (x) => patch({ pivot: { ...s.pivot!, x } }))}
              {box('pY', s.pivot.y, (y) => patch({ pivot: { ...s.pivot!, y } }))}
            </div>
          )}
          <p className="text-[10px] text-muted-foreground">Vão no .aseprite e no JSON do sprite sheet (formato Aseprite).</p>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- tileset

function TileThumb({ tile, ts, size, selected, onClick }: { tile: Uint8ClampedArray; ts: Tileset; size: number; selected: boolean; onClick: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    c.width = ts.tw
    c.height = ts.th
    c.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(tile), ts.tw, ts.th), 0, 0)
  }, [tile, ts.tw, ts.th])
  return (
    <button className={cn('rounded-sm border bg-[repeating-conic-gradient(#333_0_25%,#444_0_50%)] bg-[length:8px_8px] p-px', selected ? 'border-primary ring-1 ring-primary' : 'border-black/40')} onClick={onClick}>
      <canvas ref={ref} style={{ width: size, height: (size * ts.th) / ts.tw, imageRendering: 'pixelated', display: 'block' }} />
    </button>
  )
}

export function TilesetPanel({ doc, layer, commit, tile, setTile, tileMode, setTileMode, pixelMode, setPixelMode }: {
  doc: SpriteDoc
  layer: Layer
  commit: (d: SpriteDoc) => void
  tile: number
  setTile: (i: number) => void
  tileMode: boolean
  setTileMode: (b: boolean) => void
  pixelMode: PixelMode
  setPixelMode: (m: PixelMode) => void
}) {
  const ts = doc.tilesets.find((t) => t.id === layer.tileset)
  const [zoom, setZoom] = useState(2)
  if (!ts) return null
  const exportPng = () => {
    const cols = Math.min(16, ts.tiles.length)
    const rows = Math.ceil(ts.tiles.length / cols)
    const c = document.createElement('canvas')
    c.width = cols * ts.tw
    c.height = rows * ts.th
    const g = c.getContext('2d')!
    ts.tiles.forEach((t, i) => g.putImageData(new ImageData(new Uint8ClampedArray(t), ts.tw, ts.th), (i % cols) * ts.tw, ((i / cols) | 0) * ts.th))
    c.toBlob((b) => b && downloadBlob(b, `${ts.name.replace(/\s+/g, '_')}.png`), 'image/png')
    return { cols, rows }
  }
  const exportTiled = () => {
    const { cols } = exportPng()
    const tm = doc.cels[`${layer.id}:0`]?.tilemap
    const map = {
      type: 'map', version: '1.10', orientation: 'orthogonal', renderorder: 'right-down', infinite: false,
      width: tm?.cols ?? Math.ceil(doc.w / ts.tw), height: tm?.rows ?? Math.ceil(doc.h / ts.th), tilewidth: ts.tw, tileheight: ts.th,
      layers: doc.layers.filter((l) => l.kind === 'tilemap' && l.tileset === ts.id).map((l, i) => {
        const t = doc.cels[`${l.id}:0`]?.tilemap
        return { id: i + 1, name: l.name, type: 'tilelayer', visible: l.visible, opacity: l.opacity / 255, x: 0, y: 0, width: t?.cols ?? 0, height: t?.rows ?? 0, data: t ? Array.from(t.tiles) : [] }
      }),
      tilesets: [{ firstgid: 1, name: ts.name, image: `${ts.name.replace(/\s+/g, '_')}.png`, tilewidth: ts.tw, tileheight: ts.th, tilecount: ts.tiles.length, columns: cols, imagewidth: cols * ts.tw, imageheight: Math.ceil(ts.tiles.length / cols) * ts.th, margin: 0, spacing: 0 }],
      // no Tiled o gid 0 é vazio e o 1º tile da imagem tem gid 1: como o tile 0 daqui também é vazio, os índices batem com gid-1
      nextlayerid: 100, nextobjectid: 1, tiledversion: '1.10.2',
    }
    // índices daqui (0 = vazio, n = tile n) viram gids do Tiled (0 = vazio, n+1 = tile n da imagem)
    map.layers.forEach((l) => (l.data = l.data.map((v: number) => (v ? v + 1 : 0))))
    downloadBlob(new Blob([JSON.stringify(map, null, 1)], { type: 'application/json' }), `${layer.name.replace(/\s+/g, '_')}.tmj`)
  }
  return (
    <div className="shrink-0 space-y-2 border-t pt-2">
      <div className="flex items-center gap-1 text-xs">
        <span className="mr-auto font-medium">{ts.name} <span className="font-normal text-muted-foreground">({ts.tiles.length - 1} tiles, {ts.tw}×{ts.th})</span></span>
        <Button size="icon-xs" variant="ghost" title="Menor" onClick={() => setZoom(Math.max(1, zoom - 1))}>−</Button>
        <Button size="icon-xs" variant="ghost" title="Maior" onClick={() => setZoom(Math.min(6, zoom + 1))}>+</Button>
      </div>
      <div className="flex gap-1">
        <Button size="xs" variant={tileMode ? 'outline' : 'secondary'} onClick={() => setTileMode(false)} title="Pintar pixels (Tab alterna)">Pixels</Button>
        <Button size="xs" variant={tileMode ? 'secondary' : 'outline'} onClick={() => setTileMode(true)} title="Colocar tiles com lápis, borracha, balde e conta-gotas (Tab alterna)">Tiles</Button>
        {!tileMode && (
          <select className="ml-auto h-6 rounded border bg-transparent px-1 text-xs" value={pixelMode} onChange={(e) => setPixelMode(e.target.value as PixelMode)} title="O que acontece ao pintar pixels num tile">
            <option value="auto">Auto (tile novo)</option>
            <option value="manual">Manual (muda todas as cópias)</option>
          </select>
        )}
      </div>
      <div className="flex max-h-48 flex-wrap gap-0.5 overflow-y-auto">
        {ts.tiles.map((t, i) => (
          <TileThumb key={i} tile={t} ts={ts} size={Math.max(12, ts.tw * zoom)} selected={i === tile} onClick={() => { setTile(i); setTileMode(true) }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        <Button size="xs" variant="ghost" title="Duplicar o tile selecionado" disabled={tile <= 0} onClick={() => {
          commit(replaceTileset(doc, { ...ts, tiles: [...ts.tiles, ts.tiles[tile].slice()] }))
          setTile(ts.tiles.length)
        }}><Copy /> Duplicar</Button>
        <Button size="xs" variant="ghost" title="Remove tiles que nenhum cel usa" onClick={() => commit(pruneTileset(doc, ts.id))}>Limpar sem uso</Button>
        <Button size="xs" variant="ghost" title="Baixa o tileset em PNG" onClick={exportPng}><Download /> PNG</Button>
        <Button size="xs" variant="ghost" title="Baixa o mapa para o Tiled (.tmj) e o PNG do tileset" onClick={exportTiled}><Download /> Tiled</Button>
      </div>
    </div>
  )
}

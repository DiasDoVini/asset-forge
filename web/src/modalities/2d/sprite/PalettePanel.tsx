// Painel de paleta: amostras (esquerdo = cor principal, direito = secundária, Shift = intervalo), editar a cor
// selecionada, adicionar/remover/mover, ordenar, rampa, extrair do sprite, reduzir, paletas prontas, paleta do projeto
// e importar/exportar (.gpl, .pal, .hex, .txt, .ase, PNG, .aseprite).
import { useState } from 'react'
import { ArrowLeft, ArrowRight, ChevronDown, Minus, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { downloadBlob } from '@/core/api'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent,
  DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { hex, parseHex, ramp, reducePalette, sortPalette, uniqueColors, type SortKey } from './color'
import { removePaletteColors, replacePalette, setPaletteColor } from './colorOps'
import type { SpriteDoc } from './doc'
import { exportPalette, PRESETS, readPalette, type PaletteFormat } from './paletteIO'
import type { RGBA } from './raster'

const css = (c: RGBA) => `rgba(${c[0]},${c[1]},${c[2]},${c[3] / 255})`

export function PalettePanel({ doc, commit, fg, setFg, setBg, projectPalette, saveProjectPalette }: {
  doc: SpriteDoc
  commit: (d: SpriteDoc) => void
  fg: RGBA
  setFg: (c: RGBA) => void
  setBg: (c: RGBA) => void
  projectPalette: RGBA[]
  saveProjectPalette: (p: RGBA[]) => Promise<void>
}) {
  const [sel, setSel] = useState<[number, number] | null>(null)
  const pal = doc.palette
  const lo = sel ? Math.min(...sel) : -1
  const hi = sel ? Math.max(...sel) : -1
  const cur = sel ? pal[sel[1]] : undefined
  const setPal = (p: RGBA[], remap = true) => commit(replacePalette(doc, p, remap))
  const allCels = () => [...new Set(Object.values(doc.cels))].map((c) => c.data)

  const importFile = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = '.gpl,.pal,.hex,.txt,.ase,.aseprite,.png,.gif,.bmp,.webp,image/*'
    inp.onchange = async () => {
      const f = inp.files?.[0]
      if (!f) return
      try {
        const p = await readPalette(f)
        setPal(p)
        toast.success(`${p.length} cores de ${f.name}`)
      } catch (e) {
        toast.error(`Não consegui ler a paleta: ${e instanceof Error ? e.message : e}`)
      }
    }
    inp.click()
  }
  const exportAs = async (fmt: PaletteFormat) => {
    if (!pal.length) return toast.info('A paleta está vazia')
    const { blob, filename } = await exportPalette(pal, fmt, 'paleta')
    downloadBlob(blob, filename)
  }
  const move = (dir: -1 | 1) => {
    if (!sel || lo + dir < 0 || hi + dir >= pal.length) return
    const p = [...pal]
    const block = p.splice(lo, hi - lo + 1)
    p.splice(lo + dir, 0, ...block)
    setPal(p, false)
    setSel([sel[0] + dir, sel[1] + dir])
  }
  const makeRamp = () => {
    if (!sel || hi - lo < 2) return toast.info('Selecione um intervalo de 3+ cores (Shift+clique): as pontas viram a rampa')
    const r = ramp(pal[lo], pal[hi], hi - lo + 1)
    setPal([...pal.slice(0, lo), ...r, ...pal.slice(hi + 1)])
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        <span className="mr-auto text-xs font-medium">
          Paleta <span className="font-normal text-muted-foreground">({pal.length}{doc.colorMode === 'indexed' ? ', indexado' : doc.colorMode === 'gray' ? ', cinza' : ''})</span>
        </span>
        <Button size="icon-xs" variant="ghost" title="Adicionar a cor principal" disabled={pal.length >= 256} onClick={() => { setPal([...pal, fg], false); setSel([pal.length, pal.length]) }}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" title="Remover as selecionadas (no indexado, os pixels vão para a cor mais próxima)" disabled={!sel} onClick={() => {
          commit(removePaletteColors(doc, Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)))
          setSel(null)
        }}><Minus /></Button>
        <Button size="icon-xs" variant="ghost" title="Mover para a esquerda" disabled={!sel || lo === 0} onClick={() => move(-1)}><ArrowLeft /></Button>
        <Button size="icon-xs" variant="ghost" title="Mover para a direita" disabled={!sel || hi >= pal.length - 1} onClick={() => move(1)}><ArrowRight /></Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="xs" variant="ghost">Mais <ChevronDown /></Button>} />
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuItem onClick={importFile}>Importar paleta… (.gpl .pal .hex .txt .ase PNG .aseprite)</DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Exportar paleta</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {([['gpl', 'GIMP (.gpl)'], ['pal', 'JASC (.pal)'], ['hex', 'Lospec (.hex)'], ['txt', 'Paint.NET (.txt)'], ['ase', 'Adobe (.ase)'], ['png', 'PNG (1 px por cor)']] as const).map(([f, t]) => (
                  <DropdownMenuItem key={f} onClick={() => exportAs(f)}>{t}</DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Paletas prontas</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {PRESETS.map((p) => (
                  <DropdownMenuItem key={p.name} onClick={() => setPal(p.colors)}>
                    <span className="flex gap-px">{p.colors.slice(0, 8).map((c, i) => <span key={i} className="size-2.5" style={{ background: css(c) }} />)}</span>
                    {p.name} <span className="ml-auto text-xs text-muted-foreground">{p.colors.length}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <div className="px-2 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">Do sprite</div>
            <DropdownMenuItem onClick={() => {
              const u = uniqueColors(allCels())
              if (!u.length) return toast.info('O sprite está vazio')
              setPal(u, false)
              toast.success(`${u.length} cores${u.length >= 256 ? ' (limite de 256)' : ''}`)
            }}>Extrair as cores usadas</DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Reduzir as cores do sprite para…</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {[4, 8, 16, 32, 64].map((n) => (
                  <DropdownMenuItem key={n} onClick={() => setPal(reducePalette(allCels(), n), false)}>{n} cores (paleta)</DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Ordenar</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {([['hue', 'Por matiz'], ['luma', 'Por brilho'], ['saturation', 'Por saturação']] as [SortKey, string][]).map(([k, t]) => (
                  <DropdownMenuItem key={k} onClick={() => setPal(sortPalette(pal, k), false)}>{t}</DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem onClick={makeRamp}>Rampa entre as pontas da seleção</DropdownMenuItem>
            <DropdownMenuSeparator />
            <div className="px-2 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">Projeto</div>
            <DropdownMenuItem disabled={!projectPalette.length} onClick={() => setPal(projectPalette)}>Usar a paleta do projeto ({projectPalette.length})</DropdownMenuItem>
            <DropdownMenuItem disabled={!pal.length} onClick={() => saveProjectPalette(pal).then(() => toast.success('Paleta salva no estilo do projeto'), (e) => toast.error(String(e)))}>
              Salvar como paleta do projeto
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {pal.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">Sem paleta. Use “Mais” para importar, escolher uma pronta ou extrair do sprite.</p>
      ) : (
        <div className="flex flex-wrap gap-0.5">
          {pal.map((c, i) => (
            <button
              key={i}
              className={cn('relative size-5 rounded-sm border border-black/40', i >= lo && i <= hi && 'ring-2 ring-primary ring-offset-1 ring-offset-background')}
              style={{ background: c[3] ? css(c) : 'repeating-conic-gradient(#555 0 25%, #333 0 50%) 0 0 / 8px 8px' }}
              title={`${i}: ${hex(c)} — esquerdo: principal, direito: secundária, Shift: intervalo`}
              onClick={(e) => {
                if (e.shiftKey && sel) setSel([sel[0], i])
                else {
                  setSel([i, i])
                  setFg(c)
                }
              }}
              onContextMenu={(e) => {
                e.preventDefault()
                setBg(c)
              }}
            />
          ))}
        </div>
      )}

      {cur && sel && sel[0] === sel[1] && (
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">#{sel[1]}</span>
          <input
            type="color"
            className="h-6 w-8 cursor-pointer rounded border bg-transparent"
            value={hex([cur[0], cur[1], cur[2], 255])}
            onChange={(e) => {
              const c = parseHex(e.target.value)
              if (c) commit(setPaletteColor(doc, sel[1], [c[0], c[1], c[2], cur[3]]))
            }}
            title={doc.colorMode === 'indexed' ? 'Mudar a cor (os pixels desta cor mudam junto)' : 'Mudar a cor'}
          />
          <Input
            key={hex(cur)}
            className="h-6 w-24 px-1 font-mono text-xs"
            defaultValue={hex(cur)}
            onBlur={(e) => {
              const c = parseHex(e.target.value)
              if (c) commit(setPaletteColor(doc, sel[1], c))
            }}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          />
          <span className="text-muted-foreground">α</span>
          <Input
            key={`a${cur[3]}`}
            type="number"
            min={0}
            max={255}
            className="h-6 w-14 px-1 text-xs"
            defaultValue={cur[3]}
            onBlur={(e) => commit(setPaletteColor(doc, sel[1], [cur[0], cur[1], cur[2], Math.max(0, Math.min(255, Number(e.target.value) || 0))]))}
          />
        </div>
      )}
    </div>
  )
}

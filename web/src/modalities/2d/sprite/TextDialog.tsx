// Ferramenta de texto: escreve, escolhe a fonte pixel e insere como seleção flutuante (dá para mover antes de aplicar).
import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { RGBA } from './raster'
import type { Img } from './selection'
import { loadFontFile, loadWebFont, renderText, WEB_PIXEL_FONTS, type FontChoice } from './text'

const STORE = 'asset-forge.sprite-text'

export function TextDialog({ color, onInsert, onClose }: { color: RGBA; onInsert: (img: Img) => void; onClose: () => void }) {
  const saved = (() => {
    try {
      return JSON.parse(localStorage.getItem(STORE) ?? '{}')
    } catch {
      return {}
    }
  })()
  const [text, setText] = useState<string>(saved.text ?? 'Asset Forge')
  const [fontKey, setFontKey] = useState<string>(saved.fontKey ?? 'bitmap')
  const [size, setSize] = useState<number>(saved.size ?? 8)
  const [scale, setScale] = useState<number>(saved.scale ?? 1)
  const [spacing, setSpacing] = useState<number>(saved.spacing ?? 0)
  const [lineGap, setLineGap] = useState<number>(saved.lineGap ?? 0)
  const [files, setFiles] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [img, setImg] = useState<Img | null>(null)

  const font: FontChoice = fontKey === 'bitmap' ? { kind: 'bitmap' } : { kind: 'css', family: fontKey.replace(/^(web|file|sys):/, '') }

  useEffect(() => {
    let alive = true
    const run = async () => {
      try {
        if (fontKey.startsWith('web:')) {
          setLoading(true)
          await loadWebFont(fontKey.slice(4), size)
        }
        if (!alive) return
        setImg(renderText(text, { font, size, color, spacing, lineGap, scale }))
      } catch (e) {
        toast.error(String(e))
      } finally {
        if (alive) setLoading(false)
      }
    }
    void run()
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, fontKey, size, scale, spacing, lineGap, color])

  // prévia como <img> (o diálogo monta num portal, então um canvas com ref pode ainda não existir no efeito)
  const previewUrl = useMemo(() => {
    if (!img) return null
    const t = document.createElement('canvas')
    t.width = img.w
    t.height = img.h
    t.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(img.data), img.w, img.h), 0, 0)
    return t.toDataURL()
  }, [img])
  const previewScale = img ? Math.max(1, Math.min(8, Math.floor(360 / Math.max(img.w, 1)))) : 1

  const insert = () => {
    if (!img) return
    try {
      localStorage.setItem(STORE, JSON.stringify({ text, fontKey: fontKey.startsWith('file:') ? 'bitmap' : fontKey, size, scale, spacing, lineGap }))
    } catch {
      /* sem storage */
    }
    onInsert(img)
  }
  const addFile = () => {
    const inp = document.createElement('input')
    inp.type = 'file'
    inp.accept = '.ttf,.otf,.woff,.woff2'
    inp.onchange = async () => {
      const f = inp.files?.[0]
      if (!f) return
      try {
        const fam = await loadFontFile(f)
        setFiles((x) => [...x, fam])
        setFontKey(`file:${fam}`)
      } catch (e) {
        toast.error(`Fonte inválida: ${e}`)
      }
    }
    inp.click()
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Texto</DialogTitle>
          <DialogDescription>Sem antialiasing: só a cor principal. Depois de inserir, arraste para posicionar e Enter aplica.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 text-sm">
          <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <select className="h-8 rounded-md border bg-transparent px-2" value={fontKey} onChange={(e) => {
              const v = e.target.value
              setFontKey(v)
              const wf = WEB_PIXEL_FONTS.find((f) => `web:${f.family}` === v)
              if (wf) setSize(wf.size)
            }}>
              <option value="bitmap">Pixel 3×5 (embutida)</option>
              <optgroup label="Google Fonts (pixel)">
                {WEB_PIXEL_FONTS.map((f) => <option key={f.family} value={`web:${f.family}`}>{f.family} — {f.note}</option>)}
              </optgroup>
              {files.length > 0 && <optgroup label="Arquivos">{files.map((f) => <option key={f} value={`file:${f}`}>{f.replace(/^AF /, '')}</option>)}</optgroup>}
              <optgroup label="Sistema"><option value="sys:monospace">Monospace do sistema</option></optgroup>
            </select>
            <Button variant="outline" size="sm" onClick={addFile}>Carregar .ttf…</Button>
          </div>
          <div className="grid grid-cols-4 gap-2 text-xs">
            {fontKey !== 'bitmap' && <label className="grid gap-0.5 text-muted-foreground">Tamanho (px)<Input type="number" min={4} max={128} value={size} onChange={(e) => setSize(Math.max(4, Math.min(128, Number(e.target.value) || 8)))} /></label>}
            <label className="grid gap-0.5 text-muted-foreground">Escala<Input type="number" min={1} max={16} value={scale} onChange={(e) => setScale(Math.max(1, Math.min(16, Number(e.target.value) || 1)))} /></label>
            <label className="grid gap-0.5 text-muted-foreground">Espaço entre letras<Input type="number" min={0} max={16} value={spacing} onChange={(e) => setSpacing(Math.max(0, Number(e.target.value) || 0))} /></label>
            <label className="grid gap-0.5 text-muted-foreground">Espaço entre linhas<Input type="number" min={0} max={32} value={lineGap} onChange={(e) => setLineGap(Math.max(0, Number(e.target.value) || 0))} /></label>
          </div>
          <div className="flex min-h-16 items-center justify-center overflow-auto rounded-md border bg-[repeating-conic-gradient(#2b2b2b_0_25%,#383838_0_50%)] bg-[length:16px_16px] p-2">
            {loading ? <Loader2 className="size-4 animate-spin" /> : img && previewUrl ? (
              <img src={previewUrl} alt="" width={img.w * previewScale} height={img.h * previewScale} style={{ imageRendering: 'pixelated' }} />
            ) : <span className="text-xs text-muted-foreground">(vazio)</span>}
          </div>
          {img && <p className="text-[11px] text-muted-foreground">{img.w}×{img.h} px</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={insert} disabled={!img}>Inserir</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Exportação genérica: lista os exportadores aplicáveis ao asset (de qualquer modalidade),
// formulário pelo params_schema e download do arquivo/zip gerado.
import { useEffect, useState, type ReactElement } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, downloadBlob, type AssetVersion, type Project } from '../api'
import { SchemaForm, type Values } from '../SchemaForm'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

export function ExportDialog({ asset, project, trigger, initial }: {
  asset: AssetVersion
  project: Project
  trigger: ReactElement
  initial?: string // exporter pré-selecionado
}) {
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState<string | undefined>(initial)
  const [values, setValues] = useState<Values>({})
  const [busy, setBusy] = useState(false)
  const exporters = useQuery({
    queryKey: ['exporters', project.slug, asset.ref],
    queryFn: () => api.exporters(project.slug, asset.slug, asset.version),
    enabled: open,
  })
  const list = (exporters.data ?? []).filter((e) => e.show_in_dialog !== false)
  const current = list.find((e) => e.id === sel) ?? list[0]

  useEffect(() => setValues({}), [current?.id])

  const run = async () => {
    if (!current) return
    setBusy(true)
    try {
      const { blob, filename } = await api.exportAsset(project.slug, asset.slug, asset.version, current.id, values)
      downloadBlob(blob, filename)
      toast.success(`Exportado: ${filename}`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Exportar {asset.name || asset.slug}</DialogTitle>
          <DialogDescription>{asset.ref} · exportar não cria versão nova.</DialogDescription>
        </DialogHeader>
        {exporters.isLoading && <p className="text-sm text-muted-foreground">Carregando…</p>}
        {!exporters.isLoading && list.length === 0 && (
          <p className="text-sm text-muted-foreground">Nenhum exportador para assets do tipo “{asset.kind}”. Use “Baixar”.</p>
        )}
        {list.length > 0 && (
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-1.5">
              {list.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  onClick={() => setSel(e.id)}
                  className={cn('rounded-md border px-2.5 py-1 text-sm', current?.id === e.id ? 'border-primary bg-primary/10' : 'hover:bg-muted')}
                  title={e.description}
                >
                  {e.title}
                </button>
              ))}
            </div>
            {current?.description && <p className="text-xs text-muted-foreground">{current.description}</p>}
            {current && <SchemaForm schema={current.params_schema} value={values} onChange={setValues} />}
          </div>
        )}
        <DialogFooter>
          <Button onClick={run} disabled={!current || busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Download />} Exportar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

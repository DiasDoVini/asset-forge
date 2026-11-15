import { AlertTriangle, Boxes, CheckCircle2, Download, Layers, Loader2, RotateCcw, Wrench } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { useState, type ReactNode } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

import type { PanelProps } from "@/core/registry"

import { runJob, splitRef } from "./editor/jobs"


interface Stats {
  vertices?: number
  faces?: number
  watertight?: boolean
  components?: number
  non_manifold_edges?: number
  boundary_edges?: number
  has_uv?: boolean
  has_texture?: boolean
  volume?: number | null
}

interface SplitInfo {
  parts: number
  cuts: { axis: string; offset: number }[]
  pin?: { diameter_mm: number; length_mm: number; hole_diameter_mm: number; hole_depth_mm?: number; count: number }
  sections_without_pins?: number
  part_extents_mm?: number[][]
  all_fit?: boolean
}

export function MeshInfoPanel({ asset, project }: PanelProps) {

  const extra = (asset.extra ?? {}) as Record<string, unknown>
  const stats = (extra.stats as { after?: Stats; before?: Stats } | undefined) ?? {}
  const after = stats.after
  const before = stats.before
  const print = extra.print as { fits_build_volume?: boolean; warnings?: string[]; printer?: string; scaled_to_fit?: number | null } | undefined
  const split = extra.split as SplitInfo | undefined
  const generation = extra.generation as { model?: string; raw_faces?: number; comfy?: string } | undefined
  const lowpoly = extra.lowpoly as { palette?: string[] } | undefined

  // dense surface kept with generated models (this version only: older ones may have it further up the history)
  const hasRaw = asset.files.some((f) => f.role === "source" && /(^|\/)(highres|raw)\.glb$/.test(f.path)) || Boolean(generation)
  const textured = Boolean(after?.has_texture)
  const action = (pipeline: string) => `/p/${project.slug}/gerar?pipeline=${pipeline}&input=${encodeURIComponent(asset.ref)}`

  return (
    <div className="space-y-4 text-sm">
      {asset.kind !== "print" && (
        <Section title="Ações">
          <FixFacesButton asset={asset} project={project} />
          {hasRaw && <RawAssetButton asset={asset} project={project} />}
          {hasRaw && (
            <Button size="sm" variant="outline" className="w-full justify-start" nativeButton={false} render={<Link to={action("3d.from_raw")} />}>
              <RotateCcw />Retrabalhar a partir do RAW…
            </Button>
          )}
          {textured && (
            <Button size="sm" variant="outline" className="w-full justify-start" nativeButton={false} render={<Link to={action("3d.bake_pbr")} />}>
              <Layers />Mapas PBR (cor, metálico, rugosidade, normal)
            </Button>
          )}
        </Section>
      )}
      {after && (
        <Section title="Geometria">
          <Row k="Triângulos">
            {before?.faces && before.faces !== after.faces ? `${before.faces.toLocaleString()} → ` : ""}
            {after.faces?.toLocaleString()}
          </Row>
          <Row k="Vértices">{after.vertices?.toLocaleString()}</Row>
          <Row k="Fechada (watertight)"><Flag ok={after.watertight} /></Row>
          <Row k="Partes soltas">{after.components}</Row>
          {!!after.non_manifold_edges && <Row k="Arestas não-manifold">{after.non_manifold_edges}</Row>}
          <Row k="UV / textura">{after.has_uv ? "UV" : "—"} {after.has_texture ? "· textura" : ""}</Row>
        </Section>
      )}

      {generation && (
        <Section title="Geração">
          <Row k="Modelo">{generation.model}</Row>
          {generation.raw_faces && <Row k="Malha bruta">{generation.raw_faces.toLocaleString()} tris</Row>}
        </Section>
      )}

      {lowpoly?.palette && (
        <Section title="Paleta">
          <div className="flex flex-wrap gap-1">
            {lowpoly.palette.map((c) => (
              <span key={c} title={c} className="size-5 rounded border" style={{ background: c }} />
            ))}
          </div>
        </Section>
      )}

      {print && (
        <Section title={`Impressão${print.printer ? ` · ${print.printer}` : ""}`}>
          <Row k="Cabe na impressora"><Flag ok={print.fits_build_volume} /></Row>
          {print.scaled_to_fit ? <Row k="Reduzido para caber">{(print.scaled_to_fit * 100).toFixed(1)}%</Row> : null}
          <Warnings items={print.warnings} />
        </Section>
      )}

      {split && (
        <Section title="Peças">
          <Row k="Peças">{split.parts}</Row>
          {split.pin && split.pin.count > 0 && (
            <Row k="Pinos">
              {split.pin.count}× ⌀{split.pin.diameter_mm} × {split.pin.length_mm} mm (furo ⌀{split.pin.hole_diameter_mm}
              {split.pin.hole_depth_mm ? ` × ${split.pin.hole_depth_mm}` : ""})
            </Row>
          )}
          <Row k="Todas cabem"><Flag ok={split.all_fit} /></Row>
          {split.pin && split.pin.count > 0 && (
            <p className="text-xs text-muted-foreground">
              Os furos ficam nas faces de corte. Cada peça sai deitada sobre essa face (melhor para imprimir); use
              “Virar” no visualizador para vê-los.
            </p>
          )}
          <Warnings
            items={split.sections_without_pins ? [`${split.sections_without_pins} corte(s) fino(s) demais para pino`] : []}
          />
          <div className="mt-2 space-y-1">
            {asset.files
              .filter((f) => f.role === "part")
              .map((f, i) => (
                <div key={f.path} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs">{f.path}</span>
                  <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                    {split.part_extents_mm?.[i]?.map((v) => Math.round(v)).join("×")}
                  </span>
                  <DownloadButton href={f.url} />
                </div>
              ))}
          </div>
        </Section>
      )}

      <Section title="Arquivos">
        {asset.files.map((f) => (
          <div key={f.path} className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono text-[10px]">{f.role}</Badge>
            <span className="truncate font-mono text-xs">{f.path}</span>
            <DownloadButton href={f.url} />
          </div>
        ))}
      </Section>
    </div>
  )
}

/** One click: the dense raw surface becomes its own asset (<slug>-raw), usable by every other tool. */
/** Turn inside-out pieces outward (the "peeled" look of models made before the orientation fix): new version. */
function FixFacesButton({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const fix = async () => {
    setBusy("Corrigindo…")
    try {
      const ref = await runJob(project.slug, "3d.repair", { input_asset: asset.ref, orientation_only: true })
      const { slug, version } = splitRef(ref)
      toast.success(`Faces corrigidas: ${ref}`)
      await qc.invalidateQueries({ queryKey: ["assets", project.slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(null)
    }
  }
  return (
    <Button size="sm" variant="outline" className="w-full justify-start" disabled={!!busy} onClick={fix}
      title="Para modelos com aparência 'descascada' (partes viradas do avesso). Mantém textura e UVs; grava uma nova versão">
      {busy ? <Loader2 className="animate-spin" /> : <Wrench />}{busy ?? "Corrigir faces invertidas"}
    </Button>
  )
}

function RawAssetButton({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const create = async () => {
    setBusy("Criando…")
    try {
      const ref = await runJob(project.slug, "3d.from_raw", { input_asset: asset.ref, cleanup: "clean", target_faces: 0 },
        (job) => setBusy(`${job.message ?? "Processando"} ${Math.round((job.progress ?? 0) * 100)}%`),
        { asset: `${asset.slug}-raw` })
      toast.success(`Asset criado a partir do RAW: ${ref}`)
      const { slug, version } = splitRef(ref)
      await qc.invalidateQueries({ queryKey: ["assets", project.slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 12000 })
    } finally {
      setBusy(null)
    }
  }
  return (
    <Button size="sm" variant="outline" className="w-full justify-start" disabled={!!busy} onClick={create}
      title="A malha densa (antes da simplificação) vira um asset próprio: dá para editar, detalhar, texturizar, animar e exportar">
      {busy ? <Loader2 className="animate-spin" /> : <Boxes />}{busy ?? "Criar asset a partir do RAW"}
    </Button>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      <div className="space-y-1">{children}</div>
    </section>
  )
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right">{children}</span>
    </div>
  )
}

function Flag({ ok }: { ok?: boolean }) {
  if (ok === undefined) return <>—</>
  return ok ? (
    <CheckCircle2 className="inline size-4 text-emerald-500" aria-label="sim" />
  ) : (
    <AlertTriangle className="inline size-4 text-amber-500" aria-label="não" />
  )
}

function Warnings({ items }: { items?: string[] }) {
  if (!items?.length) return null
  return (
    <ul className="mt-1 space-y-1">
      {items.map((w) => (
        <li key={w} className="flex gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          {w}
        </li>
      ))}
    </ul>
  )
}

function DownloadButton({ href }: { href: string }) {
  return (
    <Button size="icon-xs" variant="ghost" className="ml-auto shrink-0" nativeButton={false} render={<a href={href} download />} aria-label="Baixar">
      <Download />
    </Button>
  )
}

export default MeshInfoPanel

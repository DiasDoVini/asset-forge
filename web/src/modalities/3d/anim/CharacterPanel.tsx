// "Personagem": turn a character concept (image or 3D model) into an animatable asset (3d.character: T-pose redraw
// without weapons/capes, props apart, 3D again, rig) or rig a mesh as it is (3d.character_rig); on a rigged character,
// add more library animations (3d.retarget) and read the deformation check.
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Bone, CheckCircle2, Clapperboard, Loader2, Shirt, Sparkles, Swords, Wand2 } from "lucide-react"
import { useMemo, useState, type ReactNode } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/core/api"
import type { PanelProps } from "@/core/registry"
import { cn } from "@/lib/utils"

import { runJob, splitRef } from "../editor/jobs"

type Mode = "ai" | "rig" | "retarget"

const SETS: [string, string, string][] = [
  ["basic", "Básicas", "parado, andar, correr, pular, agachar, rolar, dano, morte"],
  ["combat", "Combate", "espada, socos, magia, pistola, escudo"],
  ["social", "Social", "falar, dançar, sentar, sim/não"],
  ["all", "Todas", "todos os clipes da biblioteca"],
]

// same default as forge_3d.character.DEFAULT_REMOVE (the image editor reads English best)
const DEFAULT_REMOVE = "weapons, shields, staffs and anything held in the hands, capes and cloaks, and objects floating around the character"

interface CharacterInfo {
  skeleton?: string
  joints?: number
  height?: number
  library?: string | null
  base?: string
  props?: { item: string; asset: string }[]
  deformation?: { grade: string; worst_ratio: number; problems: { name: string; torn_pct: number; max_stretch: number; bones: Record<string, number> }[] }
}

export function CharacterPanel({ asset, project }: PanelProps) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const lib = useQuery({ queryKey: ["animation-library"], queryFn: api.animationLibrary, staleTime: 60_000 })
  const anim = asset.extra?.animation as { type?: string; clips?: { name: string }[] } | undefined
  const rigged = asset.kind === "animation" && anim?.type === "skeleton"
  const isImage = asset.modality === "2d"
  const info = (asset.extra?.character ?? null) as CharacterInfo | null
  const modes: Mode[] = rigged ? ["retarget", "rig"] : isImage ? ["ai"] : ["ai", "rig"]
  const [mode, setMode] = useState<Mode>(modes[0])

  const [remove, setRemove] = useState(DEFAULT_REMOVE)
  const [loose, setLoose] = useState("fit")
  const [extract, setExtract] = useState("")
  const [notes, setNotes] = useState("")
  const [generator, setGenerator] = useState("")
  const [texture, setTexture] = useState(true)
  const [sets, setSets] = useState<string[]>(rigged ? [] : ["basic"])
  const [names, setNames] = useState<string[]>([])
  const [rootMotion, setRootMotion] = useState(false)
  const [naming, setNaming] = useState("unreal")
  const [fingers, setFingers] = useState(false)
  const [meshFix, setMeshFix] = useState("clean")
  const [binding, setBinding] = useState("surface")
  const [height, setHeight] = useState("")
  const [replace, setReplace] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const files = asset.files
  const img = (name: string) => files.find((f) => f.path === name || f.path.endsWith(`/${name}`))
  const have = new Set(anim?.clips?.map((c) => c.name) ?? [])

  const animations = { sets, names, root_motion: rootMotion }
  const rig = {
    naming, fingers, mesh_fix: meshFix, binding, ...(height ? { height: Number(height) } : {}),
  }

  const submit = async () => {
    let pipeline = "3d.character_rig"
    let params: Record<string, unknown> = { input_asset: asset.ref, animations, rig }
    let opts: { asset?: string } = rigged ? { asset: asset.slug } : {}
    let label = "Riggando o personagem…"
    if (mode === "ai") {
      pipeline = "3d.character"
      label = "Preparando o personagem… (T-pose, 3D e rig: alguns minutos)"
      params = {
        ...params, remove, loose_clothing: loose, notes: notes || null, texture,
        extract: extract.split(",").map((s) => s.trim()).filter(Boolean),
        ...(generator ? { generator } : {}),
      }
      opts = {}
    } else if (mode === "retarget") {
      pipeline = "3d.retarget"
      label = "Aplicando as animações…"
      params = { input_asset: asset.ref, animations, replace }
      opts = {}
    }
    setBusy(label)
    try {
      const ref = await runJob(project.slug, pipeline, params,
        (job) => setBusy(`${job.message ?? label} ${Math.round((job.progress ?? 0) * 100)}%`), opts)
      toast.success(`Pronto: ${ref}`)
      const { slug, version } = splitRef(ref)
      await qc.invalidateQueries({ queryKey: ["versions", project.slug, slug] })
      await qc.invalidateQueries({ queryKey: ["assets", project.slug] })
      nav(`/p/${project.slug}/a/${slug}/${version}`)
    } catch (e) {
      toast.error(String(e).replace(/^Error: /, ""), { duration: 15000 })
    } finally {
      setBusy(null)
    }
  }

  const noLibrary = lib.data && lib.data.files.length === 0
  const nothingToAdd = mode === "retarget" && !sets.length && !names.length

  return (
    <div className="flex h-full min-h-[560px] w-full gap-3">
      <div className="min-w-0 flex-1 space-y-4 overflow-y-auto rounded-lg bg-muted/30 p-4">
        {info ? (
          <CharacterResult info={info} slug={project.slug} poses={img("poses.png")?.url} tpose={img("tpose.png")?.url}
            stripped={img("stripped.png")?.url} concept={img("concept.png")?.url}
            propImages={files.filter((f) => /prop_[^/]+\.png$/.test(f.path))} />
        ) : rigged ? (
          <p className="text-sm text-muted-foreground">Personagem com esqueleto. Adicione animações da biblioteca ao lado; elas são
            reajustadas às proporções dele. Edite as articulações na aba “Esqueleto” e crie clipes próprios em “Animar”.</p>
        ) : (
          <HowItWorks />
        )}
      </div>

      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto pr-1 text-sm">
        {modes.length > 1 && (
          <div className="grid grid-cols-2 gap-1 rounded-md bg-muted p-1">
            {modes.map((m) => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={cn("rounded px-2 py-1 text-xs font-medium", mode === m ? "bg-background shadow-sm" : "text-muted-foreground")}>
                {m === "ai" ? "Refazer em T-pose (IA)" : m === "rig" ? (rigged ? "Refazer o rig" : "Riggar como está") : "Adicionar animações"}
              </button>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          {mode === "ai" && "A IA redesenha o personagem em T-pose sem armas e capas, gera os itens removidos como props separados, refaz o 3D e monta o esqueleto com animações."}
          {mode === "rig" && "Usa a malha como está (de pé, de frente, em T ou A): limpa, encaixa o esqueleto, calcula os pesos e aplica as animações."}
          {mode === "retarget" && "Aplica clipes da biblioteca neste personagem, ajustados às proporções dele. Os clipes atuais são mantidos."}
        </p>

        {mode === "ai" && (
          <Section title="O que tirar" icon={<Swords className="size-3.5" />}>
            <Textarea rows={3} value={remove} onChange={(e) => setRemove(e.target.value)} aria-label="O que tirar do personagem" className="text-xs" />
            <Labeled label="Roupas largas">
              <NativeSelect size="sm" value={loose} onChange={(e) => setLoose(e.target.value)} aria-label="Roupas largas">
                <option value="fit">Ajustar ao corpo</option>
                <option value="keep">Manter como estão</option>
                <option value="remove">Tirar (mostrar o que tem por baixo)</option>
              </NativeSelect>
            </Labeled>
            <Labeled label="Props à parte">
              <Input className="h-7 text-xs" placeholder="ex.: cajado, escudo" value={extract} onChange={(e) => setExtract(e.target.value)} aria-label="Itens gerados como props separados" />
            </Labeled>
            <Labeled label="Notas">
              <Input className="h-7 text-xs" placeholder="ex.: manter o chapéu" value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Notas para a IA" />
            </Labeled>
            <Labeled label="Método 3D">
              <NativeSelect size="sm" value={generator} onChange={(e) => setGenerator(e.target.value)} aria-label="Método 3D">
                <option value="">Automático</option>
                <option value="trellis2">TRELLIS.2</option>
                <option value="hunyuan3d-2.1">Hunyuan3D 2.1</option>
              </NativeSelect>
            </Labeled>
            <Check checked={texture} onChange={setTexture}>Texturizar (TRELLIS.2)</Check>
          </Section>
        )}

        <Section title="Animações" icon={<Clapperboard className="size-3.5" />}>
          {lib.isLoading && <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline size-3 animate-spin" />Lendo a biblioteca…</p>}
          {noLibrary && (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              Nenhuma biblioteca de animações encontrada. Configure a pasta em <Link className="underline" to="/options?tab=anim">Opções › Animações 3D</Link>.
            </p>
          )}
          <div className="grid grid-cols-2 gap-1">
            {SETS.map(([id, label, hint]) => (
              <Check key={id} checked={sets.includes(id)} title={hint}
                onChange={(on) => setSets((s) => (on ? [...s.filter((x) => x !== id), id] : s.filter((x) => x !== id)))}>{label}</Check>
            ))}
          </div>
          {lib.data && lib.data.files.length > 0 && (
            <ClipPicker files={lib.data.files.filter((f) => !f.root_motion)} picked={names} have={have}
              inSets={new Set(sets.flatMap((s) => (s === "all" ? [] : lib.data!.sets[s] ?? [])))} onChange={setNames} />
          )}
          <Check checked={rootMotion} onChange={setRootMotion}>Com deslocamento (root motion)</Check>
          {mode === "retarget" && <Check checked={replace} onChange={setReplace}>Substituir os clipes atuais</Check>}
        </Section>

        {mode !== "retarget" && (
          <Section title="Esqueleto e malha" icon={<Bone className="size-3.5" />}>
            <Labeled label="Nomes">
              <NativeSelect size="sm" value={naming} onChange={(e) => setNaming(e.target.value)} aria-label="Nomes dos ossos">
                <option value="unreal">Unreal / UAL</option>
                <option value="mixamo">Mixamo / Unity</option>
              </NativeSelect>
            </Labeled>
            <Labeled label="Malha">
              <NativeSelect size="sm" value={meshFix} onChange={(e) => setMeshFix(e.target.value)} aria-label="Correção da malha">
                <option value="clean">Limpar (mantém a textura)</option>
                <option value="rebuild">Refazer superfície fechada</option>
                <option value="none">Usar como está</option>
              </NativeSelect>
            </Labeled>
            <Labeled label="Pesos">
              <NativeSelect size="sm" value={binding} onChange={(e) => setBinding(e.target.value)} aria-label="Cálculo dos pesos">
                <option value="surface">Superfície (corpos)</option>
                <option value="volume">Volume (cabelo longo, saias)</option>
              </NativeSelect>
            </Labeled>
            <Labeled label="Altura">
              <Input className="h-7 text-xs" inputMode="decimal" placeholder="padrão do projeto ou 1.8" value={height}
                onChange={(e) => setHeight(e.target.value.replace(/[^\d.]/g, ""))} aria-label="Altura" />
            </Labeled>
            <Check checked={fingers} onChange={setFingers}>Ossos dos dedos (mãos com dedos separados)</Check>
          </Section>
        )}

        <Button className="w-full" disabled={!!busy || nothingToAdd || (noLibrary && (sets.length > 0 || names.length > 0))} onClick={submit}>
          {busy ? <Loader2 className="animate-spin" /> : mode === "ai" ? <Sparkles /> : <Wand2 />}
          {mode === "ai" ? "Preparar personagem" : mode === "rig" ? "Riggar e animar" : "Adicionar animações"}
        </Button>
        {busy && <p className="text-xs text-muted-foreground">{busy}</p>}
      </aside>
    </div>
  )
}

function ClipPicker({ files, picked, have, inSets, onChange }: {
  files: { id: string; clips: { name: string; duration: number }[] }[]
  picked: string[]; have: Set<string>; inSets: Set<string>; onChange: (v: string[]) => void
}) {
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const all = useMemo(() => {
    const seen = new Set<string>()
    return files.flatMap((f) => f.clips.filter((c) => c.name !== "A_TPose" && !seen.has(c.name) && (seen.add(c.name), true)).map((c) => ({ ...c, lib: f.id })))
  }, [files])
  const shown = all.filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="rounded-md border">
      <button type="button" className="flex w-full items-center justify-between px-2 py-1.5 text-xs" onClick={() => setOpen((o) => !o)}>
        <span>Escolher clipes {picked.length > 0 && <Badge variant="secondary" className="ml-1">{picked.length}</Badge>}</span>
        <span className="text-muted-foreground">{all.length} na biblioteca {open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="space-y-1 border-t p-2">
          <Input className="h-7 text-xs" placeholder="Buscar (walk, sword, idle…)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar clipe" />
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {shown.map((c) => {
              const on = picked.includes(c.name)
              const implied = inSets.has(c.name)
              return (
                <li key={c.name}>
                  <label className={cn("flex items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-accent/50", implied && "text-muted-foreground")}>
                    <input type="checkbox" checked={on || implied} disabled={implied}
                      onChange={(e) => onChange(e.target.checked ? [...picked, c.name] : picked.filter((n) => n !== c.name))} />
                    <span className="truncate font-mono">{c.name}</span>
                    {have.has(c.name) && <Badge variant="outline" className="text-[10px]">já tem</Badge>}
                    <span className="ml-auto shrink-0 text-muted-foreground">{c.duration.toFixed(1)}s</span>
                  </label>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

function CharacterResult({ info, slug, poses, tpose, stripped, concept, propImages }: {
  info: CharacterInfo; slug: string; poses?: string; tpose?: string; stripped?: string; concept?: string
  propImages: { path: string; url: string }[]
}) {
  const d = info.deformation
  const linkOf = (ref: string) => {
    const { slug: s, version } = splitRef(ref)
    return `/p/${slug}/a/${s}/${version}`
  }
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">{info.skeleton}</Badge>
        <span className="text-muted-foreground">{info.joints} ossos · {info.height} de altura{info.library ? ` · biblioteca ${info.library}` : ""}</span>
        {d && (
          <span className={cn("flex items-center gap-1", d.grade === "ok" ? "text-emerald-600" : d.grade === "warn" ? "text-amber-600" : "text-red-600")}>
            {d.grade === "ok" ? <CheckCircle2 className="size-3.5" /> : <AlertTriangle className="size-3.5" />}
            deformação: {d.grade === "ok" ? "sem rasgos" : d.grade === "warn" ? "pequenos rasgos" : "rasgos visíveis"}
          </span>
        )}
      </div>
      {d && d.problems.length > 0 && (
        <details className="rounded-md border bg-background p-2 text-xs">
          <summary className="cursor-pointer">Onde estica ({d.problems.length} clipe(s))</summary>
          <ul className="mt-1 space-y-0.5">
            {d.problems.map((p) => (
              <li key={p.name}><span className="font-mono">{p.name}</span>: {p.torn_pct}% das arestas (até x{p.max_stretch}) perto de {Object.keys(p.bones).join(", ")}</li>
            ))}
          </ul>
          <p className="mt-1 text-muted-foreground">Ajuste as articulações na aba “Esqueleto”, use “Pesos: volume” para cabelo/saias ou “Malha: refazer superfície”.</p>
        </details>
      )}
      {poses && <Figure title="Poses (prévia das animações)" src={poses} />}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {concept && <Figure title="Conceito" src={concept} small />}
        {stripped && <Figure title="Sem os itens" src={stripped} small />}
        {tpose && <Figure title="T-pose (IA)" src={tpose} small />}
        {propImages.map((f) => <Figure key={f.path} title={f.path.replace(/^.*prop_|\.png$/g, "")} src={f.url} small />)}
      </div>
      {(info.base || info.props?.length) && (
        <div className="flex flex-wrap gap-2 text-xs">
          {info.base && <Button size="sm" variant="outline" render={<Link to={linkOf(info.base)} />}><Shirt /> Malha em T-pose ({info.base})</Button>}
          {info.props?.map((p) => <Button key={p.asset} size="sm" variant="outline" render={<Link to={linkOf(p.asset)} />}><Swords /> {p.item}</Button>)}
        </div>
      )}
    </>
  )
}

function HowItWorks() {
  const steps: [string, string][] = [
    ["T-pose", "A IA redesenha o personagem de frente, braços abertos, na pose do manequim da biblioteca."],
    ["Separar", "Armas, escudos, capas e objetos soltos saem do corpo; os que você pedir viram props 3D à parte."],
    ["3D de novo", "O corpo é gerado de novo em 3D a partir da T-pose (TRELLIS.2 ou Hunyuan3D), com espaço sob os braços."],
    ["Malha", "Fragmentos soltos e faces quebradas são limpos; opcionalmente vira uma superfície fechada única."],
    ["Esqueleto", "Esqueleto humanoide (nomes Unreal/UAL ou Mixamo) encaixado no corpo; pesos suaves sem degraus entre ossos."],
    ["Animações", "Clipes da Universal Animation Library reajustados às proporções do personagem, com prévia das poses."],
    ["Checagem", "Cada clipe é tocado e as regiões que esticam ou rasgam são apontadas."],
  ]
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Do conceito ao personagem animável</h3>
      <ol className="space-y-2">
        {steps.map(([t, d], i) => (
          <li key={t} className="flex gap-3 text-sm">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{i + 1}</span>
            <span><span className="font-medium">{t}.</span> <span className="text-muted-foreground">{d}</span></span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">O resultado é um asset de animação (GLB com esqueleto e clipes) pronto para Godot, Unity e Unreal.
        A malha em T-pose e os props ficam como assets próprios.</p>
    </div>
  )
}

function Figure({ title, src, small }: { title: string; src: string; small?: boolean }) {
  return (
    <figure className="space-y-1">
      <figcaption className="text-xs text-muted-foreground">{title}</figcaption>
      <a href={src} target="_blank" rel="noreferrer">
        <img src={src} alt={title} className={cn("w-full rounded-md border bg-white object-contain", small ? "aspect-square" : "")} />
      </a>
    </figure>
  )
}

function Check({ checked, onChange, title, children }: { checked: boolean; onChange: (v: boolean) => void; title?: string; children: ReactNode }) {
  return (
    <label className="flex items-center gap-2 text-xs" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{children}
    </label>
  )
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}

function Section({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">{icon}{title}</h4>
      {children}
    </section>
  )
}

export default CharacterPanel

// Image → 3D: extra reference images of the same subject, one per side (the input image is the front).
// Shown right below the main input, with the same picker (project assets, versions or an uploaded file).
import { AssetPicker } from "@/core/components/AssetPicker"
import type { FieldCtx } from "@/core/SchemaForm"
import { Label } from "@/components/ui/label"

// "left" = the subject's own left side: in the picture it faces the left of the image (Hunyuan3D-2mv convention)
const SIDES = [
  ["left", "Esquerda", "lado esquerdo do objeto: ele olha para a esquerda da imagem"],
  ["back", "Costas", "objeto visto por trás"],
  ["right", "Direita", "lado direito do objeto: ele olha para a direita da imagem"],
] as const

export function SideViewsField({ value, set, project, schema }: FieldCtx & { project: string }) {
  // the same field serves 3d.image_to_3d (shape) and 3d.texture_ai (colors)
  const forShape = (schema.description ?? "").includes("multi-view")
  const views = (value as Record<string, string> | undefined) ?? {}
  const update = (side: string, ref: string | undefined) => {
    const next = { ...views }
    if (ref) next[side] = ref
    else delete next[side]
    set(Object.keys(next).length ? next : undefined)
  }
  const count = Object.keys(views).length
  return (
    <div className="grid gap-2">
      <Label>
        Outras vistas do mesmo objeto{" "}
        <span className="text-xs font-normal text-muted-foreground">
          opcional; {forShape ? "a entrada acima é a frente" : "a frente é a imagem de origem do modelo"}. Esquerda/direita são os lados do próprio objeto
        </span>
      </Label>
      {SIDES.map(([side, label, hint]) => (
        <div key={side} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-2" title={hint}>
          <span className="text-xs text-muted-foreground">{label}</span>
          <AssetPicker project={project} value={views[side]} onChange={(v) => update(side, v)} placeholder={`Escolher vista ${label.toLowerCase()}`} />
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        {!forShape
          ? "Método projeção: as vistas dadas substituem as geradas por IA. Vistas salvas pelo Image → 3D multi-view são usadas automaticamente."
          : count
            ? `${count + 1} imagens: usa o Hunyuan3D 2.0 multi-view (lados escondidos e proporções mais fiéis). Use vistas retas: a entrada de frente exata e os lados a 90°; uma frente em 3/4 confunde o modelo.`
            : "Só a entrada: usa o método de geração escolhido acima."}
      </p>
    </div>
  )
}

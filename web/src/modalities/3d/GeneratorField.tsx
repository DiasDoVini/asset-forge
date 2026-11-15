// Image/Text -> 3D: pick the generation method; only the chosen method's options are shown
// (params.trellis / params.hunyuan blocks, see forge_3d.gen_pipelines).
import type { JsonSchema } from "@/core/api"
import type { FieldCtx } from "@/core/SchemaForm"
import { SchemaForm } from "@/core/SchemaForm"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

type Method = "auto" | "trellis2" | "hunyuan3d-2.1"

const METHODS: { id: Method; title: string; note: string }[] = [
  { id: "auto", title: "Automático", note: "TRELLIS.2 se a instância dele estiver rodando (porta 8190); senão Hunyuan3D 2.1, com as opções padrão de cada um." },
  { id: "trellis2", title: "TRELLIS.2", note: "Microsoft TRELLIS.2-4B: bordas nítidas, rosto e peças pequenas, textura opcional na mesma execução. ~4–8 min." },
  { id: "hunyuan3d-2.1", title: "Hunyuan3D 2.1", note: "Tencent Hunyuan3D 2.1: mais rápido (~1 min), forma mais suave. Também é o usado com vistas extras (multi-view)." },
]
const BLOCK: Partial<Record<Method, "trellis" | "hunyuan">> = { trellis2: "trellis", "hunyuan3d-2.1": "hunyuan" }

type Ctx = FieldCtx & { project: string; values?: Record<string, unknown>; setField?: (name: string, v: unknown) => void; root?: JsonSchema }

/** The option block's own schema ({"$ref": "#/$defs/TrellisOptions"} or allOf[...] in pydantic's output). */
function blockSchema(root: JsonSchema | undefined, name: string): JsonSchema | undefined {
  const prop = root?.properties?.[name] as (JsonSchema & { allOf?: JsonSchema[] }) | undefined
  const ref = prop?.$ref ?? prop?.allOf?.[0]?.$ref
  return ref ? root?.$defs?.[ref.split("/").pop()!] : prop
}

export function GeneratorField({ value, set, values, setField, root }: Ctx) {
  const sideViews = values?.side_views as Record<string, string> | undefined
  const multiView = Boolean(sideViews && Object.keys(sideViews).length)
  const chosen: Method = (value as Method | undefined) ?? "auto"
  const effective: Method = multiView ? "hunyuan3d-2.1" : chosen
  const block = BLOCK[effective]
  const schema = block ? blockSchema(root, block) : undefined
  return (
    <div className="grid gap-2 rounded-lg border p-3">
      <Label>Método de geração</Label>
      <div className="grid grid-cols-3 gap-1 rounded-md bg-muted p-1">
        {METHODS.map((m) => (
          <button key={m.id} type="button" disabled={multiView && m.id !== "hunyuan3d-2.1"}
            onClick={() => set(m.id === "auto" ? undefined : m.id)}
            className={cn("rounded px-2 py-1.5 text-xs font-medium transition-colors disabled:opacity-40",
              effective === m.id ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground")}>
            {m.title}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {multiView ? "Com vistas extras o modelo multi-view do Hunyuan3D é usado." : METHODS.find((m) => m.id === effective)!.note}
      </p>
      {block && schema && (
        <div className="mt-1 border-t pt-3">
          <SchemaForm
            schema={schema}
            value={(values?.[block] as Record<string, unknown> | undefined) ?? {}}
            onChange={(v) => setField?.(block, Object.keys(v).length ? v : undefined)}
          />
        </div>
      )}
    </div>
  )
}

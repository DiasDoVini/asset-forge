// Formulário genérico a partir do JSON schema (pydantic) dos params de um pipeline.
// Só envia campos que o usuário alterou; o resto usa o default do servidor.
import type { ReactNode } from 'react'
import type { JsonSchema } from './api'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Slider } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import { NativeSelect } from '@/components/ui/native-select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

export type Values = Record<string, unknown>

export type FieldCtx = { name: string; schema: JsonSchema; value: unknown; set: (v: unknown) => void; required: boolean }

export type SchemaFormProps = {
  schema: JsonSchema
  value: Values
  onChange: (v: Values) => void
  hidden?: string[]
  // sobrescreve a renderização de campos específicos (ex.: seletor de asset)
  custom?: Record<string, (ctx: FieldCtx) => ReactNode>
  className?: string
}

const LONG_TEXT = new Set(['prompt', 'instruction', 'negative', 'system_prompt', 'notes'])

function resolve(s: JsonSchema, root: JsonSchema): JsonSchema {
  if (s.$ref) {
    const key = s.$ref.split('/').pop()!
    return resolve(root.$defs?.[key] ?? {}, root)
  }
  return s
}

// anyOf [X, null] -> {X, nullable}
function unwrap(s: JsonSchema, root: JsonSchema): { base: JsonSchema; nullable: boolean } {
  s = resolve(s, root)
  if (s.anyOf) {
    const opts = s.anyOf.map((o) => resolve(o, root))
    const nonNull = opts.filter((o) => o.type !== 'null')
    const nullable = nonNull.length < opts.length
    if (nonNull.length === 1) return { base: { ...nonNull[0], description: s.description, default: s.default, title: s.title }, nullable }
  }
  return { base: s, nullable: false }
}

function typeOf(s: JsonSchema): string {
  if (s.enum) return 'enum'
  const t = Array.isArray(s.type) ? s.type.find((x) => x !== 'null') : s.type
  return t ?? 'string'
}

export function humanize(name: string) {
  const s = name.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function SchemaForm({ schema, value, onChange, hidden = [], custom = {}, className }: SchemaFormProps) {
  const props = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const names = Object.keys(props).filter((n) => !hidden.includes(n))
  // campos obrigatórios e textos longos primeiro
  names.sort((a, b) => Number(required.has(b)) - Number(required.has(a)) || Number(LONG_TEXT.has(b)) - Number(LONG_TEXT.has(a)))

  const setField = (name: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined || v === '') delete next[name]
    else next[name] = v
    onChange(next)
  }

  return (
    <div className={cn('grid gap-4', className)}>
      {names.map((name) => {
        const ctx: FieldCtx = { name, schema: props[name], value: value[name], set: (v) => setField(name, v), required: required.has(name) }
        return <div key={name}>{custom[name] ? custom[name](ctx) : <Field {...ctx} root={schema} />}</div>
      })}
    </div>
  )
}

function FieldLabel({ name, schema, required, htmlFor }: { name: string; schema: JsonSchema; required: boolean; htmlFor?: string }) {
  return (
    <div className="mb-1.5">
      <Label htmlFor={htmlFor}>
        {humanize(name)}
        {required && <span className="text-destructive" aria-label="obrigatório">*</span>}
      </Label>
      {schema.description && <p className="mt-0.5 text-xs text-muted-foreground">{schema.description}</p>}
    </div>
  )
}

function Field({ name, schema: raw, value, set, required, root }: FieldCtx & { root: JsonSchema }) {
  const { base, nullable } = unwrap(raw, root)
  const t = typeOf(base)
  const id = `f-${name}`
  const def = base.default ?? raw.default
  const placeholder = def === null || def === undefined ? (nullable ? 'auto' : '') : String(def)

  if (t === 'boolean') {
    if (nullable) {
      const cur = value === undefined ? 'auto' : value ? 'on' : 'off'
      return (
        <div>
          <FieldLabel name={name} schema={base} required={required} />
          <Tabs value={cur} onValueChange={(k) => set(k === 'auto' ? undefined : k === 'on')}>
            <TabsList aria-label={humanize(name)}>
              {(['auto', 'on', 'off'] as const).map((k) => (
                <TabsTrigger key={k} value={k} className="px-3">{k === 'auto' ? 'auto' : k === 'on' ? 'sim' : 'não'}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      )
    }
    const checked = value === undefined ? Boolean(def) : Boolean(value)
    return (
      <div className="flex items-center justify-between gap-3">
        <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
        <Switch id={id} checked={checked} onCheckedChange={(c) => set(c === Boolean(def) ? undefined : c)} />
      </div>
    )
  }

  if (t === 'enum') {
    const opts = (base.enum ?? []).map(String)
    return (
      <div>
        <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
        <NativeSelect
          id={id}
          wrapperClassName="w-full"
          value={value === undefined ? '' : String(value)}
          onChange={(e) => set(e.target.value === '' ? undefined : e.target.value)}
        >
          <option value="">{def !== undefined && def !== null ? `${def} (padrão)` : 'auto'}</option>
          {opts.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </NativeSelect>
      </div>
    )
  }

  if (t === 'integer' || t === 'number') {
    const min = base.minimum ?? base.exclusiveMinimum
    const max = base.maximum ?? base.exclusiveMaximum
    const step = t === 'integer' ? 1 : (max ?? 10) - (min ?? 0) <= 4 ? 0.05 : 0.5
    const num = value === undefined ? undefined : Number(value)
    const showSlider = min !== undefined && max !== undefined && max - min <= 256
    return (
      <div>
        <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
        <div className="flex items-center gap-3">
          {showSlider && (
            <Slider
              className="flex-1"
              min={min}
              max={max}
              step={step}
              value={[num ?? (typeof def === 'number' ? def : min!)]}
              onValueChange={(v) => set(Array.isArray(v) ? v[0] : v)}
            />
          )}
          <Input
            id={id}
            type="number"
            className={showSlider ? 'w-24' : ''}
            min={min}
            max={max}
            step={step}
            placeholder={placeholder}
            value={num ?? ''}
            onChange={(e) => set(e.target.value === '' ? undefined : t === 'integer' ? parseInt(e.target.value, 10) : parseFloat(e.target.value))}
          />
        </div>
      </div>
    )
  }

  if (t === 'array') {
    const arr = Array.isArray(value) ? (value as string[]) : []
    const isColor = /palette|colors?$/.test(name)
    return (
      <div>
        <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
        <Input
          id={id}
          placeholder={isColor ? '#1a1c2c, #5d275d, ...' : 'separados por vírgula'}
          defaultValue={arr.join(', ')}
          onBlur={(e) => {
            const items = e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
            set(items.length ? items : undefined)
          }}
        />
        {isColor && arr.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {arr.map((c) => (
              <span key={c} className="size-4 rounded-sm border" style={{ background: c }} title={c} />
            ))}
          </div>
        )}
      </div>
    )
  }

  // string
  const str = value === undefined ? '' : String(value)
  if (LONG_TEXT.has(name)) {
    return (
      <div>
        <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
        <Textarea id={id} rows={3} placeholder={placeholder} value={str} onChange={(e) => set(e.target.value)} />
      </div>
    )
  }
  const isHex = /color|outline/.test(name)
  return (
    <div>
      <FieldLabel name={name} schema={base} required={required} htmlFor={id} />
      <div className="flex items-center gap-2">
        {isHex && (
          <input
            type="color"
            className="size-8 shrink-0 cursor-pointer rounded border bg-transparent"
            value={/^#[0-9a-f]{6}$/i.test(str) ? str : '#000000'}
            onChange={(e) => set(e.target.value)}
          />
        )}
        <Input id={id} placeholder={placeholder} value={str} onChange={(e) => set(e.target.value)} />
      </div>
    </div>
  )
}

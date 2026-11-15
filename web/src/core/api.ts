// Cliente da API do Forge (forge_core/server.py).

export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  const?: unknown
  anyOf?: JsonSchema[]
  items?: JsonSchema
  properties?: Record<string, JsonSchema>
  required?: string[]
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  $ref?: string
  $defs?: Record<string, JsonSchema>
}

export type PipelineInfo = {
  id: string
  modality: string
  title: string
  description: string
  resources: string[]
  kind: string
  requires_input: boolean
  accepts_input: boolean
  same_asset: boolean
  params_schema: JsonSchema
}

export type ExporterInfo = { id: string; title: string; description: string; kinds: string[] | null; show_in_dialog?: boolean; params_schema: JsonSchema }

export type ImporterInfo = { id: string; modality: string; title: string; description: string; exts: string[]; kind: string; options_schema: JsonSchema | null }

export type ModalityInfo = { name: string; title: string; description: string; pipelines: PipelineInfo[]; exporters?: ExporterInfo[] }

export type Style = {
  prompt: string
  negative: string
  system_prompt: string
  refs: string[]
  loras: { name: string; strength: number }[]
  palette: string[]
  [k: string]: unknown
}

export type Category = { id: string; name: string; color: string }

export type FolderInfo = { path: string; name: string; depth: number; count: number; total: number }

export type Project = {
  slug: string
  path: string
  name: string
  description: string
  created: string
  style: Style
  folders: string[]
  categories: Category[]
  nsfw?: boolean
  nsfw_confirmed_at?: string | null
  souls?: { id: string; strength: number; enabled: boolean; modalities: string[] }[]
  [modality: string]: unknown
}

export type Soul = {
  id: string
  name: string
  description: string
  version: number
  status: 'empty' | 'building' | 'ready' | 'error'
  error: string | null
  source: { folder?: string; catalog?: string | null; include?: string[]; exclude?: string[] }
  license: string
  nsfw: boolean
  tags: string[]
  style: { prompt: string; negative: string; system_prompt: string }
  pixel: { is_pixel_art: boolean; scale: number; art_size: number; colors: number; outline: string | null; sizes: Record<string, { p10: number; median: number; p90: number; n: number }> }
  palette: string[]
  categories: Record<string, number>
  pinned: string[]
  count: number
  loras: { name: string; file: string; categories: string[]; base: string; template: string; cell_px: number; guide: boolean; notes?: string }[]
  has_cover: boolean
  progress: { value: number; message: string } | null
}

export type SoulRef = { id: string; stem: string; name: string; category: string; source: string; fw: number; fh: number; score?: number }
export type ProjectSoulRef = SoulRef & { soul: string; ref: string; url: string }

export type AssetFile = { path: string; role: string; mime: string; url: string }

export type AssetVersion = {
  slug: string
  version: number
  ref: string
  modality: string
  kind: string
  name: string
  pipeline: string | null
  prompt: string | null
  seed: number | null
  params: Record<string, unknown>
  parent: string | null
  files: AssetFile[]
  tags: string[]
  notes: string
  extra: Record<string, unknown>
  created: string
  folder?: string
  category?: string | null
}

export type AssetRow = {
  slug: string
  version: number
  ref: string
  modality: string
  kind: string
  name: string
  pipeline: string | null
  prompt: string
  tags: string[]
  parent: string | null
  primary_file: string | null
  preview_file: string | null
  primary_url: string | null
  preview_url: string | null
  created: string
  version_count: number
  folder: string
  category: string | null
}

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export type Job = {
  id: string
  request: { project: string; pipeline: string; params: Record<string, unknown>; asset?: string | null; name?: string | null; tags: string[] }
  status: JobStatus
  progress: number
  message: string | null
  result: string | null
  error: string | null
  created: string
  started: string | null
  finished: string | null
  log_tail: string[]
  source?: string
  retry_of?: string | null
}

export type Health = { ok: boolean; comfy: { url: string; online: boolean }; projects_dir: string; modality_errors: Record<string, string>; boot_id?: string; started?: string; supervised?: boolean; pipelines?: number }

// ---- opções: sistema e instâncias do ComfyUI ----
export type ComfyInstance = {
  name: string; port: number; title: string; url: string; script: string; installed: boolean; install_hint: string
  listening: boolean; ready: boolean; autostart: boolean; starting: boolean; log: string; pid?: number | null
  system?: { comfyui_version?: string; python_version?: string; pytorch_version?: string; ram_total?: number; ram_free?: number }
  devices?: { name: string; type: string; vram_total: number; vram_free: number }[]
  queue?: { running: number; pending: number }
}
export type SystemInfo = {
  boot_id: string; started: string; supervised: boolean; comfy_supervisor: { pid: number; ts: number; instances: Record<string, string> } | null
  host: string; port: number; comfy_url: string; paths: Record<string, string | null>; env: Record<string, string>
  python: string; platform: string; pipelines: number; modality_errors: Record<string, string>; active_jobs: string[]
}

export type LlmCatalogItem = { id: string; title: string; size_gb: number; description: string; capabilities: string[]; released?: string; recommended?: boolean; nsfw?: boolean }
export type LlmInfo = {
  url: string; online: boolean; version?: string; detail?: string; env_model?: string | null; chosen?: string | null; active?: string | null
  installed: { name: string; size: number; family?: string; params?: string; quant?: string }[]
  running: { name: string; size: number; vram: number; expires?: string }[]
  catalog: LlmCatalogItem[]
  others?: { id: string; title: string; backend: string; model: string | null; available: boolean; env: string; detail: string; used_by: string }[]
}
export type SetupItem = {
  id: string; group: string; title: string; description: string; needed_for: string; required: boolean; size: number
  link: string | null; manual: string | null; depends: string[]; can_install: boolean; install_label: string
  state: 'ok' | 'missing' | 'partial' | 'manual'; detail: string; have?: number
  files?: { name: string; folder: string; size: number; state: 'ok' | 'missing' | 'partial' | 'mismatch'; url: string }[]
  task?: SetupTask | null
}
export type SetupOverview = { models_dir: string; models_dir_exists: boolean; disk_free: number | null; winget: boolean; groups: { id: string; title: string; description: string }[]; items: SetupItem[] }
export type SetupTask = { id: string; item: string; title: string; kind: string; status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'; done: number; total: number; message: string; started: number; finished: number | null; log?: string[] }

export type WorkflowItem = {
  id: string | null; prompt_id: string | null; url: string; instance: string
  status: 'running' | 'pending' | 'done' | 'error' | 'rejected' | 'offline' | 'unknown'
  created: number | null; finished: number | null; error: string | null
  meta: { project?: string; pipeline?: string; asset?: string; version?: number; external?: boolean }
  nodes: number; summary: string[]; models: string[]
}

export type AIRole = { provider: 'local' | 'claude'; model: string; effort: string; configured: string }
export type AIInfo = {
  assistant: AIRole; chat: AIRole
  claude: { available: boolean; detail: string; models: string[]; key_saved: boolean; efforts: string[] }
  local: { available: boolean; url: string; models: string[]; default: string | null }
}
export type Diagnosis = {
  summary: string; cause: string; steps: string[]; confidence: string; provider: string; model: string; seconds: number
  fix: { kind: 'retry' | 'retry_with_params' | 'install' | 'start_instance' | 'restart_instance' | 'none'; label: string; params?: Record<string, unknown>; item?: string; instance?: string }
}
export type ChatAsset = { ref: string; slug: string; version: number; name: string; kind: string; modality: string; project: string; preview: string | null; preview_mime: string | null; primary: string | null; primary_mime: string | null; folder: string }
export type ChatMessage = {
  role: 'user' | 'assistant' | 'tool' | 'error'; text?: string; at?: number; model?: string; provider?: string
  tool_calls?: { id: string; name: string; input: Record<string, unknown> }[]
  tool_call_id?: string; name?: string; result?: string; is_error?: boolean; data?: { asset?: ChatAsset; job?: string }
}
export type ChatSummary = { id: string; title: string; created: number; updated: number; provider: string; model: string; messages: number; assets: number; running: boolean; error: string | null }
export type Chat = { id: string; title: string; created: number; updated: number; provider: 'local' | 'claude'; model: string; effort?: string; model_used?: string; messages: ChatMessage[]; running: boolean; error: string | null }

// ---- geração avançada (refine) ----
export type RefineActionSpec = { pipeline: string; when: string; hints: string; title: string; available: boolean; params_schema: JsonSchema | null }
export type RefinerInfo = { id: string; title: string; description: string; modality: string; default_criteria: string[]; actions: RefineActionSpec[] }
export type RefineAction = { pipeline: string; params: Record<string, unknown>; note: string }
export type RefineReview = { score: number; verdict: 'accept' | 'revise' | 'restart'; summary: string; issues: string[]; next_action: RefineAction | null; reviewer: string; created: string }
export type RefineIteration = {
  n: number; action: RefineAction; resolved_params: Record<string, unknown>; job_id: string | null; result: string | null
  error: string | null; previews: { path: string; label: string; url?: string }[]; review: RefineReview | null; started: string; finished: string | null
}
export type RefineBrief = { prompt: string; references: string[]; criteria: string[]; max_iterations: number; target_score: number; params: Record<string, unknown> }
export type RefineStatus = 'ready' | 'running' | 'awaiting_review' | 'done' | 'failed' | 'cancelled'
export type RefineSession = {
  id: string; project: string; refiner: string; title: string; brief: RefineBrief; critic: 'api' | 'manual'; asset: string
  status: RefineStatus; pending_action: RefineAction | null; iterations: RefineIteration[]; best: string | null; final: string | null
  error: string | null; message: string | null; created: string; updated: string
  iteration_count?: number; thumb?: string | null; last_score?: number | null
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface AnimationLibraryFile {
  id: string
  path: string
  joints: number
  root_motion: boolean
  clips: { name: string; duration: number }[]
}

export interface AnimationLibrary {
  folders: string[]
  searched: { path: string; exists: boolean }[]
  files: AnimationLibraryFile[]
  sets: Record<string, string[]>
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = r.statusText
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
    } catch {
      /* corpo não-JSON */
    }
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

const enc = encodeURIComponent

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  system: () => req<SystemInfo>('GET', '/api/system'),
  comfyInstances: () => req<ComfyInstance[]>('GET', '/api/comfy/instances'),
  comfyAction: (name: string, action: 'start' | 'stop' | 'restart' | 'free' | 'interrupt', force = false) =>
    req<Record<string, unknown>>('POST', `/api/comfy/instances/${enc(name)}/${action}`, action === 'stop' || action === 'restart' ? { force } : undefined),
  comfyAutostart: (name: string, value: boolean) => req<{ autostart: boolean }>('PUT', `/api/comfy/instances/${enc(name)}/autostart`, { value }),
  comfyLog: (name: string, lines = 300) => req<{ path: string; text: string }>('GET', `/api/comfy/instances/${enc(name)}/log?lines=${lines}`),
  llm: () => req<LlmInfo>('GET', '/api/llm'),
  llmChoose: (model: string | null) => req<LlmInfo>('PUT', '/api/llm/model', { model }),
  llmUnload: (model: string) => req<{ unloaded: string }>('POST', '/api/llm/unload', { model }),
  llmDelete: (model: string) => req<{ deleted: string }>('DELETE', `/api/llm/models/${model}`),
  animationLibrary: () => req<AnimationLibrary>('GET', '/api/3d/animation-library'),
  setAnimationLibraryFolders: (folders: string[]) => req<AnimationLibrary>('PUT', '/api/3d/animation-library', { folders }),
  setup: () => req<SetupOverview>('GET', '/api/setup'),
  setupHfToken: (token: string) => req<{ user: string; path: string }>('POST', '/api/setup/hf-token', { token }),
  setupTasks: () => req<SetupTask[]>('GET', '/api/setup/tasks'),
  setupTask: (id: string) => req<SetupTask>('GET', `/api/setup/tasks/${enc(id)}`),
  setupCancel: (id: string) => req<{ cancelled: boolean }>('POST', `/api/setup/tasks/${enc(id)}/cancel`),
  setupClear: () => req<{ cleared: number }>('DELETE', '/api/setup/tasks'),
  setupInstall: (item: string) => req<SetupTask>('POST', `/api/setup/items/${enc(item)}/install`),
  workflows: () => req<{ items: WorkflowItem[]; node_installed: Record<string, boolean> }>('GET', '/api/workflows'),
  workflowOpen: (id: string) => req<{ name: string; instance: string; url: string; open_url: string | null; hint?: string }>('POST', `/api/workflows/${enc(id)}/open`),
  workflowCancel: (id: string) => req<Record<string, string>>('POST', `/api/workflows/${enc(id)}/cancel`),
  workflowInstallNode: () => req<{ installed: string[]; restart_needed: boolean }>('POST', '/api/workflows/install-node'),
  ai: () => req<AIInfo>('GET', '/api/ai'),
  aiSetRole: (role: 'assistant' | 'chat', body: { provider?: string; model?: string; effort?: string }) => req<AIInfo>('PUT', `/api/ai/${role}`, body),
  aiSetKey: (key: string) => req<AIInfo>('PUT', '/api/ai-claude-key', { key }),
  aiDeleteKey: () => req<AIInfo>('DELETE', '/api/ai-claude-key'),
  diagnoseJob: (id: string, body?: { provider?: string; model?: string }) => req<Diagnosis>('POST', `/api/jobs/${enc(id)}/diagnose`, body ?? {}),
  chats: (project: string) => req<ChatSummary[]>('GET', `/api/projects/${enc(project)}/chats`),
  chat: (project: string, id: string) => req<Chat>('GET', `/api/projects/${enc(project)}/chats/${enc(id)}`),
  newChat: (project: string, body?: { provider?: string; model?: string }) => req<Chat>('POST', `/api/projects/${enc(project)}/chats`, body ?? {}),
  deleteChat: (project: string, id: string) => req<{ deleted: string }>('DELETE', `/api/projects/${enc(project)}/chats/${enc(id)}`),
  sendChat: (project: string, id: string, body: { text: string; provider?: string; model?: string; effort?: string }) =>
    req<{ ok: boolean }>('POST', `/api/projects/${enc(project)}/chats/${enc(id)}/messages`, body),
  cancelChat: (project: string, id: string) => req<{ cancelled: boolean }>('POST', `/api/projects/${enc(project)}/chats/${enc(id)}/cancel`),
  reloadServer: (force = false) => req<{ reloading: boolean; boot_id: string; interrupted: string[] }>('POST', '/api/admin/reload', { force }),
  modalities: () => req<ModalityInfo[]>('GET', '/api/modalities'),
  comfyModels: (folder: string) => req<string[]>('GET', `/api/comfy/models/${enc(folder)}`),

  projects: () => req<Project[]>('GET', '/api/projects'),
  project: (slug: string) => req<Project>('GET', `/api/projects/${enc(slug)}`),
  createProject: (body: { slug: string; name?: string; description?: string; style?: Partial<Style> }) =>
    req<Project>('POST', '/api/projects', body),
  patchProject: (slug: string, body: { name?: string; description?: string; style?: Style; modalities?: Record<string, Record<string, unknown>>; categories?: Category[] }) =>
    req<Project>('PATCH', `/api/projects/${enc(slug)}`, body),
  setNsfw: (slug: string, enabled: boolean, confirm_adult: boolean) =>
    req<Project>('POST', `/api/projects/${enc(slug)}/nsfw`, { enabled, confirm_adult }),
  localLlm: () => req<{ available: boolean; model?: string; detail?: string; url: string }>('GET', '/api/local-llm'),
  promptAssist: (slug: string, prompt: string, target: string) =>
    req<{ prompt: string }>('POST', `/api/projects/${enc(slug)}/prompt-assist`, { prompt, target }),
  souls: () => req<Soul[]>('GET', '/api/souls'),
  soul: (id: string) => req<Soul>('GET', `/api/souls/${enc(id)}`),
  createSoul: (body: { name: string; folder: string; include?: string[]; exclude?: string[]; license?: string; describe?: boolean }) =>
    req<Soul>('POST', '/api/souls', body),
  patchSoul: (id: string, body: { name?: string; description?: string; style?: Partial<Soul['style']>; pinned?: string[]; license?: string }) =>
    req<Soul>('PATCH', `/api/souls/${enc(id)}`, body),
  rebuildSoul: (id: string) => req<Soul>('POST', `/api/souls/${enc(id)}/rebuild`),
  captionSoul: (id: string) => req<Soul>('POST', `/api/souls/${enc(id)}/captions`),
  deleteSoul: (id: string) => req('DELETE', `/api/souls/${enc(id)}`),
  soulSearch: (id: string, q: string, category = '', k = 24) =>
    req<SoulRef[]>('GET', `/api/souls/${enc(id)}/search?${new URLSearchParams({ q, k: String(k), ...(category ? { category } : {}) })}`),
  importSoul: (file: File, name?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    if (name) fd.append('name', name)
    return req<Soul>('POST', '/api/souls/import', fd)
  },
  attachSoul: (slug: string, body: { id: string; strength?: number; enabled?: boolean; modalities?: string[] }) =>
    req<Project>('POST', `/api/projects/${enc(slug)}/souls`, body),
  detachSoul: (slug: string, id: string) => req<Project>('DELETE', `/api/projects/${enc(slug)}/souls/${enc(id)}`),
  projectSoulRefs: (slug: string, q: string, k = 4) =>
    req<ProjectSoulRef[]>('GET', `/api/projects/${enc(slug)}/soul-refs?${new URLSearchParams({ q, k: String(k) })}`),
  uploadRef: (slug: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<{ name: string; url: string }>('POST', `/api/projects/${enc(slug)}/refs`, fd)
  },
  deleteRef: (slug: string, name: string) => req('DELETE', `/api/projects/${enc(slug)}/refs/${enc(name)}`),

  /** folder: undefined = todas as pastas; '' = raiz; 'A/B' = pasta (recursive inclui subpastas). */
  assets: (slug: string, q: { q?: string; modality?: string; kind?: string; tag?: string; base?: string; folder?: string; recursive?: boolean; category?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(q).filter(([k, v]) => (k === 'folder' ? v !== undefined : Boolean(v))).map(([k, v]) => [k, String(v)]),
    )
    return req<AssetRow[]>('GET', `/api/projects/${enc(slug)}/assets?${qs}`)
  },
  versions: (slug: string, asset: string) => req<AssetVersion[]>('GET', `/api/projects/${enc(slug)}/assets/${enc(asset)}`),
  patchAsset: (slug: string, asset: string, version: number, body: { name?: string; tags?: string[]; notes?: string }) =>
    req<AssetVersion>('PATCH', `/api/projects/${enc(slug)}/assets/${enc(asset)}/${version}`, body),
  deleteAsset: (slug: string, asset: string, version?: number) =>
    req('DELETE', `/api/projects/${enc(slug)}/assets/${enc(asset)}${version ? `?version=${version}` : ''}`),
  importFormats: () => req<ImporterInfo[]>('GET', '/api/import/formats'),
  uploadAsset: (slug: string, file: Blob, opts: { asset?: string; parent?: string; modality?: string; kind?: string; name?: string; filename?: string; folder?: string; category?: string; options?: Record<string, unknown> } = {}) => {
    const fd = new FormData()
    fd.append('file', file, opts.filename ?? (file instanceof File ? file.name : 'image.png'))
    for (const k of ['asset', 'parent', 'modality', 'kind', 'name', 'folder', 'category'] as const) if (opts[k]) fd.append(k, opts[k]!)
    if (opts.options && Object.keys(opts.options).length) fd.append('options', JSON.stringify(opts.options))
    return req<AssetVersion>('POST', `/api/projects/${enc(slug)}/assets`, fd)
  },
  uploadTemp: (slug: string, file: Blob, filename = 'upload.png') => {
    const fd = new FormData()
    fd.append('file', file, filename)
    return req<{ path: string; url: string }>('POST', `/api/projects/${enc(slug)}/uploads`, fd)
  },

  exporters: (slug: string, asset: string, version: number) =>
    req<ExporterInfo[]>('GET', `/api/projects/${enc(slug)}/assets/${enc(asset)}/${version}/exporters`),
  /** Roda um exportador e devolve o arquivo (ou zip) como Blob + nome sugerido. */
  exportAsset: async (slug: string, asset: string, version: number, exporter: string, params: Record<string, unknown>) => {
    const r = await fetch(`/api/projects/${enc(slug)}/assets/${enc(asset)}/${version}/export/${enc(exporter)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!r.ok) {
      let msg = r.statusText
      try {
        msg = (await r.json()).detail ?? msg
      } catch {
        /* corpo não-JSON */
      }
      throw new ApiError(r.status, String(msg))
    }
    const cd = r.headers.get('content-disposition') ?? ''
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd)
    return { blob: await r.blob(), filename: m ? decodeURIComponent(m[1]) : `export-${asset}` }
  },

  folders: (slug: string) => req<{ folders: FolderInfo[]; root_count: number; total: number }>('GET', `/api/projects/${enc(slug)}/folders`),
  createFolder: (slug: string, path: string) => req<{ path: string }>('POST', `/api/projects/${enc(slug)}/folders`, { path }),
  renameFolder: (slug: string, old: string, newPath: string) =>
    req<{ moved: number; path: string }>('POST', `/api/projects/${enc(slug)}/folders/rename`, { old, new: newPath }),
  deleteFolder: (slug: string, path: string) =>
    req<{ moved: number; moved_to: string }>('DELETE', `/api/projects/${enc(slug)}/folders?path=${enc(path)}`),
  organize: (slug: string, body: { slugs: string[]; folder?: string; category?: string; clear_category?: boolean }) =>
    req<{ updated: string[] }>('POST', `/api/projects/${enc(slug)}/assets/organize`, body),

  refiners: () => req<{ refiners: RefinerInfo[]; api_critic: { available: boolean; detail: string } }>('GET', '/api/refiners'),
  refineSessions: (slug: string) => req<RefineSession[]>('GET', `/api/projects/${enc(slug)}/refine`),
  refineSession: (slug: string, id: string) => req<RefineSession>('GET', `/api/projects/${enc(slug)}/refine/${enc(id)}`),
  refineStart: (slug: string, body: { refiner: string; brief: Partial<RefineBrief> & { prompt: string }; critic: 'api' | 'manual'; title?: string; asset?: string; folder?: string; category?: string }) =>
    req<RefineSession>('POST', `/api/projects/${enc(slug)}/refine`, body),
  refineReview: (slug: string, id: string, body: { score: number; verdict: string; summary?: string; issues?: string[]; next_action?: RefineAction | null; reviewer?: string }) =>
    req<RefineSession>('POST', `/api/projects/${enc(slug)}/refine/${enc(id)}/review`, body),
  refineResume: (slug: string, id: string, critic?: 'api' | 'manual') =>
    req<RefineSession>('POST', `/api/projects/${enc(slug)}/refine/${enc(id)}/resume`, critic ? { critic } : {}),
  refineCancel: (slug: string, id: string) => req<RefineSession>('POST', `/api/projects/${enc(slug)}/refine/${enc(id)}/cancel`),

  jobs: (q: { project?: string; status?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]))
    return req<Job[]>('GET', `/api/jobs?${qs}`)
  },
  retryJob: (id: string, body: { params?: Record<string, unknown>; new_seed?: boolean } = {}) =>
    req<Job>('POST', `/api/jobs/${enc(id)}/retry`, body),
  deleteJob: (id: string) => req('DELETE', `/api/jobs/${enc(id)}`),
  clearJobs: (q: { status?: string; project?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][])
    return req<{ deleted: number }>('DELETE', `/api/jobs?${qs}`)
  },
  job: (id: string) => req<Job & { logs: string[] }>('GET', `/api/jobs/${enc(id)}`),
  submitJob: (body: { project: string; pipeline: string; params: Record<string, unknown>; asset?: string; name?: string; tags?: string[]; folder?: string; category?: string }) =>
    req<Job>('POST', '/api/jobs', body),
  cancelJob: (id: string) => req<Job>('POST', `/api/jobs/${enc(id)}/cancel`),
}

export function primaryFile(v: AssetVersion): AssetFile | undefined {
  return v.files.find((f) => f.role === 'primary') ?? v.files[0]
}

export function previewFile(v: AssetVersion): AssetFile | undefined {
  return v.files.find((f) => f.role === 'preview') ?? v.files.find((f) => f.role === 'primary' && f.mime.startsWith('image/'))
}

export function downloadBlob(blob: Blob, filename: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 5000)
}
// ---- pixel art (modalidade "pixel", forge_pixel) ----

/** Mapa de pixels editável (pixelmap.json). rows: de cima para baixo, H strings de W chars; "." = transparente. */
export type PixelMap = {
  format: 'asset-forge-pixelmap/1'
  width: number
  height: number
  legend: Record<string, string>
  rows: string[]
  outline: { auto: boolean; color: string }
  side: null | 'L' | 'R'
  shade_L: number
}

/** Especificação do asset. pivot e safe_area têm origem no canto INFERIOR esquerdo (convenção Unity). */
export type PixelSpec = {
  name: string
  kind: 'part' | 'tile' | 'icon' | 'enemy' | 'ui' | 'fx' | 'weapon'
  canvas: [number, number]
  pivot: [number, number]
  safe_area: [number, number, number, number] | null
  outline: 'required' | 'none' | 'optional'
  outline_color: string
  alpha: 'binary' | 'allow'
  palette_group: string | null
  max_colors: number | null
  tileable: boolean
  coverage: [number, number] | null
  min_island: number
  anchor: 'bottom' | 'top' | 'center'
}

export type PixelCheck = { id: string; ok: boolean; elim: boolean; detail: string; data?: unknown }
export type PixelReport = { ok: boolean; checks: PixelCheck[] }
export type PixelPalette = { core: string[]; groups: Record<string, { cores: string[] }> }
export type PixelConfig = { palette: PixelPalette; specs: Record<string, PixelSpec> }

export const pixelApi = {
  config: (slug: string) => req<PixelConfig>('GET', `/api/projects/${enc(slug)}/pixel/config`),
  /** Valida o mapa renderizado sem salvar. */
  validate: (slug: string, body: { map: PixelMap; spec: string | PixelSpec; palette_group?: string | null }) =>
    req<PixelReport>('POST', `/api/projects/${enc(slug)}/pixel/validate`, body),
}

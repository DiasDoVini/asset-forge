// Abrir um asset no editor de sprite e salvar o documento como nova versão (via .aseprite).
import { api, type AssetVersion, type Project } from '@/core/api'
import { readAseprite, writeAseprite } from './aseprite'
import { docFromFrames, type SpriteDoc, type Tag } from './doc'

async function decode(url: string): Promise<{ w: number; h: number; data: Uint8ClampedArray }> {
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.src = url
  await img.decode()
  const c = document.createElement('canvas')
  c.width = img.naturalWidth
  c.height = img.naturalHeight
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.drawImage(img, 0, 0)
  return { w: c.width, h: c.height, data: g.getImageData(0, 0, c.width, c.height).data }
}

export type Loaded = { doc: SpriteDoc; from: 'aseprite' | 'animation' | 'image'; skipped: string[] }

/** Prioridade: o .aseprite guardado na versão (camadas preservadas) > animation.json > imagem primária. */
export async function loadDoc(asset: AssetVersion): Promise<Loaded> {
  const ase = asset.files.find((f) => f.path.toLowerCase().endsWith('.aseprite') || f.path.toLowerCase().endsWith('.ase'))
  if (ase) {
    const buf = await fetch(ase.url).then((r) => r.arrayBuffer())
    const { doc, skipped } = await readAseprite(buf)
    return { doc, from: 'aseprite', skipped }
  }
  const anim = asset.files.find((f) => f.path === 'animation.json')
  if (anim) {
    const data = (await fetch(anim.url).then((r) => r.json())) as {
      frames: { file: string; duration: number }[]
      tags?: { name: string; from: number; to: number; direction?: string; repeat?: number }[]
    }
    const base = anim.url.slice(0, anim.url.length - 'animation.json'.length)
    const frames = await Promise.all(data.frames.map((f) => decode(base + f.file)))
    const tags: Tag[] = (data.tags ?? []).map((t, i) => ({
      name: t.name, from: t.from, to: t.to, repeat: t.repeat ?? 0,
      direction: (['forward', 'reverse', 'pingpong', 'pingpong_reverse'].includes(t.direction ?? '') ? t.direction : 'forward') as Tag['direction'],
      color: ['#f59e0b', '#22c55e', '#3b82f6', '#ec4899'][i % 4],
    }))
    const { w, h } = frames[0]
    return { doc: docFromFrames(w, h, frames.map((f) => f.data), data.frames.map((f) => f.duration), tags), from: 'animation', skipped: [] }
  }
  const prim = asset.files.find((f) => f.role === 'primary' && f.mime.startsWith('image/')) ?? asset.files.find((f) => f.mime.startsWith('image/'))
  if (!prim) throw new Error('este asset não tem imagem para editar')
  const img = await decode(prim.url)
  return { doc: docFromFrames(img.w, img.h, [img.data], [100]), from: 'image', skipped: [] }
}

export async function docBlob(doc: SpriteDoc): Promise<Blob> {
  return new Blob([(await writeAseprite(doc)) as BlobPart], { type: 'application/octet-stream' })
}

/** Salva como nova versão do asset: o servidor importa o .aseprite (animação ou imagem + o arquivo como fonte). */
export async function saveAsVersion(project: Project, asset: AssetVersion, doc: SpriteDoc): Promise<AssetVersion> {
  return api.uploadAsset(project.slug, await docBlob(doc), { asset: asset.slug, parent: asset.ref, name: asset.name, filename: `${asset.slug}.aseprite` })
}

/** Cria um asset novo a partir do documento (ex.: "Novo sprite"). */
export async function saveAsNew(project: Project, doc: SpriteDoc, name: string, folder?: string): Promise<AssetVersion> {
  return api.uploadAsset(project.slug, await docBlob(doc), { name, folder, filename: `${name || 'sprite'}.aseprite` })
}

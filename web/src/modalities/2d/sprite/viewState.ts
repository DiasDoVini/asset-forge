// Estado de exibição do editor (não vai para o .aseprite): modo tiled, grade, encaixe, réguas e guias, imagem de
// referência e prévia flutuante. Preferências gerais ficam no navegador; guias e referência, por asset.
import { useEffect, useState } from 'react'

export type Tiled = 'none' | 'x' | 'y' | 'both'
export type Reference = { src: string; name: string; x: number; y: number; scale: number; opacity: number; above: boolean; visible: boolean }
export type Guides = { x: number[]; y: number[] }
export type PreviewWin = { open: boolean; x: number; y: number; zoom: number; playing: boolean }

export type ViewState = {
  tiled: Tiled
  showGrid: boolean // grade do sprite (doc.grid)
  snap: boolean // encaixar seleção/transformação/fatias na grade
  rulers: boolean
  guides: Guides
  reference: Reference | null
  refPanel: boolean
  preview: PreviewWin
}

const GLOBAL = 'asset-forge.sprite-view'
const perAsset = (slug: string) => `asset-forge.sprite-view.${slug}`

const read = <T,>(k: string): Partial<T> => {
  try {
    return JSON.parse(localStorage.getItem(k) ?? '{}') as Partial<T>
  } catch {
    return {}
  }
}
const write = (k: string, v: unknown) => {
  try {
    localStorage.setItem(k, JSON.stringify(v))
  } catch {
    /* storage cheio/bloqueado: fica só na sessão */
  }
}

export function useViewState(assetSlug: string) {
  const [v, setV] = useState<ViewState>(() => ({
    tiled: 'none', showGrid: false, snap: false, rulers: false, refPanel: false,
    preview: { open: false, x: 16, y: 16, zoom: 2, playing: true },
    guides: { x: [], y: [] }, reference: null,
    ...read<ViewState>(GLOBAL),
    ...read<ViewState>(perAsset(assetSlug)),
  }))
  useEffect(() => {
    const { guides, reference, refPanel: _r, ...global } = v
    write(GLOBAL, global)
    write(perAsset(assetSlug), { guides, reference })
  }, [v, assetSlug])
  const set = <K extends keyof ViewState>(k: K, val: ViewState[K]) => setV((s) => ({ ...s, [k]: val }))
  return { view: v, setView: set }
}

// Histórico de desfazer do documento. Como o documento é imutável (cels copy-on-write), cada passo guarda só
// a referência ao documento anterior.
import { useCallback, useRef, useState } from 'react'
import type { SpriteDoc } from './doc'

const LIMIT = 200

export function useDocHistory() {
  const [doc, setDoc] = useState<SpriteDoc | null>(null)
  const cur = useRef<SpriteDoc | null>(null)
  const past = useRef<SpriteDoc[]>([])
  const future = useRef<SpriteDoc[]>([])
  const [saved, setSaved] = useState<SpriteDoc | null>(null)
  const [depth, setDepth] = useState({ undo: 0, redo: 0 })

  const show = (d: SpriteDoc | null) => {
    cur.current = d
    setDoc(d)
    setDepth({ undo: past.current.length, redo: future.current.length })
  }

  const lastMerge = useRef<{ key: string; at: number } | null>(null)

  /** Grava um passo. Com `mergeKey`, passos seguidos com a mesma chave (ex.: arrastar um slider) viram um só. */
  const commit = useCallback((next: SpriteDoc, mergeKey?: string) => {
    if (cur.current === next) return
    const now = Date.now()
    const m = lastMerge.current
    lastMerge.current = mergeKey ? { key: mergeKey, at: now } : null
    if (mergeKey && m && m.key === mergeKey && now - m.at < 1500 && past.current.length) {
      future.current = []
      show(next)
      return
    }
    if (cur.current) {
      past.current.push(cur.current)
      if (past.current.length > LIMIT) past.current.shift()
    }
    future.current = []
    show(next)
  }, [])

  const reset = useCallback((next: SpriteDoc) => {
    past.current = []
    future.current = []
    setSaved(next)
    show(next)
  }, [])

  const undo = useCallback(() => {
    const prev = past.current.pop()
    if (!prev || !cur.current) return
    future.current.push(cur.current)
    show(prev)
  }, [])

  const redo = useCallback(() => {
    const next = future.current.pop()
    if (!next || !cur.current) return
    past.current.push(cur.current)
    show(next)
  }, [])

  const markSaved = useCallback(() => setSaved(cur.current), [])

  return {
    doc,
    /** documento atual sem esperar o re-render (para handlers encadeados) */
    current: () => cur.current,
    commit,
    reset,
    undo,
    redo,
    markSaved,
    canUndo: depth.undo > 0,
    canRedo: depth.redo > 0,
    dirty: doc !== null && doc !== saved,
  }
}

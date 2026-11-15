// Estado da grade com desfazer/refazer (até HISTORY_LIMIT passos).
import { useCallback, useRef, useState } from 'react'
import type { Grid } from './render'

export const HISTORY_LIMIT = 200

export function useGridHistory(initial: Grid) {
  const [grid, setGrid] = useState(initial)
  const ref = useRef(initial)
  const past = useRef<Grid[]>([])
  const future = useRef<Grid[]>([])
  const [, setTick] = useState(0)

  /** Troca a grade sem registrar no histórico (traço em andamento). */
  const set = useCallback((g: Grid) => {
    ref.current = g
    setGrid(g)
  }, [])

  /** Registra `before` como passo desfazível (fim de um traço). */
  const record = useCallback((before: Grid) => {
    past.current.push(before)
    if (past.current.length > HISTORY_LIMIT) past.current.shift()
    future.current = []
    setTick((t) => t + 1)
  }, [])

  /** Troca a grade como um passo desfazível. */
  const commit = useCallback(
    (g: Grid) => {
      if (g === ref.current) return
      record(ref.current)
      set(g)
    },
    [record, set],
  )

  const step = useCallback(
    (back: boolean) => {
      const from = back ? past.current : future.current
      const to = back ? future.current : past.current
      const g = from.pop()
      if (!g) return
      to.push(ref.current)
      set(g)
      setTick((t) => t + 1)
    },
    [set],
  )

  return {
    grid,
    ref,
    set,
    record,
    commit,
    undo: () => step(true),
    redo: () => step(false),
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
  }
}

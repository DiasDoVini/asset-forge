// WebSocket /api/events -> invalida caches do react-query e notifica ouvintes.
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Job } from './api'

export type ForgeEvent =
  | { type: 'job'; job: Job }
  | { type: 'log'; job_id: string; line: string }
  | { type: 'asset'; project: string; ref: string }
  | { type: 'asset_deleted'; project: string; ref: string }
  | { type: 'refine'; project: string; id: string; status: string; message: string | null }
  | { type: 'server_reloading' }
  | { type: 'chat'; project: string; chat: string }
  | { type: 'soul'; id: string; status?: string; error?: string; progress?: { value: number; message: string } }

type Listener = (e: ForgeEvent) => void
const listeners = new Set<Listener>()

export function onForgeEvent(fn: Listener) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function useForgeEvents() {
  const qc = useQueryClient()
  useEffect(() => {
    let ws: WebSocket | null = null
    let stop = false
    let retry: number | undefined
    let connectedOnce = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/api/events`)
      ws.onopen = () => {
        // reconectou (ex.: servidor recarregado): atualiza tudo, inclusive pipelines novos
        if (connectedOnce) qc.invalidateQueries()
        connectedOnce = true
      }
      ws.onmessage = (m) => {
        const e = JSON.parse(m.data) as ForgeEvent
        if (e.type === 'job') {
          qc.setQueryData<Job[]>(['jobs'], (old) => {
            const list = old ? [...old] : []
            const i = list.findIndex((j) => j.id === e.job.id)
            if (i >= 0) list[i] = e.job
            else list.unshift(e.job)
            return list
          })
        }
        if (e.type === 'asset' || e.type === 'asset_deleted') {
          qc.invalidateQueries({ queryKey: ['assets', e.project] })
          qc.invalidateQueries({ queryKey: ['folders', e.project] })
          qc.invalidateQueries({ queryKey: ['versions', e.project, e.ref.split('@')[0]] })
        }
        if (e.type === 'chat') {
          qc.invalidateQueries({ queryKey: ['chat', e.project, e.chat] })
          qc.invalidateQueries({ queryKey: ['chats', e.project] })
        }
        if (e.type === 'soul') {
          if (e.progress) {
            qc.setQueryData(['soul', e.id], (old: Record<string, unknown> | undefined) => (old ? { ...old, progress: e.progress } : old))
          } else {
            qc.invalidateQueries({ queryKey: ['soul', e.id] })
            qc.invalidateQueries({ queryKey: ['souls'] })
          }
        }
        if (e.type === 'refine') {
          qc.invalidateQueries({ queryKey: ['refine', e.project] })
        }
        listeners.forEach((fn) => fn(e))
      }
      ws.onclose = () => {
        if (!stop) retry = window.setTimeout(connect, 1500)
      }
    }
    connect()
    return () => {
      stop = true
      window.clearTimeout(retry)
      ws?.close()
    }
  }, [qc])
}

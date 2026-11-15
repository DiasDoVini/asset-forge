// Atalhos configuráveis do editor de sprite (como Editar ▸ Atalhos do Aseprite / Pixelorama). Cada ação tem uma ou mais
// combinações ("Ctrl+Shift+Z"); o usuário troca no diálogo e fica salvo no navegador. Teclas de contexto (Espaço para
// arrastar, Esc, Enter fechando polígono, Delete, Tab no tilemap) continuam fixas.
import { useEffect, useState } from 'react'
import { TOOL_DEFS } from './tools'

export type Action = { id: string; title: string; group: string; keys: string[] }

const A = (group: string, id: string, title: string, ...keys: string[]): Action => ({ id, title, group, keys })

export const ACTIONS: Action[] = [
  A('Arquivo', 'save', 'Salvar versão', 'Ctrl+S'),
  A('Editar', 'undo', 'Desfazer', 'Ctrl+Z'),
  A('Editar', 'redo', 'Refazer', 'Ctrl+Y', 'Ctrl+Shift+Z'),
  A('Editar', 'copy', 'Copiar', 'Ctrl+C'),
  A('Editar', 'copy_merged', 'Copiar mesclado (todas as camadas)', 'Ctrl+Shift+C'),
  A('Editar', 'cut', 'Recortar', 'Ctrl+X'),
  A('Editar', 'paste', 'Colar', 'Ctrl+V'),
  A('Editar', 'transform', 'Transformar seleção', 'Ctrl+T'),
  A('Seleção', 'select_all', 'Selecionar tudo', 'Ctrl+A'),
  A('Seleção', 'deselect', 'Desmarcar', 'Ctrl+D'),
  A('Seleção', 'invert_sel', 'Inverter seleção', 'Ctrl+Shift+I'),
  A('Quadros', 'new_frame', 'Novo quadro vazio', 'Alt+N'),
  A('Quadros', 'dup_frame', 'Duplicar quadro', 'Alt+D'),
  A('Quadros', 'prev_frame', 'Quadro anterior', ','),
  A('Quadros', 'next_frame', 'Próximo quadro', '.'),
  A('Quadros', 'first_frame', 'Primeiro quadro', 'Home'),
  A('Quadros', 'last_frame', 'Último quadro', 'End'),
  A('Quadros', 'play', 'Tocar / pausar', 'Enter'),
  A('Quadros', 'onion', 'Onion skin', 'F3'),
  A('Camadas', 'new_layer', 'Nova camada', 'Shift+N'),
  A('Cor', 'swap_colors', 'Trocar cor principal e secundária', 'X'),
  A('Exibir', 'zoom_in', 'Mais zoom', '+', '='),
  A('Exibir', 'zoom_out', 'Menos zoom', '-'),
  A('Pincel', 'brush_smaller', 'Pincel menor', '['),
  A('Pincel', 'brush_bigger', 'Pincel maior', ']'),
  ...TOOL_DEFS.map((t) => A('Ferramentas', `tool:${t.id}`, t.label.replace(/ \(.*\)$/, ''), `${t.shift ? 'Shift+' : ''}${t.key.toUpperCase()}`)),
]

export type Bindings = Record<string, string[]>

const STORE = 'asset-forge:sprite-shortcuts'
const MODS = ['Control', 'Shift', 'Alt', 'Meta', 'AltGraph', 'CapsLock', 'Dead']
const NAMED: Record<string, string> = { ' ': 'Space', arrowleft: 'Left', arrowright: 'Right', arrowup: 'Up', arrowdown: 'Down', escape: 'Esc' }

/** Combinação normalizada de um evento ("Ctrl+Shift+Z"); null se só modificadores. Cmd no Mac conta como Ctrl. */
export function comboOf(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>): string | null {
  if (MODS.includes(e.key)) return null
  const low = e.key.toLowerCase()
  const key = NAMED[low] ?? (e.key.length === 1 ? e.key.toUpperCase() : e.key[0].toUpperCase() + e.key.slice(1))
  // Shift muda o caractere de símbolos ("{" em vez de "["): nesses casos o Shift já está "dentro" da tecla
  const symbolShifted = e.shiftKey && e.key.length === 1 && !/[a-z0-9]/i.test(e.key)
  return [e.ctrlKey || e.metaKey ? 'Ctrl' : '', e.altKey ? 'Alt' : '', e.shiftKey && !symbolShifted ? 'Shift' : '', key].filter(Boolean).join('+')
}

export const defaults = (): Bindings => Object.fromEntries(ACTIONS.map((a) => [a.id, [...a.keys]]))

export function loadBindings(): Bindings {
  const b = defaults()
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) ?? '{}') as Bindings
    for (const [id, keys] of Object.entries(saved)) if (id in b && Array.isArray(keys)) b[id] = keys.filter((k) => typeof k === 'string')
  } catch {
    // sem armazenamento (janela privada etc.): fica nos padrões
  }
  return b
}

/** Guarda só o que difere dos padrões (novas ações em versões futuras pegam o padrão). */
export function saveBindings(b: Bindings) {
  const d = defaults()
  const diff = Object.fromEntries(Object.entries(b).filter(([id, k]) => JSON.stringify(k) !== JSON.stringify(d[id])))
  try {
    if (Object.keys(diff).length) localStorage.setItem(STORE, JSON.stringify(diff))
    else localStorage.removeItem(STORE)
  } catch {
    // ignora
  }
  window.dispatchEvent(new Event(STORE))
}

/** combinação → ação (a primeira na ordem de ACTIONS ganha se houver conflito). */
export function keymap(b: Bindings): Map<string, string> {
  const m = new Map<string, string>()
  for (const a of ACTIONS) for (const k of b[a.id] ?? []) if (!m.has(k)) m.set(k, a.id)
  return m
}

/** Combinações usadas por mais de uma ação. */
export function conflicts(b: Bindings): Map<string, string[]> {
  const uses = new Map<string, string[]>()
  for (const a of ACTIONS) for (const k of b[a.id] ?? []) uses.set(k, [...(uses.get(k) ?? []), a.id])
  return new Map([...uses].filter(([, ids]) => ids.length > 1))
}

export const keyHint = (b: Bindings, id: string) => (b[id]?.length ? b[id].join(' / ') : 'sem atalho')

export function useBindings(): [Bindings, (b: Bindings) => void] {
  const [b, setB] = useState(loadBindings)
  useEffect(() => {
    const on = () => setB(loadBindings())
    window.addEventListener(STORE, on)
    window.addEventListener('storage', on)
    return () => {
      window.removeEventListener(STORE, on)
      window.removeEventListener('storage', on)
    }
  }, [])
  return [b, (n: Bindings) => {
    setB(n)
    saveBindings(n)
  }]
}

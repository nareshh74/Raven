/* Conversation autonomy: a persisted session choice and a one-message override. */

import { t } from '../i18n/t'
import { gateway } from '../rpc/gateway'
import { viewRuntime } from './session/registry'
import { show as toast } from './toast'

import type { SessionRuntime } from './session/runtime'

export type AutonomyMode = 'manual' | 'balanced' | 'auto'
export const MODES: readonly AutonomyMode[] = ['manual', 'balanced', 'auto']
const KEY = 'agents.defaults.autonomyMode'

interface AutonomyState {
  readonly loaded: boolean
  readonly session: AutonomyMode | null
  readonly turn: AutonomyMode | null
  readonly saving: boolean
  readonly error: string | null
}

const empty: AutonomyState = { loaded: false, session: null, turn: null, saving: false, error: null }
let states = new WeakMap<SessionRuntime, AutonomyState>()
let pending = new WeakMap<SessionRuntime, Promise<void>>()
let reads = new WeakMap<SessionRuntime, number>()
let staged = new WeakSet<SessionRuntime>()
const listeners = new Set<() => void>()

export const get = (): AutonomyState => state(viewRuntime())
const state = (rt: SessionRuntime): AutonomyState => states.get(rt) ?? empty
export function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
function set(rt: SessionRuntime, patch: Partial<AutonomyState>): void {
  states.set(rt, { ...state(rt), ...patch })
  for (const fn of listeners) fn()
}

export async function load(rt: SessionRuntime = viewRuntime()): Promise<void> {
  if (state(rt).saving) return
  const ticket = (reads.get(rt) ?? 0) + 1
  reads.set(rt, ticket)
  try {
    const reply = await gateway().call('config.get', {
      keys: [KEY], ...(rt.key ? { session_id: rt.key } : {}),
    })
    const mode = reply.config[KEY]
    if (mode !== 'manual' && mode !== 'balanced' && mode !== 'auto') {
      throw new Error(t('gui.autonomy.unavailable'))
    }
    if (reads.get(rt) !== ticket) return
    // A draft's local pick has no server record until promotion.
    set(rt, { loaded: true, session: rt.key ? mode : state(rt).session ?? mode, error: null })
  } catch (error) {
    if (reads.get(rt) !== ticket) return
    set(rt, { error: error instanceof Error ? error.message : String(error) })
  }
}

export function pickTurn(mode: AutonomyMode | null): void {
  set(viewRuntime(), { turn: mode })
}

export async function pickSession(mode: AutonomyMode): Promise<void> {
  const rt = viewRuntime()
  if (state(rt).saving) return
  reads.set(rt, (reads.get(rt) ?? 0) + 1)
  if (!rt.key) {
    staged.add(rt)
    set(rt, { loaded: true, session: mode, error: null })
    return
  }
  set(rt, { saving: true, error: null })
  const saving = write(rt.key, mode).then(() => {
    staged.delete(rt)
    set(rt, { loaded: true, session: mode })
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : String(error)
    set(rt, { error: detail })
    toast(t('gui.autonomy.failed', { detail }))
    throw error
  }).finally(() => {
    pending.delete(rt)
    set(rt, { saving: false })
  })
  pending.set(rt, saving)
  // The pending promise also gates sends; report refusals without an unhandled rejection.
  await saving.catch(() => {})
}

async function write(sessionId: string, mode: AutonomyMode): Promise<void> {
  const reply = await gateway().call('config.set', { key: KEY, value: mode, scope: 'session', session_id: sessionId })
  if (!reply.applied) throw new Error(t('gui.autonomy.refused'))
}

export const ready = (rt: SessionRuntime): Promise<void> | undefined =>
  pending.get(rt) ?? (rt.key ? applyDraft(rt, rt.key) : undefined)

export function applyDraft(rt: SessionRuntime, sessionId: string): Promise<void> | undefined {
  const mode = state(rt).session
  if (!staged.has(rt) || !mode) return
  set(rt, { saving: true, error: null })
  const saving = write(sessionId, mode).then(() => {
    staged.delete(rt)
    set(rt, { error: null })
  }).catch(async (error: unknown) => {
    staged.delete(rt)
    const detail = error instanceof Error ? error.message : String(error)
    set(rt, { loaded: false, session: null, saving: false, error: detail })
    toast(t('gui.autonomy.failed', { detail }))
    await load(rt)
  }).finally(() => {
    pending.delete(rt)
    set(rt, { saving: false })
  })
  pending.set(rt, saving)
  return saving
}

/** Snapshot at send time, so queued messages and retries keep their own choice. */
export function takeTurn(): AutonomyMode | undefined {
  const rt = viewRuntime()
  const mode = state(rt).turn ?? undefined
  if (mode) set(rt, { turn: null })
  return mode
}

export function _resetForTests(): void {
  states = new WeakMap()
  pending = new WeakMap()
  reads = new WeakMap()
  staged = new WeakSet()
  listeners.clear()
}

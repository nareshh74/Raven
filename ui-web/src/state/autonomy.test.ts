// @vitest-environment happy-dom
/* Session autonomy writes, draft promotion, and pending-write isolation. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FixtureTransport } from '../rpc/fixtureTransport'
import { setGateway } from '../rpc/gateway'
import * as autonomy from './autonomy'
import { _resetForTests as resetRegistry, adopt } from './session/registry'

vi.mock('./toast', () => ({ show: vi.fn() }))

const KEY = 'agents.defaults.autonomyMode'
let transport: FixtureTransport

beforeEach(() => {
  resetRegistry()
  autonomy._resetForTests()
  adopt('autonomy-one')
  transport = new FixtureTransport({
    'config.get': { config: { [KEY]: 'balanced' } },
    'config.set': { applied: true, previous: null },
  })
  setGateway(transport)
})

afterEach(() => { setGateway(null) })

describe('conversation autonomy state', () => {
  it('persists only to the selected session and keeps another session independent', async () => {
    await autonomy.load()
    await autonomy.pickSession('auto')
    expect(transport.calls.at(-1)).toEqual({
      method: 'config.set', params: { key: KEY, value: 'auto', scope: 'session', session_id: 'autonomy-one' },
    })
    adopt('autonomy-two')
    expect(autonomy.get().loaded).toBe(false)
    await autonomy.load()
    expect(autonomy.get().session).toBe('balanced')
    adopt('autonomy-one')
    expect(autonomy.get().session).toBe('auto')
  })

  it('stages a draft pick until promotion and never writes an unpicked default', async () => {
    const rt = adopt(null)
    await autonomy.load()
    expect(autonomy.applyDraft(rt, 'made')).toBeUndefined()
    await autonomy.pickSession('manual')
    expect(transport.calls.filter((c) => c.method === 'config.set')).toEqual([])
    await autonomy.applyDraft(rt, 'made')
    expect(transport.calls.at(-1)?.params).toEqual({ key: KEY, value: 'manual', scope: 'session', session_id: 'made' })
    expect(autonomy.applyDraft(rt, 'made')).toBeUndefined()
  })

  it('clears staged choices and subscribers when reset with a reused runtime', async () => {
    const rt = adopt(null)
    await autonomy.pickSession('manual')
    const listener = vi.fn()
    autonomy.subscribe(listener)
    autonomy._resetForTests()
    await autonomy.load()
    expect(autonomy.applyDraft(rt, 'made')).toBeUndefined()
    expect(listener).not.toHaveBeenCalled()
  })

  it('does not claim a refused write succeeded, and a waiting send rejects', async () => {
    await autonomy.load()
    let refuse: (reply: { applied: boolean; previous: null }) => void = () => {}
    setGateway(new FixtureTransport({
      'config.set': () => new Promise((resolve) => { refuse = resolve }),
    }))
    const rt = adopt('autonomy-one')
    const saving = autonomy.pickSession('auto')
    const waiting = autonomy.ready(rt)
    const rejected = expect(waiting).rejects.toThrow()
    refuse({ applied: false, previous: null })
    await saving
    await rejected
    expect(autonomy.get().session).toBe('balanced')
    expect(autonomy.get().error).toBeTruthy()
    expect(autonomy.get().saving).toBe(false)
  })

  it('does not let a stale read overwrite a newer pick or a different conversation', async () => {
    let answer: (reply: { config: Record<string, string> }) => void = () => {}
    setGateway(new FixtureTransport({
      'config.get': () => new Promise((resolve) => { answer = resolve }),
      'config.set': { applied: true, previous: null },
    }))
    const loading = autonomy.load()
    await autonomy.pickSession('auto')
    adopt('autonomy-two')
    answer({ config: { [KEY]: 'manual' } })
    await loading
    expect(autonomy.get().loaded).toBe(false)
    adopt('autonomy-one')
    expect(autonomy.get().session).toBe('auto')
  })
})

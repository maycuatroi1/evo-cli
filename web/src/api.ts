import { useCallback, useEffect, useRef, useState } from 'react'
import type { GitOverlay, PlanPayload, State } from './types'

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  const body = await response.json().catch(() => ({ error: `${response.status} ${response.statusText}` }))
  if (!response.ok) throw new Error((body as { error?: string }).error ?? `${response.status}`)
  return body as T
}

export const fetchState = () => get<State>('api/state')
export const fetchPlan = (id: string) => get<PlanPayload>(`api/plans/${encodeURIComponent(id)}`)
export const fetchGit = (id: string, refetch = false) =>
  get<GitOverlay>(`api/plans/${encodeURIComponent(id)}/git${refetch ? '?fetch=1' : ''}`)

interface Async<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): Async<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const latest = useRef(0)

  useEffect(() => {
    const ticket = ++latest.current
    setLoading(true)
    loader()
      .then((value) => {
        if (ticket !== latest.current) return
        setData(value)
        setError(null)
      })
      .catch((exc: Error) => {
        if (ticket !== latest.current) return
        setError(exc.message)
      })
      .finally(() => {
        if (ticket === latest.current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload: useCallback(() => setNonce((n) => n + 1), []) }
}

/** The server pushes a digest of every file it reads. A change means reload, not a diff to apply. */
export function useDigest(): { digest: string | null; live: boolean } {
  const [digest, setDigest] = useState<string | null>(null)
  const [live, setLive] = useState(false)

  useEffect(() => {
    const source = new EventSource('api/stream')
    source.onopen = () => setLive(true)
    source.onerror = () => setLive(false)
    source.onmessage = (event) => {
      try {
        setDigest((JSON.parse(event.data) as { digest: string }).digest)
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    }
    return () => source.close()
  }, [])

  return { digest, live }
}

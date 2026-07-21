import { useEffect, useState } from 'react'

type Route = { view: 'cluster' | 'contracts' | 'deployments' | 'plans'; id: string | null }
type Theme = 'dark' | 'light'

function currentRoute(): Route {
  const parts = window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'contracts') return { view: 'contracts', id: parts[1] ?? null }
  if (parts[0] === 'deployments') return { view: 'deployments', id: parts[1] ?? null }
  if (parts[0] === 'plans') return { view: 'plans', id: parts[1] ?? null }
  return { view: 'cluster', id: null }
}

export function useRoute(): [Route, (to: string) => void] {
  const [route, setRoute] = useState(currentRoute)

  useEffect(() => {
    const update = () => setRoute(currentRoute())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  return [route, (to) => (window.location.hash = to)]
}

function initialTheme(): Theme {
  const saved = window.localStorage.getItem('harness-theme')
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('harness-theme', theme)
  }, [theme])

  return [theme, () => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))]
}

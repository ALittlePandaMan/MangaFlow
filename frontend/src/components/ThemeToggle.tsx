import {Moon, Sun} from 'lucide-react'
import {useEffect, useState} from 'react'

import {applyTheme, nextThemeMode, readStoredThemeMode, resolveTheme, SYSTEM_DARK_QUERY} from '../theme'

function readSystemDark(): boolean {
  return window.matchMedia?.(SYSTEM_DARK_QUERY).matches ?? true
}

export function ThemeToggle() {
  const [mode, setMode] = useState(readStoredThemeMode)
  const [systemDark, setSystemDark] = useState(readSystemDark)
  const resolved = resolveTheme(mode, systemDark)
  const nextMode = nextThemeMode(mode, systemDark)
  const modeLabel = mode === 'system' ? `跟随系统（当前${resolved === 'dark' ? '深色' : '浅色'}）` : mode === 'dark' ? '深色主题' : '浅色主题'
  const nextLabel = nextMode === 'system' ? '跟随系统' : nextMode === 'dark' ? '深色主题' : '浅色主题'
  const Icon = resolved === 'dark' ? Moon : Sun

  useEffect(() => {
    const media = window.matchMedia?.(SYSTEM_DARK_QUERY)
    if (!media) return
    const update = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => applyTheme(mode, systemDark), [mode, systemDark])

  return <button
    type="button"
    className="ml-2 grid size-10 shrink-0 cursor-pointer place-items-center rounded-[10px] border border-line bg-raised text-secondary outline-none transition-colors hover:border-line-strong hover:bg-hover hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/25"
    title={`${modeLabel}，点击切换为${nextLabel}`}
    aria-label={`主题：${modeLabel}，点击切换为${nextLabel}`}
    onClick={() => setMode(nextMode)}
  ><Icon aria-hidden="true" size={18}/></button>
}

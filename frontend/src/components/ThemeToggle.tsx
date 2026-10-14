import {Moon, Sun} from 'lucide-react'
import {useCallback, useEffect, useRef, useState} from 'react'
import {flushSync} from 'react-dom'

import {applyTheme, cancelThemeTransition, nextThemeMode, readStoredThemeMode, resolveTheme, SYSTEM_DARK_QUERY, transitionTheme, type ThemeMode} from '../theme'

function readSystemDark(): boolean {
  return window.matchMedia?.(SYSTEM_DARK_QUERY).matches ?? true
}

export function ThemeToggle() {
  const [mode, setMode] = useState(readStoredThemeMode)
  const [systemDark, setSystemDark] = useState(readSystemDark)
  const requestedMode = useRef(mode)
  const requestedSystemDark = useRef(systemDark)
  const resolved = resolveTheme(mode, systemDark)
  const nextMode = nextThemeMode(mode, systemDark)
  const modeLabel = mode === 'system' ? `跟随系统（当前${resolved === 'dark' ? '深色' : '浅色'}）` : mode === 'dark' ? '深色主题' : '浅色主题'
  const nextLabel = nextMode === 'system' ? '跟随系统' : nextMode === 'dark' ? '深色主题' : '浅色主题'
  const Icon = resolved === 'dark' ? Moon : Sun

  const switchTheme = useCallback((next: ThemeMode, dark: boolean) => {
    requestedMode.current = next
    requestedSystemDark.current = dark
    void transitionTheme(next, dark, () => {
      // Update the icon and root palette in the SAME incoming snapshot.
      flushSync(() => {setMode(next); setSystemDark(dark)})
    })
  }, [])

  useEffect(() => {
    // index.html already restores the palette before the first paint.
    applyTheme(requestedMode.current, requestedSystemDark.current)
    return cancelThemeTransition
  }, [])

  useEffect(() => {
    const media = window.matchMedia?.(SYSTEM_DARK_QUERY)
    if (!media) return
    const update = (event: MediaQueryListEvent) => switchTheme(requestedMode.current, event.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [switchTheme])

  return <button
    type="button"
    className="app-header-icon grid size-10 shrink-0 cursor-pointer place-items-center rounded-[10px] border border-line bg-panel text-secondary outline-none transition-colors hover:border-line-strong hover:bg-hover hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/25"
    title={`${modeLabel}，点击切换为${nextLabel}`}
    aria-label={`主题：${modeLabel}，点击切换为${nextLabel}`}
    onClick={() => switchTheme(nextThemeMode(requestedMode.current, requestedSystemDark.current), requestedSystemDark.current)}
  ><Icon aria-hidden="true" size={18}/></button>
}

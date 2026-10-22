import {t, useLocale} from '../i18n'
import {Moon, Sun} from 'lucide-react'
import {useCallback, useEffect, useRef, useState} from 'react'
import {flushSync} from 'react-dom'
import {cn, iconButtonClass} from '../ui'

import {applyTheme, cancelThemeTransition, nextThemeMode, readStoredThemeMode, resolveTheme, SYSTEM_DARK_QUERY, transitionTheme, type ThemeMode} from '../theme'

function readSystemDark(): boolean {
  return window.matchMedia?.(SYSTEM_DARK_QUERY).matches ?? true
}

export function ThemeToggle() {
  useLocale()
  const [mode, setMode] = useState(readStoredThemeMode)
  const [systemDark, setSystemDark] = useState(readSystemDark)
  const requestedMode = useRef(mode)
  const requestedSystemDark = useRef(systemDark)
  const resolved = resolveTheme(mode, systemDark)
  const nextMode = nextThemeMode(mode, systemDark)
  const modeLabel = mode === 'system' ? t("跟随系统（当前{{v0}}）", {v0: resolved === 'dark' ? t("深色") : t("浅色")}) : mode === 'dark' ? t("深色主题") : t("浅色主题")
  const nextLabel = nextMode === 'system' ? t("跟随系统") : nextMode === 'dark' ? t("深色主题") : t("浅色主题")
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
    className={cn(iconButtonClass,'app-header-icon')}
    title={t("{{v0}}，点击切换为{{v1}}", {v0: modeLabel, v1: nextLabel})}
    aria-label={t("主题：{{v0}}，点击切换为{{v1}}", {v0: modeLabel, v1: nextLabel})}
    onClick={() => switchTheme(nextThemeMode(requestedMode.current, requestedSystemDark.current), requestedSystemDark.current)}
  ><Icon aria-hidden="true" size={18}/></button>
}

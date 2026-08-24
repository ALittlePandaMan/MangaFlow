export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemeMode, 'system'>

export const THEME_STORAGE_KEY = 'mangaflow.theme'
export const SYSTEM_DARK_QUERY = '(prefers-color-scheme: dark)'

export function readStoredThemeMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function resolveTheme(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  return mode === 'system' ? (systemDark ? 'dark' : 'light') : mode
}

export function nextThemeMode(mode: ThemeMode, systemDark: boolean): ThemeMode {
  if (mode === 'system') return systemDark ? 'light' : 'dark'
  if (mode === 'light') return 'dark'
  return 'system'
}

export function applyTheme(mode: ThemeMode, systemDark: boolean): void {
  const resolved = resolveTheme(mode, systemDark)
  document.documentElement.dataset.theme = resolved
  document.documentElement.dataset.themeMode = mode
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#111310' : '#ecefe9')
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, mode)
  } catch {
    // Theme still works for this session when storage is unavailable.
  }
}

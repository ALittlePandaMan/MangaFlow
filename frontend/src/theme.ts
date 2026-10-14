export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemeMode, 'system'>

export const THEME_STORAGE_KEY = 'mangaflow.theme'
export const SYSTEM_DARK_QUERY = '(prefers-color-scheme: dark)'
export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

type ThemeViewTransition = {
  ready: Promise<void>
  updateCallbackDone: Promise<void>
  finished: Promise<void>
  skipTransition: () => void
}
type ThemeTransitionJob = {transition?: ThemeViewTransition}
let activeTransition: ThemeTransitionJob | null = null

const afterThemePaint = () => new Promise<void>(resolve => {
  // Keep transition:none through one actual style/render update on browsers
  // without snapshots, then restore the normal hover/control transitions.
  let firstFrame = 0, secondFrame = 0
  const finish = () => {
    window.clearTimeout(timer)
    window.cancelAnimationFrame(firstFrame)
    window.cancelAnimationFrame(secondFrame)
    resolve()
  }
  // RAF can be suspended in a background tab; do not strand the switch guard.
  const timer = window.setTimeout(finish, 120)
  firstFrame = window.requestAnimationFrame(() => {secondFrame = window.requestAnimationFrame(finish)})
})

export function cancelThemeTransition(): void {
  const previous = activeTransition
  activeTransition = null
  previous?.transition?.skipTransition()
  delete document.documentElement.dataset.themeSwitching
}

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
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#212121' : '#ffffff')
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, mode)
  } catch {
    // Theme still works for this session when storage is unavailable.
  }
}

/** Theme-only motion: one snapshot fade, never a route or mask-mode animation. */
export function transitionTheme(mode: ThemeMode, systemDark: boolean, commit: () => void = () => {}): Promise<void> {
  cancelThemeTransition()
  const root = document.documentElement
  const current = root.dataset.theme
  const resolved = resolveTheme(mode, systemDark)
  // Initial paint and light -> system-light do not need a visual transition.
  if (!current || current === resolved) {
    applyTheme(mode, systemDark)
    commit()
    return Promise.resolve()
  }

  const job: ThemeTransitionJob = {}
  activeTransition = job
  root.dataset.themeSwitching = 'true'
  let applied = false
  const update = () => {
    // Skipping a transition does not cancel its asynchronous update callback.
    // A rapid second click must win even if the first callback arrives later.
    if (activeTransition !== job || applied) return
    applied = true
    applyTheme(mode, systemDark)
    commit()
  }
  const finish = () => {
    if (activeTransition !== job) return
    update()
    activeTransition = null
    delete root.dataset.themeSwitching
  }
  const instant = () => {
    // Flush transition:none before changing colors. Unsupported browsers and
    // reduced-motion users get an atomic switch, not staggered local fades.
    void window.getComputedStyle(root).transitionProperty
    update()
    return afterThemePaint().then(finish)
  }

  const start = (document as Document & {
    startViewTransition?: (callback: () => Promise<void>) => ThemeViewTransition
  }).startViewTransition
  if (!start || window.matchMedia?.(REDUCED_MOTION_QUERY).matches || document.visibilityState === 'hidden') return instant()
  try {
    const transition = start.call(document, async () => {
      update()
      // Rendering/RAF is suspended while the native update callback runs.
      // Yield to MutationObservers, whose themed canvases repaint immediately.
      await Promise.resolve()
    })
    job.transition = transition
    // A hidden document / skipped capture can reject ready independently of
    // finished. Theme selection must still work without unhandled rejections.
    void transition.ready.catch(() => undefined)
    void transition.updateCallbackDone.catch(() => undefined)
    return transition.finished.then(finish, finish)
  } catch {
    return instant()
  }
}

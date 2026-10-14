import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {applyTheme, cancelThemeTransition, REDUCED_MOTION_QUERY, THEME_STORAGE_KEY, transitionTheme} from './theme'

function deferred() {
  let resolve!: () => void, reject!: (reason:Error) => void
  const promise = new Promise<void>((yes,no) => {resolve=yes; reject=no})
  return {promise,resolve,reject}
}

function nativeTransition() {
  const finished = deferred(), ready = deferred(), done = deferred()
  let update!: () => Promise<void>
  const transition = {finished:finished.promise,ready:ready.promise,updateCallbackDone:done.promise,skipTransition:vi.fn()}
  const start = vi.fn((callback:() => Promise<void>) => {update=callback; return transition})
  Object.defineProperty(document,'startViewTransition',{configurable:true,writable:true,value:start})
  return {start,transition,finished,ready,done,update:() => update()}
}

describe('one theme transition timeline', () => {
  beforeEach(() => {
    cancelThemeTransition()
    applyTheme('light',false)
    vi.stubGlobal('matchMedia',vi.fn(() => ({matches:false})))
    Object.defineProperty(document,'startViewTransition',{configurable:true,writable:true,value:undefined})
  })
  afterEach(() => {cancelThemeTransition(); Reflect.deleteProperty(document,'startViewTransition'); vi.unstubAllGlobals(); vi.restoreAllMocks()})

  it('does not animate initial paint or a mode change with the same resolved palette', async () => {
    const native = nativeTransition(), commit = vi.fn()
    delete document.documentElement.dataset.theme
    await transitionTheme('light',false,commit)
    await transitionTheme('system',false,commit)
    expect(native.start).not.toHaveBeenCalled()
    expect(commit).toHaveBeenCalledTimes(2)
    expect(document.documentElement).toHaveAttribute('data-theme-mode','system')
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })

  it('switches root and React content together inside the incoming snapshot and restores hover afterwards', async () => {
    const native = nativeTransition(), commit = vi.fn()
    const completion = transitionTheme('dark',false,commit)
    expect(document.documentElement).toHaveAttribute('data-theme','light')
    expect(document.documentElement).toHaveAttribute('data-theme-switching','true')
    expect(commit).not.toHaveBeenCalled()
    await native.update()
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(commit).toHaveBeenCalledOnce()
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    native.ready.resolve(); native.done.resolve(); native.finished.resolve()
    await completion
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })

  it('keeps the latest request when skipped update callbacks and completions arrive out of order', async () => {
    const older = nativeTransition(), firstCommit = vi.fn()
    const first = transitionTheme('dark',false,firstCommit)
    const newer = nativeTransition(), lastCommit = vi.fn()
    const second = transitionTheme('system',true,lastCommit)
    expect(older.transition.skipTransition).toHaveBeenCalledOnce()
    await older.update()
    expect(document.documentElement).toHaveAttribute('data-theme','light')
    expect(firstCommit).not.toHaveBeenCalled()
    older.finished.resolve(); await first
    expect(document.documentElement).toHaveAttribute('data-theme-switching','true')
    await newer.update(); newer.finished.resolve(); await second
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(document.documentElement).toHaveAttribute('data-theme-mode','system')
    expect(lastCommit).toHaveBeenCalledOnce()
  })

  it('atomically applies the theme without staggered control fades on unsupported browsers', async () => {
    const commit = vi.fn()
    const completion = transitionTheme('dark',false,commit)
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(document.documentElement).toHaveAttribute('data-theme-switching','true')
    expect(commit).toHaveBeenCalledOnce()
    await completion
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })

  it('respects reduced motion even on a browser with native transitions', async () => {
    const native = nativeTransition()
    vi.stubGlobal('matchMedia',vi.fn(query => ({matches:query===REDUCED_MOTION_QUERY})))
    await transitionTheme('dark',false)
    expect(native.start).not.toHaveBeenCalled()
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })

  it('cleans up rejected snapshot promises without losing the selected theme', async () => {
    const native = nativeTransition(), commit = vi.fn()
    const completion = transitionTheme('dark',false,commit)
    native.ready.reject(new Error('capture skipped')); native.done.reject(new Error('capture skipped'))
    native.finished.reject(new Error('capture skipped'))
    await completion
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
    expect(commit).toHaveBeenCalledOnce()
  })

  it('falls back if the browser cannot start the capture and also cleans up when RAF is suspended', async () => {
    const native = nativeTransition()
    native.start.mockImplementation(() => {throw new Error('capture unavailable')})
    vi.spyOn(window,'requestAnimationFrame').mockReturnValue(1)
    await transitionTheme('dark',false)
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })
})

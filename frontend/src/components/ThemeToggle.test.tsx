import {act, render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'

import {cancelThemeTransition, THEME_STORAGE_KEY} from '../theme'
import {ThemeToggle} from './ThemeToggle'

type ThemeListener = (event: MediaQueryListEvent) => void

let systemDark = true
let listeners: Set<ThemeListener>

function installMatchMedia() {
  listeners = new Set()
  const media = {
    get matches() { return systemDark },
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: (_type: string, listener: ThemeListener) => listeners.add(listener),
    removeEventListener: (_type: string, listener: ThemeListener) => listeners.delete(listener),
    addListener: (listener: ThemeListener) => listeners.add(listener),
    removeListener: (listener: ThemeListener) => listeners.delete(listener),
    dispatchEvent: () => true,
  } as unknown as MediaQueryList
  vi.stubGlobal('matchMedia', vi.fn(() => media))
}

function changeSystemTheme(dark: boolean) {
  systemDark = dark
  act(() => listeners.forEach(listener => listener({matches:dark} as MediaQueryListEvent)))
}

describe('ThemeToggle', () => {
  beforeEach(() => {
    systemDark = true
    window.localStorage.clear()
    delete document.documentElement.dataset.theme
    delete document.documentElement.dataset.themeMode
    installMatchMedia()
  })

  afterEach(() => {cancelThemeTransition(); Reflect.deleteProperty(document,'startViewTransition'); vi.unstubAllGlobals()})

  it('follows system theme changes until the user chooses an explicit theme', async () => {
    const {container} = render(<ThemeToggle/>)

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'dark'))
    expect(screen.getByRole('button', {name:/跟随系统（当前深色）/})).toBeInTheDocument()
    expect(container.querySelector('.lucide-moon')).toBeInTheDocument()

    changeSystemTheme(false)
    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'light'))
    expect(screen.getByRole('button', {name:/跟随系统（当前浅色）/})).toBeInTheDocument()
    expect(container.querySelector('.lucide-sun')).toBeInTheDocument()
  })

  it('cycles through explicit and system modes and persists the choice', async () => {
    const user = userEvent.setup()
    render(<ThemeToggle/>)

    await user.click(screen.getByRole('button', {name:/点击切换为浅色主题/}))
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')

    await user.click(screen.getByRole('button', {name:/点击切换为深色主题/}))
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    await user.click(screen.getByRole('button', {name:/点击切换为跟随系统/}))
    expect(document.documentElement).toHaveAttribute('data-theme-mode', 'system')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('system')
  })

  it('commits the icon and palette inside the same native snapshot, not before it', async () => {
    systemDark = false
    const callbacks: Array<() => Promise<void>> = []
    let finish!: () => void
    const finished = new Promise<void>(resolve => {finish=resolve})
    const start = vi.fn((update:() => Promise<void>) => {callbacks.push(update); return {ready:Promise.resolve(),updateCallbackDone:Promise.resolve(),finished,skipTransition:vi.fn()}})
    Object.defineProperty(document,'startViewTransition',{configurable:true,value:start})
    const {container} = render(<ThemeToggle/>)
    expect(start).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button',{name:/点击切换为深色主题/}))
    expect(document.documentElement).toHaveAttribute('data-theme','light')
    expect(container.querySelector('.lucide-sun')).toBeInTheDocument()
    await act(async () => callbacks[0]())
    expect(document.documentElement).toHaveAttribute('data-theme','dark')
    expect(container.querySelector('.lucide-moon')).toBeInTheDocument()
    await act(async () => finish())
    expect(document.documentElement).not.toHaveAttribute('data-theme-switching')
  })

  it('keeps the latest mode when clicked again before an older snapshot callback starts', async () => {
    systemDark = false
    const callbacks: Array<() => Promise<void>> = []
    const skip = vi.fn()
    const start = vi.fn((update:() => Promise<void>) => {callbacks.push(update); return {ready:Promise.resolve(),updateCallbackDone:Promise.resolve(),finished:new Promise<void>(() => {}),skipTransition:skip}})
    Object.defineProperty(document,'startViewTransition',{configurable:true,value:start})
    const {container}=render(<ThemeToggle/>)
    const button=screen.getByRole('button',{name:/主题：/})
    await userEvent.click(button)
    await userEvent.click(button)
    expect(skip).toHaveBeenCalledOnce()
    await act(async () => callbacks[0]())
    expect(document.documentElement).toHaveAttribute('data-theme-mode','system')
    expect(document.documentElement).toHaveAttribute('data-theme','light')
    expect(container.querySelector('.lucide-sun')).toBeInTheDocument()
  })
})

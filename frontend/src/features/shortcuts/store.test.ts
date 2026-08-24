import {describe, expect, it} from 'vitest'
import {DEFAULT_SHORTCUTS, formatShortcut, matchesShortcut, shortcutConflicts, shortcutForEvent, shortcutFromEvent} from './store'

const keyboardEvent = (code: string, options: KeyboardEventInit = {}) => new KeyboardEvent('keydown', {code, ...options})

describe('shortcut bindings', () => {
  it('captures modifiers in a stable order', () => {
    expect(shortcutFromEvent(keyboardEvent('KeyK', {ctrlKey:true, shiftKey:true}))).toBe('Mod+Shift+KeyK')
    expect(formatShortcut('Mod+Shift+KeyK')).toBe('Ctrl + Shift + K')
  })

  it('matches ctrl and meta as the same command modifier', () => {
    expect(matchesShortcut(keyboardEvent('KeyZ', {ctrlKey:true}), 'Mod+KeyZ')).toBe(true)
    expect(matchesShortcut(keyboardEvent('KeyZ', {metaKey:true}), 'Mod+KeyZ')).toBe(true)
    expect(matchesShortcut(keyboardEvent('KeyZ'), 'Mod+KeyZ')).toBe(false)
  })

  it('keeps shift as a fast nudge variant', () => {
    const event = keyboardEvent('ArrowLeft', {shiftKey:true})
    expect(matchesShortcut(event, 'ArrowLeft', true)).toBe(true)
    expect(shortcutForEvent(event, DEFAULT_SHORTCUTS)).toBe('region.nudgeLeft')
  })

  it('keeps number-pad aliases for views and zoom controls', () => {
    expect(shortcutForEvent(keyboardEvent('Numpad2'), DEFAULT_SHORTCUTS)).toBe('view.clean')
    expect(shortcutForEvent(keyboardEvent('NumpadAdd'), DEFAULT_SHORTCUTS)).toBe('zoom.in')
    expect(shortcutForEvent(keyboardEvent('Equal', {shiftKey:true}), DEFAULT_SHORTCUTS)).toBe('zoom.in')
  })

  it('reports duplicate configured shortcuts', () => {
    const shortcuts = {...DEFAULT_SHORTCUTS, 'tool.select':'KeyR'}
    expect(shortcutConflicts(shortcuts).get('KeyR')).toEqual(['tool.select', 'tool.rectangle'])
  })

  it('reports bindings shadowed by the fast nudge shift variant', () => {
    const shortcuts = {...DEFAULT_SHORTCUTS, 'region.cancelSelection':'Shift+ArrowLeft'}
    expect(shortcutConflicts(shortcuts).get('Shift+ArrowLeft')).toEqual(['region.cancelSelection', 'region.nudgeLeft'])
  })
})

import {describe, expect, it} from 'vitest'
import {DEFAULT_SHORTCUTS, formatShortcut, matchesShortcut, shortcutConflicts, shortcutForEvent, shortcutFromEvent, SHORTCUT_DEFINITIONS, useShortcutStore} from './store'

const keyboardEvent = (code: string, options: KeyboardEventInit = {}) => new KeyboardEvent('keydown', {code, ...options})

describe('shortcut bindings', () => {
  it('captures modifiers in a stable order', () => {
    expect(shortcutFromEvent(keyboardEvent('KeyK', {ctrlKey:true, shiftKey:true}))).toBe('Mod+Shift+KeyK')
    expect(formatShortcut('Mod+Shift+KeyK')).toBe('Ctrl + Shift + K')
  })

  it('uses A/D for image navigation without shadowing select-all or modified keys', () => {
    expect(shortcutForEvent(keyboardEvent('KeyA'), DEFAULT_SHORTCUTS)).toBe('page.previous')
    expect(shortcutForEvent(keyboardEvent('KeyD'), DEFAULT_SHORTCUTS)).toBe('page.next')
    expect(shortcutForEvent(keyboardEvent('KeyA', {ctrlKey:true}), DEFAULT_SHORTCUTS)).toBe('edit.selectAll')
    expect(shortcutForEvent(keyboardEvent('KeyD', {ctrlKey:true}), DEFAULT_SHORTCUTS)).toBeNull()
    expect(shortcutConflicts(DEFAULT_SHORTCUTS).size).toBe(0)
    expect(shortcutForEvent(keyboardEvent('PageDown'), {...DEFAULT_SHORTCUTS, 'page.next':'PageDown'})).toBe('page.next')
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

  it('removes standalone re-layout commands from definitions, settings and keyboard matching', () => {
    expect(SHORTCUT_DEFINITIONS.some(item => item.label.includes('重新排版'))).toBe(false)
    expect(DEFAULT_SHORTCUTS).not.toHaveProperty('region.render')
    expect(DEFAULT_SHORTCUTS).not.toHaveProperty('page.workflowRender')
    expect(shortcutForEvent(keyboardEvent('Digit4',{altKey:true}),DEFAULT_SHORTCUTS)).toBeNull()
    expect(shortcutForEvent(keyboardEvent('Digit4',{ctrlKey:true,shiftKey:true}),DEFAULT_SHORTCUTS)).toBeNull()
    expect(shortcutForEvent(keyboardEvent('Digit4'),DEFAULT_SHORTCUTS)).toBe('view.comparison')
  })

  it('discards removed commands in persisted settings without losing custom active shortcuts', () => {
    const legacy={...DEFAULT_SHORTCUTS,'region.render':'KeyR','page.workflowRender':'KeyD','page.next':'PageDown'}
    const restored=useShortcutStore.persist.getOptions().merge!({shortcuts:legacy},useShortcutStore.getState())
    expect(restored.shortcuts).not.toHaveProperty('region.render')
    expect(restored.shortcuts).not.toHaveProperty('page.workflowRender')
    expect(restored.shortcuts['page.next']).toBe('PageDown')
    expect(shortcutConflicts(restored.shortcuts).size).toBe(0)
  })
})

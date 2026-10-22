import {fireEvent, render, screen} from '@testing-library/react'
import {StrictMode} from 'react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {useContentReveal} from './useContentReveal'

function View({tab, revealOnMount = false}: {tab: string, revealOnMount?: boolean}) {
  const ref = useContentReveal<HTMLDivElement>(tab, revealOnMount ? {animateOnMount:true, duration:220, fromOpacity:0} : undefined)
  return <div ref={ref}><input aria-label="draft" defaultValue="saved"/></div>
}

describe('useContentReveal', () => {
  const cancel = vi.fn()
  const animate = vi.fn(() => ({cancel}))
  const media = {matches:false, addEventListener:vi.fn(), removeEventListener:vi.fn()}
  beforeEach(() => {
    vi.clearAllMocks()
    media.matches = false
    vi.stubGlobal('matchMedia', vi.fn(() => media))
    Object.defineProperty(HTMLElement.prototype, 'animate', {configurable:true, value:animate})
  })
  afterEach(() => {
    Reflect.deleteProperty(HTMLElement.prototype, 'animate')
    vi.unstubAllGlobals()
  })

  it('reveals a changed view without remounting its draft or losing focus', () => {
    const {rerender} = render(<View tab="first"/>)
    const input = screen.getByRole('textbox')
    input.focus()
    fireEvent.change(input, {target:{value:'unsaved'}})
    expect(animate).not.toHaveBeenCalled()
    rerender(<View tab="second"/>)
    expect(animate).toHaveBeenCalledOnce()
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveValue('unsaved')
    expect(input).toHaveFocus()
    rerender(<View tab="second"/>)
    expect(animate).toHaveBeenCalledOnce()
  })

  it('cancels interrupted motion and cleans up when unmounted', () => {
    const {rerender, unmount} = render(<View tab="first"/>)
    rerender(<View tab="second"/>)
    rerender(<View tab="third"/>)
    expect(cancel).toHaveBeenCalledOnce()
    expect(animate).toHaveBeenCalledTimes(2)
    unmount()
    expect(cancel).toHaveBeenCalledTimes(2)
    expect(media.removeEventListener).toHaveBeenCalledTimes(2)
  })

  it('can reveal initial content and revisited sections without replaying on unrelated renders', () => {
    const {rerender} = render(<View tab="first" revealOnMount/>)
    expect(animate).toHaveBeenLastCalledWith([{opacity:0}, {opacity:1}], expect.objectContaining({duration:220}))
    rerender(<View tab="second" revealOnMount/>)
    rerender(<View tab="first" revealOnMount/>)
    expect(animate).toHaveBeenCalledTimes(3)
    rerender(<View tab="first" revealOnMount/>)
    expect(animate).toHaveBeenCalledTimes(3)
    expect(cancel).toHaveBeenCalledTimes(2)
  })

  it('skips motion when reduced motion is requested', () => {
    media.matches = true
    const {rerender} = render(<View tab="first"/>)
    rerender(<View tab="second"/>)
    expect(animate).not.toHaveBeenCalled()
  })

  it('restarts entry after StrictMode cleans up the first effect', () => {
    const {unmount} = render(<StrictMode><View tab="first" revealOnMount/></StrictMode>)
    expect(animate).toHaveBeenCalledTimes(2)
    expect(cancel).toHaveBeenCalledOnce()
    unmount()
    expect(cancel).toHaveBeenCalledTimes(2)
  })

  it('cancels immediately when the motion preference changes', () => {
    const {rerender} = render(<View tab="first"/>)
    rerender(<View tab="second"/>)
    const onChange = media.addEventListener.mock.calls[0][1] as () => void
    onChange()
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('works without Web Animations support', () => {
    Reflect.deleteProperty(HTMLElement.prototype, 'animate')
    const {rerender} = render(<View tab="first"/>)
    expect(() => rerender(<View tab="second"/>)).not.toThrow()
    expect(screen.getByRole('textbox')).toHaveValue('saved')
  })
})

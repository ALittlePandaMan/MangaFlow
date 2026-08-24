import {act, renderHook, waitFor} from '@testing-library/react'
import {afterAll, beforeAll, describe, expect, it, vi} from 'vitest'

import {preloadImage, useImage} from './useImage'

class FakeImage {
  static instances: FakeImage[] = []

  crossOrigin = ''
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  decode = vi.fn(async () => undefined)
  private value = ''

  constructor() {
    FakeImage.instances.push(this)
  }

  get src() { return this.value }
  set src(value: string) { this.value = value }
}

describe('useImage cache', () => {
  beforeAll(() => vi.stubGlobal('Image', FakeImage))
  afterAll(() => vi.unstubAllGlobals())

  it('deduplicates concurrent loads and decodes once', async () => {
    const before = FakeImage.instances.length
    const first = preloadImage('/media/concurrent-a.png')
    const second = preloadImage('/media/concurrent-a.png')
    expect(FakeImage.instances).toHaveLength(before + 1)

    const image = FakeImage.instances.at(-1)!
    image.onload?.()
    await Promise.all([first, second])

    expect(image.decode).toHaveBeenCalledTimes(1)
  })

  it('does not show the previous image while a new source is pending', async () => {
    const firstLoad = preloadImage('/media/source-a.png')
    FakeImage.instances.at(-1)!.onload?.()
    await firstLoad

    const {result, rerender} = renderHook(({source}) => useImage(source), {
      initialProps: {source: '/media/source-a.png'},
    })
    expect(result.current.image?.src).toBe('/media/source-a.png')

    rerender({source: '/media/source-b.png'})
    expect(result.current.image).toBeNull()

    await act(async () => { FakeImage.instances.at(-1)!.onload?.() })
    await waitFor(() => expect(result.current.image?.src).toBe('/media/source-b.png'))
  })

  it('keeps recently reused pages in the LRU window', async () => {
    const load = async (source: string) => {
      const pending = preloadImage(source)
      FakeImage.instances.at(-1)!.onload?.()
      await pending
    }
    for (let index = 0; index < 6; index += 1) await load(`/media/lru-${index}.png`)

    const beforeTouch = FakeImage.instances.length
    await preloadImage('/media/lru-0.png')
    expect(FakeImage.instances).toHaveLength(beforeTouch)
    await load('/media/lru-6.png')

    const beforeReload = FakeImage.instances.length
    const evicted = preloadImage('/media/lru-1.png')
    expect(FakeImage.instances).toHaveLength(beforeReload + 1)
    FakeImage.instances.at(-1)!.onload?.()
    await evicted
    expect(FakeImage.instances.some(image => image.src === '/media/lru-0.png')).toBe(true)
  })
})

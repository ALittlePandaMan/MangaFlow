import {describe, expect, it} from 'vitest'
import {isPerspectiveQuad, orderPerspectiveQuad, perspectiveQuadSize, perspectiveRasterRatio} from './perspectiveText'

// Same three angled boxes as the reported page, in geometric TL/TR/BR/BL order.
const quads = [
  [[309,99], [331,92], [346,137], [326,144]],
  [[272,107], [302,93], [352,200], [322,214]],
  [[249,130], [277,117], [316,197], [290,210]],
]

describe('perspective reading direction', () => {
  it.each(quads.map((quad, index) => ({quad, index})))('keeps box $index top-to-bottom regardless of its starting corner or winding', ({quad}) => {
    const size = perspectiveQuadSize(quad)!
    expect(size.height).toBeGreaterThan(size.width)
    for (let start = 0; start < 4; start++) {
      const cycled = [...quad.slice(start), ...quad.slice(0, start)]
      for (const points of [cycled, [...cycled].reverse()]) {
        const original = structuredClone(points)
        expect(orderPerspectiveQuad(points)).toEqual(quad)
        expect(perspectiveQuadSize(points)).toEqual(size)
        expect(points).toEqual(original)
      }
    }
    expect(orderPerspectiveQuad([quad[2], quad[0], quad[3], quad[1]])).toEqual(quad)
  })
  it('continues to reject invalid geometry', () => {
    expect(isPerspectiveQuad([[0,0], [100,0], [30,20], [0,100]])).toBe(false)
    expect(isPerspectiveQuad([[0,0], [100,0], [100,100], [0,0]])).toBe(false)
    expect(isPerspectiveQuad([[0,0], [100,0], [100,Number.NaN], [0,100]])).toBe(false)
  })
  it.each([[100_000,100_000],[100,1_000_000]])('bounds overflow raster allocation for a %s x %s tile', (width,height) => {
    const ratio=perspectiveRasterRatio(width,height,6)
    expect(ratio).toBeGreaterThan(0)
    expect(width*height*ratio*ratio).toBeLessThanOrEqual(8_000_000.001)
    expect(Math.max(width,height)*ratio).toBeLessThanOrEqual(16_384)
  })
  it('keeps high-resolution small text unchanged', () => {
    expect(perspectiveRasterRatio(100,50,6)).toBe(6)
  })
})

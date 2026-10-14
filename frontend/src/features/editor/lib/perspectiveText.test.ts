import {describe, expect, it} from 'vitest'
import {isPerspectiveQuad, orderPerspectiveQuad, perspectiveQuadSize} from './perspectiveText'

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
})

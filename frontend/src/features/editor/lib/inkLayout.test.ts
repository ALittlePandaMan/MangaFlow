import {describe, expect, it} from 'vitest'
import {fitInk, type GlyphMetrics} from './inkLayout'

const measure = (character: string): GlyphMetrics => ({
  advance:20, left:0, right:/[甲乙]/u.test(character) ? 18 : 8, top:-18, bottom:0,
})

describe('upright vertical Latin layout', () => {
  it('stacks mixed dialogue upright and wraps into columns from right to left', () => {
    const layout = fitInk('甲play12乙',100,78,20,true,1.15,0,'center',measure)
    expect(layout.lines).toEqual(['甲pla','y12乙'])
    expect(layout.glyphs.every(glyph => !glyph.rotate)).toBe(true)
    const centers = layout.glyphs.map(glyph => glyph.x+(glyph.right-glyph.left)/2)
    expect(new Set(centers.slice(0,4)).size).toBe(1)
    expect(new Set(centers.slice(4)).size).toBe(1)
    expect(centers[0]).toBeGreaterThan(centers[4])
    expect(layout.glyphs.map(glyph => glyph.y)).toEqual([0,20,40,60,0,20,40,60])
    expect(layout.overflow).toBe(false)
  })

  it('measures unrotated ink when deciding whether a narrow column fits', () => {
    const layout = fitInk('I',10,20,20,true,1.15,0,'center',measure)
    expect(layout.width).toBe(8)
    expect(layout.height).toBe(18)
    expect(layout.overflow).toBe(false)
  })

  it.each([false,true])('keeps letters and digits upright without changing punctuation, vertical: %s', vertical => {
    const text = 'AaZz019!?-'
    const layout = fitInk(text,400,400,20,vertical,1.15,0,'center',measure)
    expect(layout.glyphs.map(glyph => glyph.text).join('')).toBe(vertical ? 'AaZz019︕︖-' : text)
    expect(layout.glyphs.map(glyph => glyph.rotate)).toEqual([...Array<boolean>(9).fill(false),vertical])
  })
})

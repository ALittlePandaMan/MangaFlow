import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {alignedTextFrame, translationTextFrame} from './textFrame'
import {sizeText} from './fontSizing'

const polygon = (angle: number) => [[-70,-90],[70,-90],[70,90],[-70,90]].map(([x,y]) => {
  const cosine = Math.cos(angle*Math.PI/180), sine = Math.sin(angle*Math.PI/180)
  return [180+x*cosine-y*sine,180+x*sine+y*cosine]
})
const fixture = (angle: number, orientation: TextRegion['orientation']) => {
  const points = polygon(angle), xs = points.map(p => p[0]), ys = points.map(p => p[1])
  return {polygon:points,bbox:[Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)],
    translated_polygon:points,rotation:angle,orientation,translated_text:'MANGA TEXT',font_size:12,auto_font_size:true,
    font_family:'sans-serif',font_weight:400,stroke_width:0,line_spacing:1.15,character_spacing:0,alignment:'center',layout_data:{}} as TextRegion
}

describe('slanted free-text local coordinates', () => {
  it.each([-45,-12,12,45,90,180])('measures the rectangle in text axes at %s degrees', angle => {
    const points = polygon(angle)
    for (const ordered of [points,[...points].reverse(),[...points.slice(2),...points.slice(0,2)]]) {
      expect(alignedTextFrame(ordered,angle)).toEqual([110,90,140,180])
    }
    expect(alignedTextFrame(points,0)).toBeNull()
    expect(alignedTextFrame(points,angle+5)).toBeNull()
  })
  it('leaves authored angles, perspective and irregular polygons on their own fitting path', () => {
    const region = fixture(12,'horizontal')
    expect(translationTextFrame({...region,rotation:0})).toBeNull()
    expect(translationTextFrame({...region,rotation:7})).toBeNull()
    expect(translationTextFrame({...region,perspective_warp:true})).toBeNull()
    region.translated_polygon[2][0] += 10
    expect(translationTextFrame(region)).toBeNull()
    expect(alignedTextFrame([[0,0],[0,0],[0,0],[0,0]],12)).toBeNull()
    expect(alignedTextFrame(polygon(12),NaN)).toBeNull()
  })
  it.each(['horizontal','vertical'] as const)('fits %s text before rotation, preserving size and reading order', orientation => {
    const measure = (_character: string, size: number) => size
    const upright = sizeText(fixture(0,orientation),undefined,measure)
    for (const angle of [-45,-12,12,45]) {
      const region = fixture(angle,orientation)
      const fitted = sizeText(region,undefined,measure)
      expect(fitted).toEqual(upright)
      expect(fitted.overflow).toBe(false)
      const adjusted = sizeText({...region,font_size:120,auto_font_size:false},undefined,measure)
      expect(adjusted.fontSize).toBe(120)
      expect(adjusted.overflow).toBe(true)
    }
  })
})

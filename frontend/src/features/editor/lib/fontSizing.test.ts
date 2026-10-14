import {describe,expect,it} from 'vitest'
import type {TextRegion} from '../../../types'
import {sizeText,usesAutomaticFontSize} from './fontSizing'
import {extendedPerspectiveQuad} from './perspectiveText'
import {shapePreviewRegion} from './shapeText'

const fixture = (patch: Partial<TextRegion> = {}) => ({bbox:[0,0,200,200],polygon:[[0,0],[200,0],[200,200],[0,200]],
  translated_bbox:[0,0,200,200],translated_polygon:[[0,0],[200,0],[200,200],[0,200]],translated_text:'漫画翻译',
  font_size:12,font_family:'sans-serif',font_weight:400,orientation:'vertical',line_spacing:1.15,character_spacing:0,
  stroke_width:0,alignment:'center',layout_data:{},...patch} as TextRegion)
const measure = (_character: string,size: number) => size

describe('initial font sizing and subsequent user adjustments',()=>{
  it.each(['horizontal','vertical'] as const)('fills a %s frame without a preferred-size cap',orientation=>{
    const region=fixture({orientation})
    const sized=sizeText(region,undefined,measure)
    expect(sized.fontSize).toBeGreaterThan(12)
    expect(sized.overflow).toBe(false)
    expect(sizeText({...region,auto_font_size:false,font_size:sized.fontSize+1}, {width:196,height:196},measure).overflow).toBe(true)
    expect(sizeText(region,{width:30,height:40},measure).fontSize).toBeLessThan(sized.fontSize)
  })
  it.each(['horizontal','vertical'] as const)('keeps user-selected %s font size above and below previous limits',orientation=>{
    for (const font_size of [6,120]) {
      const result=sizeText(fixture({orientation,font_size,auto_font_size:false}),{width:20,height:30},measure)
      expect(result.fontSize).toBe(font_size)
    }
  })
  it('tracks overrides without a UI mode and yields to optimistic changes',()=>{
    expect(usesAutomaticFontSize(fixture())).toBe(true)
    expect(usesAutomaticFontSize(fixture({font_size:60,layout_data:{render_input:{font_size:24}}}))).toBe(false)
    expect(usesAutomaticFontSize(fixture({auto_font_size:false,layout_data:{auto_font_size:true}}))).toBe(false)
  })
  it('does not clip a user-adjusted size to a previously generated safe box',()=>{
    const region=fixture({auto_font_size:false,layout_data:{render_skipped:true,shape_layout_applied:true}})
    expect(shapePreviewRegion(region)).toBe(region)
  })
  it('projects overflow with the original frame scale instead of shrinking it',()=>{
    const quad=[[100,100],[140,100],[140,160],[100,160]]
    expect(extendedPerspectiveQuad(quad,{width:40,height:60},{x:-20,y:-30,width:80,height:120})).toEqual([[80,70],[160,70],[160,190],[80,190]])
  })
  it('retains negative-spacing top-to-bottom order and maximum fitting size',()=>{
    const result=sizeText(fixture({character_spacing:-4}),undefined,measure)
    expect(result.cellHeight).toBeGreaterThan(0)
    expect(result.overflow).toBe(false)
  })
})

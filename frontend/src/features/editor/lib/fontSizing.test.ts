import {describe,expect,it} from 'vitest'
import type {TextRegion} from '../../../types'
import {sizeText,usesAutomaticFontSize} from './fontSizing'
import {extendedPerspectiveQuad} from './perspectiveText'
import {fitInk, type GlyphMetrics} from './inkLayout'
import {hasCurrentComputedFont} from './fontSizing'
import {PERSPECTIVE_RENDER_OUTPUT_VERSION} from './perspectiveText'

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
    const region=fixture({auto_font_size:false,font_size:80,
      layout_data:{render_skipped:true,shape_layout_applied:true,effective_text_bbox:[0,0,1,1]}})
    const sized=sizeText(region,{width:20,height:30},measure)
    expect(sized.fontSize).toBe(80)
    expect(sized.overflow).toBe(true)
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
  it.each(['horizontal','vertical'] as const)('uses real %s ink instead of a padded em square', orientation => {
    const ink = (_character: string, size: number): GlyphMetrics => ({advance: size, left: .1*size, right: .9*size, top: -.7*size, bottom: 0})
    const region = fixture({orientation, translated_text:'甲', stroke_width:0})
    const result = sizeText(region, {width:100,height:100}, ink)
    expect(result.fontSize).toBe(122)
    expect(result.width).toBeCloseTo(97.6)
    expect(result.overflow).toBe(false)
    const manual = sizeText({...region,auto_font_size:false,font_size:result.fontSize}, {width:98,height:98}, ink)
    expect(manual.glyphs).toEqual(result.glyphs)
    expect(manual.height).toEqual(result.height)
    expect(sizeText({...region,auto_font_size:false,font_size:200}, {width:100,height:100}, ink).overflow).toBe(true)
  })
  it('uses the full visible frame regardless of the polygon area', () => {
    const region = fixture()
    expect(sizeText({...region,translated_polygon:[[0,0],[200,0],[100,200]]},undefined,measure)).toEqual(sizeText(region,undefined,measure))
  })
  it('preserves paragraphs, vertical punctuation and top-to-bottom right-to-left order', () => {
    const metrics = (_character: string): GlyphMetrics => ({advance:20,left:0,right:20,top:-20,bottom:0})
    const layout = fitInk('甲乙\n\n丙!?A',200,200,20,true,1.15,0,'center',metrics)
    expect(layout.lines).toEqual(['甲乙','','丙!?A'])
    expect(layout.glyphs.map(glyph=>glyph.text)).toEqual(['甲','乙','丙','︕','︖','A'])
    expect(layout.glyphs[0].x).toBeGreaterThan(layout.glyphs[2].x)
    expect(layout.glyphs[0].y).toBeLessThan(layout.glyphs[1].y)
    expect(layout.glyphs.map(glyph=>glyph.rotate)).toEqual([false,false,false,false,false,true])
  })
  it('measures repeated glyphs once per candidate without retaining stale font metrics', () => {
    const calls: string[] = []
    const measured = (character: string,size: number) => {calls.push(`${character}:${size}`);return size}
    sizeText(fixture({translated_text:'甲乙'.repeat(60)}),undefined,measured)
    expect(calls.length).toBe(new Set(calls).size)
    expect(calls.length).toBeLessThanOrEqual(22)
    const first = sizeText(fixture(),undefined,measure)
    const changed = sizeText(fixture(),undefined,(_char,size)=>size*.5)
    expect(changed.fontSize).toBeGreaterThan(first.fontSize)
  })
  it('invalidates the previous sizing algorithm even when saved inputs match', () => {
    const region = fixture()
    region.layout_data = {render_output_version:PERSPECTIVE_RENDER_OUTPUT_VERSION-1,render_input:{...region,auto_font_size:true,custom_font_path:null}}
    expect(hasCurrentComputedFont(region)).toBe(false)
    expect(sizeText(region,undefined,measure).fontSize).toBeGreaterThan(region.font_size)
  })
  it('fits perspective auto text without charging for the detector angle twice', () => {
    const region=fixture({orientation:'horizontal',translated_text:'甲乙',rotation:-28,perspective_warp:true,
      layout_data:{text_rotation:{source:'detector',role:'free_text',angle:-28}}})
    const rect={width:160,height:40}
    expect(sizeText(region,rect,measure)).toEqual(sizeText({...region,rotation:0,layout_data:{}},rect,measure))
    const automatic={...region,layout_data:{...region.layout_data,render_output_version:PERSPECTIVE_RENDER_OUTPUT_VERSION,
      render_input:{...region,auto_font_size:true,custom_font_path:null,text_rotation:region.layout_data.text_rotation}}}
    expect(hasCurrentComputedFont(automatic)).toBe(true)
    expect(hasCurrentComputedFont({...automatic,layout_data:{...automatic.layout_data,
      text_rotation:{source:'user',angle:-28}}})).toBe(false)
  })
})

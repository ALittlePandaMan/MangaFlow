import {describe, expect, it, vi} from 'vitest'
import type {TextRegion} from '../../../types'
import {drawInkText, inkTextBounds} from './drawInkText'
import {fitInk} from './inkLayout'

describe('perspective tile ink bounds', () => {
  it('paints vertical English and digits without rotating the canvas', () => {
    const layout = fitInk('playAZ019',40,300,20,true,1.15,0,'center',() => ({advance:12,left:1,right:11,top:-18,bottom:1}))
    const context = {save:vi.fn(),restore:vi.fn(),translate:vi.fn(),rotate:vi.fn(),fillText:vi.fn()}
    drawInkText(context as unknown as CanvasRenderingContext2D,
      {font_family:'sans-serif',font_weight:400} as TextRegion, layout,
      {textColor:'#000',strokeColor:'#fff',strokeWidth:0},40,300)
    expect(context.rotate).not.toHaveBeenCalled()
    expect(context.fillText.mock.calls.map(call => call[0]).join('')).toBe('playAZ019')
  })

  it.each([false,true])('retains oversized upright Latin glyphs, vertical: %s', vertical => {
    const layout=fitInk('A',30,40,120,vertical,1.15,0,'center',() => ({advance:120,left:-5,right:95,top:-90,bottom:10}))
    const bounds=inkTextBounds(layout,30,40,0,0)
    expect(bounds.x).toBeLessThan(0)
    expect(bounds.y).toBeLessThan(0)
    expect(bounds.width).toBe(104)
    expect(bounds.height).toBe(104)
    expect(layout.glyphs[0].rotate).toBe(false)
  })

  it.each([-35,0,28,90])('contains every drawn corner and stroke after %s degree rotation', rotation => {
    const layout=fitInk('A甲',30,40,120,true,1.15,0,'center',() => ({advance:100,left:-7,right:73,top:-90,bottom:10}))
    const stroke=6, bounds=inkTextBounds(layout,30,40,stroke,rotation)
    const angle=rotation*Math.PI/180, cos=Math.cos(angle), sin=Math.sin(angle)
    for (const glyph of layout.glyphs) {
      const x=(30-layout.coordinateWidth)/2+glyph.x, y=(40-layout.height)/2+glyph.y
      const w=glyph.rotate ? glyph.bottom-glyph.top : glyph.right-glyph.left
      const h=glyph.rotate ? glyph.right-glyph.left : glyph.bottom-glyph.top
      for (const [px,py] of [[x-stroke,y-stroke],[x+w+stroke,y-stroke],[x+w+stroke,y+h+stroke],[x-stroke,y+h+stroke]]) {
        const rx=15+(px-15)*cos-(py-20)*sin, ry=20+(px-15)*sin+(py-20)*cos
        expect(rx).toBeGreaterThanOrEqual(bounds.x)
        expect(rx).toBeLessThanOrEqual(bounds.x+bounds.width)
        expect(ry).toBeGreaterThanOrEqual(bounds.y)
        expect(ry).toBeLessThanOrEqual(bounds.y+bounds.height)
      }
    }
  })
})

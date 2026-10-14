import {describe, expect, it} from 'vitest'
import {MaskBitmap, maskCoverage, maskRgba} from './maskBitmap'

describe('mask bitmap editing', () => {
  it('paints a continuous stroke at image resolution and erases coverage only', () => {
    const mask = new MaskBitmap(80, 60)
    mask.paint({x: 10, y: 20}, {x: 70, y: 20}, 10, 'brush'); mask.commit()
    expect(mask.pixels[20 * 80 + 40]).toBe(255)
    expect(mask.pixels[40 * 80 + 40]).toBe(0)
    const before = mask.pixels.slice()
    mask.paint({x: 40, y: 10}, {x: 40, y: 30}, 6, 'eraser'); mask.commit()
    expect(mask.pixels[20 * 80 + 40]).toBe(0)
    expect(mask.pixels[20 * 80 + 15]).toBe(255)
    mask.undo(); expect(mask.pixels).toEqual(before)
    mask.redo(); expect(mask.pixels[20 * 80 + 40]).toBe(0)
  })
  it('supports dots, bounds clipping, cancellation and clean-state undo', () => {
    const mask = new MaskBitmap(20, 20)
    mask.paint({x: 0, y: 0}, {x: 0, y: 0}, 8, 'brush'); mask.cancel()
    expect(mask.dirty).toBe(false)
    mask.paint({x: 0, y: 0}, {x: 0, y: 0}, 8, 'brush'); mask.commit()
    expect(mask.pixels[0]).toBe(255); expect(mask.pixels[399]).toBe(0)
    expect(mask.dirty).toBe(true)
    mask.undo(); expect(mask.dirty).toBe(false)
    mask.paint({x: 10, y: 10}, {x: 10, y: 10}, 4, 'brush'); mask.commit()
    expect(mask.canRedo).toBe(false)
  })
  it('loads black/white masks and exports coverage as alpha, without overlay opacity', () => {
    const coverage = maskCoverage(new Uint8ClampedArray([0,0,0,255, 255,255,255,255, 128,128,128,255]))
    expect([...coverage]).toEqual([0,255,128])
    expect([...maskRgba(coverage)]).toEqual([255,255,255,0, 255,255,255,255, 255,255,255,128])
    expect([...maskRgba(coverage, [59,130,246])].filter((_, index) => index % 4 === 3)).toEqual([0,255,128])
  })
  it('ignores empty strokes and validates mask dimensions', () => {
    const mask = new MaskBitmap(10, 10)
    mask.paint({x: 4, y: 4}, {x: 6, y: 4}, 4, 'eraser'); mask.commit()
    expect(mask.canUndo).toBe(false)
    expect(() => new MaskBitmap(10,10,new Uint8Array(5))).toThrow()
  })
  it('fills polygon interiors, clips off-page shapes and never wraps into adjacent rows', () => {
    const mask = new MaskBitmap(10, 10)
    mask.fillPolygon([{x:2,y:2},{x:8,y:2},{x:5,y:8}]); mask.commit()
    expect(mask.pixels[3*10+5]).toBe(255)
    expect(mask.pixels[7*10+2]).toBe(0)
    const before = mask.pixels.slice()
    mask.fillPolygon([{x:15,y:1},{x:20,y:1},{x:20,y:8},{x:15,y:8}]); mask.commit()
    expect(mask.pixels).toEqual(before)
    mask.markSaved(); expect(mask.dirty).toBe(false)
    mask.undo(); expect(mask.dirty).toBe(true)
    mask.redo(); expect(mask.dirty).toBe(false)
  })

  it('uploads tight stroke patches and keeps late overlapping saves dirty until the latest patch commits', () => {
    const mask=new MaskBitmap(4000,3000)
    mask.paint({x:2000,y:2700},{x:2020,y:2710},12,'brush');mask.commit()
    const first=mask.patch()
    expect(first.pixels.length).toBeLessThan(1000)
    expect(first.x).toBeGreaterThan(1900)
    mask.paint({x:2008,y:2702},{x:2015,y:2706},8,'eraser');mask.commit()
    const second=mask.patch()
    mask.markPatchSaved(first);expect(mask.dirty).toBe(true)
    mask.markPatchSaved(second);expect(mask.dirty).toBe(false)
    mask.undo();expect(mask.dirty).toBe(true)
    expect(mask.patch().pixels.length).toBe(second.pixels.length)
    mask.redo();expect(mask.dirty).toBe(false)
    mask.paint({x:30,y:30},{x:30,y:30},4,'brush');mask.cancel()
    expect(mask.dirty).toBe(false)
    mask.paint({x:2008,y:2702},{x:2015,y:2706},8,'eraser')
    expect(mask.commit()).toBe(false)
  })

  it('retains many sparse undo/redo steps on large pages instead of only two full-frame copies', () => {
    const mask=new MaskBitmap(4000,3000)
    for (let i=0;i<20;i++) {mask.paint({x:100+i*40,y:2800},{x:100+i*40,y:2800},8,'brush');mask.commit()}
    let count=0
    while(mask.canUndo) {mask.undo();count++}
    expect(count).toBe(20);expect(mask.dirty).toBe(false)
    while(mask.canRedo) mask.redo()
    expect(mask.pixels[2800*4000+100]).toBe(255)
  })
})

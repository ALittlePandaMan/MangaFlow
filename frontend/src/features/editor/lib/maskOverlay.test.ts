import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {buildMaskOverlay} from './maskOverlay'

type Bitmap={width:number;height:number;pixels:Uint8ClampedArray;close:ReturnType<typeof vi.fn>}
describe('mask overlay compositing',() => {
  let buffers:WeakMap<HTMLCanvasElement,Uint8ClampedArray>
  let images:Map<Blob,Bitmap>
  beforeEach(() => {
    buffers=new WeakMap();images=new Map()
    vi.stubGlobal('createImageBitmap',vi.fn(async (blob:Blob) => images.get(blob)!))
    // Tiny pixel-backed 2D contexts exercise actual union/alpha behavior,
    // rather than only asserting that a canvas drawing method was called.
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(function(this:HTMLCanvasElement) {
      const {width,height}=this,pixels=new Uint8ClampedArray(width*height*4)
      buffers.set(this,pixels)
      return {globalCompositeOperation:'source-over',fillStyle:'#000',
        fillRect:() => {for(let i=0;i<pixels.length;i+=4) {pixels[i]=pixels[i+1]=pixels[i+2]=0;pixels[i+3]=255}},
        drawImage(this:CanvasRenderingContext2D,source:Bitmap|HTMLCanvasElement) {
          const input=source instanceof HTMLCanvasElement ? buffers.get(source)! : source.pixels
          for(let i=0;i<pixels.length;i+=4) {
            const alpha=input[i+3]/255
            for(let channel=0;channel<3;channel++) pixels[i+channel]=(this.globalCompositeOperation==='lighten' ? Math.max(pixels[i+channel],input[i+channel]) : input[i+channel])*alpha+pixels[i+channel]*(1-alpha)
          }
        },
        getImageData:() => ({data:pixels.slice(),width,height}),
        putImageData:(image:ImageData) => pixels.set(image.data),
      } as unknown as CanvasRenderingContext2D
    } as unknown as typeof HTMLCanvasElement.prototype.getContext)
  })
  afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})

  const mockMasks=(values:number[][]) => {
    const entries=values.map(pixels => {const blob=new Blob();const bitmap={width:2,height:1,pixels:new Uint8ClampedArray(pixels),close:vi.fn()};images.set(blob,bitmap);return {blob,bitmap}})
    let index=0
    vi.stubGlobal('fetch',vi.fn(async () => {
      if(index) expect(entries[index-1].bitmap.close).toHaveBeenCalledOnce()
      return {ok:true,blob:async () => entries[index++].blob}
    }))
    return entries
  }

  it('turns black into transparency, retains soft coverage and unions overlaps without darkening',async () => {
    const entries=mockMasks([[255,255,255,128,255,255,255,255],[100,100,100,255,0,0,0,255]])
    const result=await buildMaskOverlay({width:2,height:1,sources:['/a','/b']},new AbortController().signal)
    expect([...buffers.get(result)!]).toEqual([57,121,207,128,57,121,207,255])
    entries.forEach(entry => expect(entry.bitmap.close).toHaveBeenCalledOnce())
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('leaves non-mask pixels transparent rather than drawing the OCR rectangle',async () => {
    mockMasks([[0,0,0,255,255,255,255,255]])
    const result=await buildMaskOverlay({width:2,height:1,sources:['/a']},new AbortController().signal)
    expect(buffers.get(result)![3]).toBe(0)
    expect(buffers.get(result)![7]).toBe(255)
  })

  it('rejects a mismatched mask and still releases the decoded bitmap',async () => {
    const [entry]=mockMasks([[0,0,0,255,0,0,0,255]])
    entry.bitmap.width=3
    await expect(buildMaskOverlay({width:2,height:1,sources:['/a']},new AbortController().signal)).rejects.toThrow('尺寸')
    expect(entry.bitmap.close).toHaveBeenCalledOnce()
  })

  it('bounds preview allocation and skips aborted network reads',async () => {
    const output=await buildMaskOverlay({width:8000,height:8000,sources:[]},new AbortController().signal)
    expect(output.width*output.height).toBeLessThanOrEqual(4_000_000)
    const controller=new AbortController();controller.abort()
    vi.stubGlobal('fetch',vi.fn())
    await expect(buildMaskOverlay({width:2,height:1,sources:['/a']},controller.signal)).rejects.toHaveProperty('name','AbortError')
    expect(fetch).not.toHaveBeenCalled()
  })
})

import {afterEach,describe,expect,it,vi} from 'vitest'
import {MaskEncoder} from './maskEncoder'

describe('background mask encoder', () => {
  afterEach(() => {vi.unstubAllGlobals()})
  it('transfers a patch copy to a worker without scanning or detaching the live stroke', async () => {
    const send=vi.fn(),terminate=vi.fn()
    const workers:Array<{onmessage:(event:unknown) => void,postMessage:typeof send,terminate:typeof terminate}>=[]
    vi.stubGlobal('OffscreenCanvas',class {})
    vi.stubGlobal('Worker',class {onmessage=(_event:unknown) => {};postMessage=send;terminate=terminate;constructor() {workers.push(this)}})
    const encoder=new MaskEncoder(),pixels=new Uint8Array([255,0,128,0])
    const encoded=encoder.encode({x:3000,y:2500,width:2,height:2,pixels})
    const [{id,patch},transfer]=send.mock.calls[0]
    expect(patch.pixels).not.toBe(pixels)
    expect([...pixels]).toEqual([255,0,128,0])
    expect(transfer).toEqual([patch.pixels.buffer])
    const blob=new Blob(['patch'],{type:'image/png'})
    workers[0].onmessage({data:{id,blob}})
    await expect(encoded).resolves.toBe(blob)
    encoder.dispose();expect(terminate).toHaveBeenCalledOnce()
  })
  it('rejects interrupted encodes so the save queue can retain and retry the stroke', async () => {
    vi.stubGlobal('OffscreenCanvas',class {})
    vi.stubGlobal('Worker',class {postMessage() {} terminate() {}})
    const encoder=new MaskEncoder()
    const encoded=encoder.encode({x:0,y:0,width:1,height:1,pixels:new Uint8Array([255])})
    const failed=expect(encoded).rejects.toThrow('已中断')
    encoder.dispose();await failed
  })
})

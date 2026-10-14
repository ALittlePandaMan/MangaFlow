import {describe,expect,it,vi} from 'vitest'
import {MaskAutosaveQueue} from './maskAutosave'
import {MaskBitmap} from './maskBitmap'

function deferred() {let resolve!:() => void; const promise=new Promise<void>(yes => {resolve=yes}); return {promise,resolve}}
const frame=(pixels:Uint8Array) => ({x:0,y:0,width:pixels.length,height:1,pixels})

describe('ordered mask autosave', () => {
  it('keeps every stroke immutable and serializes slow writes instead of overwriting newer pixels', async () => {
    const first=deferred(),saved=vi.fn(),changed=vi.fn()
    const persist=vi.fn(async (_patch:ReturnType<typeof frame>) => {if (persist.mock.calls.length===1) await first.promise})
    const queue=new MaskAutosaveQueue(persist,saved,changed)
    const pixels=new Uint8Array([255,0,0,0])
    queue.enqueue(frame(pixels))
    pixels[1]=255; queue.enqueue(frame(pixels)); pixels[2]=255
    expect(persist).toHaveBeenCalledOnce()
    const done=queue.flush()
    first.resolve(); await done
    expect(persist.mock.calls.map(([item]) => [...item.pixels])).toEqual([[255,0,0,0],[255,255,0,0]])
    expect(saved).toHaveBeenCalledTimes(2)
    expect(queue.pending).toBe(false)
    expect(changed.mock.lastCall![0]).toEqual({pending:false,blocked:false,error:null})
  })

  it('retains a failed frame and following strokes, then retries in their original order', async () => {
    const failed=new Error('offline'),changed=vi.fn(),saved=vi.fn()
    const persist=vi.fn(async (_patch:ReturnType<typeof frame>) => {})
    persist.mockRejectedValueOnce(failed)
    const queue=new MaskAutosaveQueue(persist,saved,changed)
    queue.enqueue(frame(new Uint8Array([255,0])))
    await expect(queue.flush()).rejects.toThrow('offline')
    queue.enqueue(frame(new Uint8Array([255,255])))
    expect(persist).toHaveBeenCalledOnce()
    expect(changed.mock.lastCall![0].error).toBe(failed)
    await queue.flush()
    expect(persist.mock.calls.map(([item]) => [...item.pixels])).toEqual([[255,0],[255,0],[255,255]])
    expect(saved).toHaveBeenCalledTimes(2)
    expect(queue.pending).toBe(false)
  })

  it('bounds actual patch bytes and resumes as pending writes complete', async () => {
    const first=deferred(),queue=new MaskAutosaveQueue(async () => {await first.promise},() => {},() => {},2)
    queue.enqueue(frame(new Uint8Array(1))); expect(queue.blocked).toBe(false)
    queue.enqueue(frame(new Uint8Array(1))); expect(queue.blocked).toBe(true)
    first.resolve(); await queue.flush()
    expect(queue.blocked).toBe(false)
  })

  it('does not mark a newer in-progress bitmap as saved when an older request finishes', async () => {
    const first=deferred(),mask=new MaskBitmap(20,20)
    const queue=new MaskAutosaveQueue(async () => {await first.promise},patch => mask.markPatchSaved(patch),() => {})
    mask.paint({x:4,y:4},{x:4,y:4},4,'brush'); expect(mask.commit()).toBe(true)
    queue.enqueue(mask.patch())
    mask.paint({x:15,y:15},{x:15,y:15},4,'brush'); mask.commit()
    first.resolve(); await queue.flush()
    expect(mask.dirty).toBe(true)
    mask.undo(); expect(mask.dirty).toBe(false)
    mask.redo(); expect(mask.dirty).toBe(true)
  })

  it('does not pause large-page painting behind a slow save after a few small strokes', async () => {
    const first=deferred(),persist=vi.fn(async () => {await first.promise})
    const queue=new MaskAutosaveQueue(persist,() => {},() => {})
    for (let i=0;i<100;i++) queue.enqueue({x:i*10,y:2800,width:10,height:10,pixels:new Uint8Array(100)})
    expect(queue.blocked).toBe(false)
    first.resolve();await queue.flush()
    expect(persist).toHaveBeenCalledTimes(100)
  })

  it('orders history commands with uploads without turning undo into a new mask edit', async () => {
    const gate=deferred(),events:string[]=[]
    const queue=new MaskAutosaveQueue(async () => {events.push('save');await gate.promise},() => {},() => {})
    queue.enqueue(frame(new Uint8Array([255])))
    queue.enqueue(frame(new Uint8Array([0])),async () => {events.push('undo')})
    queue.enqueue(frame(new Uint8Array([255])),async () => {events.push('redo')})
    expect(events).toEqual(['save'])
    gate.resolve();await queue.flush()
    expect(events).toEqual(['save','undo','redo'])
  })
})

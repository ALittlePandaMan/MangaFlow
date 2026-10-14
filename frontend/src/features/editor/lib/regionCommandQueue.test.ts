import {describe,expect,it,vi} from 'vitest'
import {RegionCommandQueue,type RegionCommand} from './regionCommandQueue'
import type {EditorHistoryState} from '../../../types'

const step=(sequence:number):RegionCommand=>({pageId:'page',ids:[`region-${sequence}`],before:[],after:[],sequence,label:'编辑',entryId:sequence})
const deferred=()=>{let resolve!:()=>void;const promise=new Promise<void>(done=>{resolve=done});return {promise,resolve}}
describe('synchronous region save ordering',()=>{
  it('stays pending until ordered saves and their completion work have finished',async()=>{
    const changed=vi.fn(),queue=new RegionCommandQueue(changed),first=step(1),second=step(2),gate=deferred(),events:string[]=[]
    queue.record(first);queue.record(second)
    const save=queue.enqueue(async()=>{events.push('save');await gate.promise})
    expect(queue.pending).toBe(true)
    expect(queue.last).toBe(second)
    queue.enqueue(async()=>{events.push('save second')})
    const third=step(3);queue.record(third);queue.enqueue(async()=>{events.push('edit third')})
    expect(queue.last).toBe(third)
    gate.resolve();await save;await queue.flush()
    expect(events).toEqual(['save','save second','edit third'])
    expect(queue.pending).toBe(false)
  })
  it('cancels dependent jobs after a failure rather than applying them to the wrong document',async()=>{
    const queue=new RegionCommandQueue(vi.fn()),run=vi.fn(),failure=new Error('offline')
    const first=queue.enqueue(async()=>{throw failure}),second=queue.enqueue(async()=>{run()})
    await expect(first).rejects.toBe(failure);await expect(second).rejects.toBe(failure)
    await queue.settled()
    expect(run).not.toHaveBeenCalled();expect(queue.failed).toBe(true)
    queue.reset();await queue.enqueue(async()=>{run()})
    expect(run).toHaveBeenCalledOnce()
  })
  it('invalidates form coalescing metadata after another command changes the server history',()=>{
    const queue=new RegionCommandQueue(vi.fn());queue.record(step(1))
    const state={undo_id:2,redo_id:null} as EditorHistoryState
    queue.observe(state);expect(queue.last).toBeUndefined()
  })
  it('only retains the most recent form coalescing metadata and removes a no-op',()=>{
    const queue=new RegionCommandQueue(vi.fn()),command=step(1);queue.record(command)
    queue.record(step(2));queue.remove(command);expect(queue.last?.sequence).toBe(2)
    queue.remove(queue.last!);expect(queue.last).toBeUndefined()
  })
})

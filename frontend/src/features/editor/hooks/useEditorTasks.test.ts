import {act, renderHook, waitFor} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {ApiError, api} from '../../../services/api'
import {taskFixture} from '../lib/taskActivity.testUtils'
import {useEditorTasks} from './useEditorTasks'

afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers()})

it('recovers five tasks with one batched monitor and keeps them while the visible page changes', async () => {
  const running = Array.from({length:5}, (_, i) => taskFixture(`${i}`, {status:'RUNNING'}))
  vi.spyOn(api.tasks,'list').mockResolvedValueOnce(running).mockResolvedValue([])
  const statuses = vi.spyOn(api.tasks,'statuses').mockResolvedValue(running.map(task => ({...task,status:'COMPLETED',progress:1})))
  const get = vi.spyOn(api.tasks,'get')
  const update = vi.fn()
  const {result, rerender} = renderHook(({page}) => {void page; return useEditorTasks('project',update)}, {initialProps:{page:'first'}})
  await waitFor(() => expect(update).toHaveBeenCalledTimes(5))
  const activity = result.current.activity
  rerender({page:'second'})
  expect(result.current.activity).toBe(activity)
  expect(activity.getSnapshot().tasks).toHaveLength(0)
  await waitFor(()=>expect(activity.getSnapshot().completion).toMatchObject({total:5,completed:5}))
  expect(statuses).toHaveBeenCalledOnce()
  expect(get).not.toHaveBeenCalled()
  expect(api.tasks.list).toHaveBeenCalledWith('project', undefined, {limit:400,offset:0,activeFirst:true})
})

it('restores all 67 live activities before any completes instead of only the five visible rows', async () => {
  const running=Array.from({length:67},(_,i) => taskFixture(`${i}`))
  const list=vi.spyOn(api.tasks,'list').mockResolvedValueOnce(running).mockResolvedValue([])
  let finish!:(tasks:ReturnType<typeof taskFixture>[]) => void
  const statuses=vi.spyOn(api.tasks,'statuses').mockReturnValue(new Promise(resolve => {finish=resolve}))
  const {result}=renderHook(() => useEditorTasks('project',vi.fn()))
  await waitFor(() => expect(result.current.activity.getSnapshot().tasks).toHaveLength(67))
  expect(list).toHaveBeenCalledOnce()
  expect(statuses).toHaveBeenCalledExactlyOnceWith(running.map(task => task.id))
  await act(async () => finish(running.map(task => ({...task,status:'COMPLETED',progress:1}))))
  await waitFor(() => expect(result.current.activity.getSnapshot().completion).toMatchObject({total:67,completed:67}))
})

it('paginates recovery and status requests for books over 400 tasks without polling history', async () => {
  const running=Array.from({length:401},(_,i) => taskFixture(`${i}`))
  const list=vi.spyOn(api.tasks,'list').mockResolvedValueOnce(running.slice(0,400))
    .mockResolvedValueOnce([running[400],taskFixture('old',{status:'FAILED'})]).mockResolvedValue([])
  let finish!:(tasks:ReturnType<typeof taskFixture>[]) => void
  const statuses=vi.spyOn(api.tasks,'statuses').mockReturnValueOnce(new Promise(resolve => {finish=resolve}))
    .mockResolvedValueOnce([{...running[400],status:'COMPLETED'}])
  const {result}=renderHook(() => useEditorTasks('project',vi.fn()))
  await waitFor(() => expect(result.current.activity.getSnapshot().tasks).toHaveLength(401))
  expect(list).toHaveBeenNthCalledWith(1,'project',undefined,{limit:400,offset:0,activeFirst:true})
  expect(list).toHaveBeenNthCalledWith(2,'project',undefined,{limit:400,offset:400,activeFirst:true})
  expect(result.current.activity.getSnapshot().completion).toBeNull()
  await act(async () => finish(running.slice(0,400).map(task => ({...task,status:'COMPLETED'}))))
  await waitFor(() => expect(result.current.activity.getSnapshot().completion).toMatchObject({total:401,completed:401,failed:0}))
  expect(statuses.mock.calls.map(([ids]) => ids.length)).toEqual([400,1])
  expect(statuses.mock.calls.flatMap(([ids]) => ids)).not.toContain('old')
})

it('does not start a second poller for locally owned task ids', async () => {
  let resolve!: (value: ReturnType<typeof taskFixture>[]) => void
  vi.spyOn(api.tasks,'list').mockReturnValue(new Promise(done => {resolve=done}))
  const completed = taskFixture('1', {status:'COMPLETED',progress:1})
  const get = vi.spyOn(api.tasks,'get').mockResolvedValue(completed)
  const statuses = vi.spyOn(api.tasks,'statuses')
  const {result} = renderHook(() => useEditorTasks('project',vi.fn()))
  let done!: Promise<unknown>
  await act(async () => {
    done = result.current.waitForTask('1')
    resolve([taskFixture('1')])
    await done
  })
  expect(get).toHaveBeenCalledOnce()
  expect(statuses).not.toHaveBeenCalled()
})

it('recovers a broken status connection without submitting or retrying inference', async () => {
  vi.useFakeTimers()
  vi.spyOn(api.tasks,'list').mockResolvedValueOnce([]).mockResolvedValue([taskFixture('1', {status:'COMPLETED',progress:1})])
  vi.spyOn(api.tasks,'get').mockRejectedValue(new Error('status connection unavailable'))
  const submit = vi.spyOn(api.images,'process')
  const network = vi.spyOn(globalThis,'fetch').mockRejectedValue(new Error('Unexpected network request'))
  const {result} = renderHook(() => useEditorTasks('project', vi.fn()))
  await act(async () => {await Promise.resolve()})
  await act(async () => {
    result.current.activity.update([taskFixture('1', {status:'RUNNING'})])
    await expect(result.current.waitForTask('1')).rejects.toThrow('status connection unavailable')
  })
  expect(result.current.activity.getSnapshot().connectionIssue).toBe(true)
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(result.current.activity.getSnapshot().connectionIssue).toBe(false)
  expect(result.current.activity.getSnapshot().active).toBe(0)
  expect(submit).not.toHaveBeenCalled()
  expect(network).not.toHaveBeenCalled()
})

it('ignores historical terminal tasks without polling them or showing a completion notification', async () => {
  vi.spyOn(api.tasks,'list').mockResolvedValue([taskFixture('old',{status:'COMPLETED'})])
  const statuses=vi.spyOn(api.tasks,'statuses')
  const {result}=renderHook(()=>useEditorTasks('project',vi.fn()))
  await waitFor(()=>expect(result.current.activity.getSnapshot().recovering).toBe(false))
  expect(result.current.activity.getSnapshot()).toMatchObject({tasks:[],total:0,completion:null})
  expect(statuses).not.toHaveBeenCalled()
})

it('waits for every recovery chunk and counts only newly observed in-flight tasks', async () => {
  const first=taskFixture('1',{status:'RUNNING'}),second=taskFixture('2',{status:'RUNNING'})
  vi.spyOn(api.tasks,'list').mockResolvedValueOnce([first]).mockResolvedValueOnce([second]).mockResolvedValue([taskFixture('old',{status:'COMPLETED'})])
  let resolve!: (tasks: ReturnType<typeof taskFixture>[]) => void
  vi.spyOn(api.tasks,'statuses').mockResolvedValueOnce([{...first,status:'COMPLETED'}])
    .mockReturnValueOnce(new Promise(done=>{resolve=done}))
  const {result}=renderHook(()=>useEditorTasks('project',vi.fn()))
  await waitFor(()=>expect(result.current.activity.getSnapshot().total).toBe(2))
  expect(result.current.activity.getSnapshot().completion).toBeNull()
  await act(async()=>resolve([{...second,status:'COMPLETED'}]))
  await waitFor(()=>expect(result.current.activity.getSnapshot().completion).toMatchObject({total:2,completed:2}))
})

it('stops only the requested current task and guards duplicate, unknown and terminal requests', async () => {
  vi.spyOn(api.tasks,'list').mockResolvedValue([])
  let resolve!: (task: ReturnType<typeof taskFixture>) => void
  const cancel=vi.spyOn(api.tasks,'cancel').mockReturnValue(new Promise(done=>{resolve=done}))
  const {result}=renderHook(()=>useEditorTasks('project',vi.fn()))
  await waitFor(()=>expect(result.current.activity.getSnapshot().recovering).toBe(false))
  act(()=>result.current.activity.begin([taskFixture('1',{status:'RUNNING'}),taskFixture('2'),taskFixture('3',{status:'COMPLETED'})]))
  let stopping!:Promise<void>
  await act(async()=>{
    stopping=result.current.cancelTask('1')
    await result.current.cancelTask('1')
    await result.current.cancelTask('missing')
    await result.current.cancelTask('3')
  })
  expect(cancel).toHaveBeenCalledExactlyOnceWith('1')
  await act(async()=>{resolve(taskFixture('1',{status:'CANCELLING'}));await stopping})
  await act(async()=>result.current.cancelTask('1'))
  expect(cancel).toHaveBeenCalledOnce()
  expect(result.current.activity.getTask('2')?.status).toBe('QUEUED')
})

it('accepts a completion race with stop without replaying inference', async () => {
  vi.spyOn(api.tasks,'list').mockResolvedValue([])
  const cancel=vi.spyOn(api.tasks,'cancel').mockRejectedValue(new ApiError(409,'Already complete'))
  const get=vi.spyOn(api.tasks,'get').mockResolvedValue(taskFixture('1',{status:'COMPLETED',progress:1}))
  const submit=vi.spyOn(api.images,'process')
  const {result}=renderHook(()=>useEditorTasks('project',vi.fn()))
  await waitFor(()=>expect(result.current.activity.getSnapshot().recovering).toBe(false))
  act(()=>result.current.activity.begin([taskFixture('1',{status:'RUNNING'})]))
  await act(async()=>result.current.cancelTask('1'))
  expect(cancel).toHaveBeenCalledExactlyOnceWith('1')
  expect(get).toHaveBeenCalledExactlyOnceWith('1')
  expect(result.current.activity.getSnapshot().completion).toMatchObject({total:1,completed:1})
  expect(submit).not.toHaveBeenCalled()
})

it('retains the page lock until cancellation results have been adopted',async()=>{
  vi.spyOn(api.tasks,'list').mockResolvedValue([])
  const cancelled=taskFixture('1',{status:'CANCELLED'})
  vi.spyOn(api.tasks,'cancel').mockResolvedValue(cancelled)
  let adopt!:()=>void
  const update=vi.fn(()=>new Promise<void>(resolve=>{adopt=resolve}))
  const {result}=renderHook(()=>useEditorTasks('project',update))
  await waitFor(()=>expect(result.current.activity.getSnapshot().recovering).toBe(false))
  act(()=>result.current.activity.begin([taskFixture('1',{status:'RUNNING'})]))
  let stopping!:Promise<void>
  await act(async()=>{stopping=result.current.cancelTask('1');await Promise.resolve()})
  expect(update).toHaveBeenCalledWith(cancelled)
  expect(result.current.activity.getPageTask('page-1')?.status).toBe('RUNNING')
  await act(async()=>{adopt();await stopping})
  expect(result.current.activity.getPageTask('page-1')).toBeNull()
})

it('does not let a recovery listing release an owned page during result adoption',async()=>{
  let list!: (tasks:ReturnType<typeof taskFixture>[])=>void,adopt!:()=>void
  vi.spyOn(api.tasks,'list').mockReturnValue(new Promise(resolve=>{list=resolve}))
  const completed=taskFixture('1',{status:'COMPLETED'})
  vi.spyOn(api.tasks,'get').mockResolvedValue(completed)
  const update=vi.fn(()=>new Promise<void>(resolve=>{adopt=resolve}))
  const recoveredUpdate=vi.fn()
  const {result}=renderHook(()=>useEditorTasks('project',recoveredUpdate))
  act(()=>result.current.activity.begin([taskFixture('1',{status:'RUNNING'})]))
  let monitoring!:Promise<unknown>
  await act(async()=>{monitoring=result.current.waitForTask('1',update);await Promise.resolve()})
  await act(async()=>list([completed]))
  expect(result.current.activity.getPageTask('page-1')?.status).toBe('RUNNING')
  expect(recoveredUpdate).not.toHaveBeenCalled()
  await act(async()=>{adopt();await monitoring})
  expect(result.current.activity.getPageTask('page-1')).toBeNull()
})

it('keeps a task active after a failed stop and leaves retries to an explicit user click', async () => {
  vi.spyOn(api.tasks,'list').mockResolvedValue([])
  const cancel=vi.spyOn(api.tasks,'cancel').mockRejectedValue(new ApiError(503,'Unavailable'))
  const get=vi.spyOn(api.tasks,'get')
  const {result}=renderHook(()=>useEditorTasks('project',vi.fn()))
  await waitFor(()=>expect(result.current.activity.getSnapshot().recovering).toBe(false))
  act(()=>result.current.activity.begin([taskFixture('1',{status:'RUNNING'})]))
  await act(async()=>{await expect(result.current.cancelTask('1')).rejects.toThrow('Unavailable')})
  expect(result.current.activity.getSnapshot().active).toBe(1)
  expect(cancel).toHaveBeenCalledOnce()
  expect(get).not.toHaveBeenCalled()
})

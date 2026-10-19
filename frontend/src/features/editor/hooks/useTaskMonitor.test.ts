import {act, renderHook} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {api} from '../../../services/api'
import type {ProcessingTask} from '../../../types'
import {useTaskMonitor} from './useTaskMonitor'

afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers()})

it('stops stale task responses after switching pages', async () => {
  let resolve!: (task:ProcessingTask) => void
  vi.spyOn(api.tasks,'get').mockImplementation(() => new Promise(done => {resolve=done}))
  const {result,rerender}=renderHook(({page}) => useTaskMonitor(page), {initialProps:{page:'first'}})
  const update=vi.fn()
  const pending=result.current.waitForTask('task',update)
  rerender({page:'second'})
  await act(async () => resolve({id:'task',status:'COMPLETED'} as ProcessingTask))
  expect(await pending).toBeNull()
  expect(update).not.toHaveBeenCalled()
})

it('returns completed tasks without polling again', async () => {
  const task={id:'task',status:'COMPLETED',message:'done',progress:1} as ProcessingTask
  const get=vi.spyOn(api.tasks,'get').mockResolvedValue(task)
  const {result}=renderHook(() => useTaskMonitor('first'))
  const update=vi.fn()
  expect(await result.current.waitForTask('task',update)).toBe(task)
  expect(get).toHaveBeenCalledOnce()
  expect(update).toHaveBeenCalledOnce()
})

it('monitors 350 pages with one request per round and stops polling completed tasks', async () => {
  vi.useFakeTimers()
  const tasks = Array.from({length:350}, (_, i) => ({id:`task-${i}`,status:'QUEUED',progress:0,updated_at:'first'} as ProcessingTask))
  const statuses = vi.spyOn(api.tasks, 'statuses')
    .mockResolvedValueOnce(tasks.map((task, i) => i < 348 ? {...task,status:'COMPLETED',progress:1} : task))
    .mockResolvedValueOnce(tasks.slice(-2).map(task => ({...task,status:'COMPLETED',progress:1})))
  const single = vi.spyOn(api.tasks, 'get')
  const {result} = renderHook(() => useTaskMonitor('page'))
  const update = vi.fn()
  const pending = result.current.waitForTasks(tasks.map(task => task.id), update)
  await act(async () => {await vi.advanceTimersByTimeAsync(1600)})
  expect((await pending)?.length).toBe(350)
  expect(statuses.mock.calls.map(([ids]) => ids.length)).toEqual([350,2])
  expect(statuses.mock.calls[1][0]).toEqual(['task-348','task-349'])
  expect(single).not.toHaveBeenCalled()
  expect(update).toHaveBeenCalledTimes(352)
})

it('bounds large status batches and includes failed/cancelled results in input order', async () => {
  const ids = Array.from({length:901}, (_, i) => `${i}`)
  const statuses = vi.spyOn(api.tasks, 'statuses').mockImplementation(async batch => batch.map(id => ({
    id, status:Number(id) % 2 ? 'FAILED' : 'CANCELLED', progress:1,
  } as ProcessingTask)).reverse())
  const {result} = renderHook(() => useTaskMonitor('page'))
  expect((await result.current.waitForTasks(ids))?.map(task => task.id)).toEqual(ids)
  expect(statuses.mock.calls.map(([batch]) => batch.length)).toEqual([400,400,101])
})

it('ignores batch responses after leaving the current page', async () => {
  let resolve!: (tasks:ProcessingTask[]) => void
  vi.spyOn(api.tasks, 'statuses').mockReturnValue(new Promise(done => {resolve=done}))
  const {result,rerender} = renderHook(({page}) => useTaskMonitor(page), {initialProps:{page:'first'}})
  const update = vi.fn()
  const pending = result.current.waitForTasks(['task'], update)
  rerender({page:'second'})
  await act(async () => resolve([{id:'task',status:'COMPLETED'} as ProcessingTask]))
  expect(await pending).toBeNull()
  expect(update).not.toHaveBeenCalled()
})

it('rejects missing batch results instead of falsely reporting success', async () => {
  vi.spyOn(api.tasks, 'statuses').mockResolvedValue([])
  const {result} = renderHook(() => useTaskMonitor('page'))
  await expect(result.current.waitForTasks(['task'])).rejects.toThrow('返回不完整')
})

it('delivers dependency and parallel-stage changes even without a progress change', async () => {
  vi.useFakeTimers()
  const task = {id:'task',status:'QUEUED',progress:0,updated_at:'same'} as ProcessingTask
  vi.spyOn(api.tasks,'statuses')
    .mockResolvedValueOnce([{...task,waiting_reason:'previous_page_translation'}])
    .mockResolvedValueOnce([{...task,waiting_reason:'previous_page_paused'}])
    .mockResolvedValueOnce([{...task,status:'RUNNING',active_stages:['translation','mask']}])
    .mockResolvedValueOnce([{...task,status:'RUNNING',active_stages:['translation','inpainting']}])
    .mockResolvedValueOnce([{...task,status:'COMPLETED'}])
  const {result} = renderHook(() => useTaskMonitor('page'))
  const update = vi.fn()
  const pending = result.current.waitForTasks(['task'],update)
  await act(async () => {await vi.advanceTimersByTimeAsync(4800)})
  await pending
  expect(update).toHaveBeenCalledTimes(5)
})

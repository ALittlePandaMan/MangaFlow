import {act, renderHook} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {api} from '../../../services/api'
import type {ProcessingTask} from '../../../types'
import {useTaskMonitor} from './useTaskMonitor'

afterEach(() => vi.restoreAllMocks())

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

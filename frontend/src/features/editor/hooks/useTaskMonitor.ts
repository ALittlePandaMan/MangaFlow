import {useCallback, useEffect, useRef} from 'react'
import {ApiError, api} from '../../../services/api'
import type {ProcessingTask} from '../../../types'

export type TaskUpdateHandler = (task: ProcessingTask) => void | Promise<void>

const taskVersion = (task: ProcessingTask) => JSON.stringify([
  task.status, task.current_stage, task.progress, task.message, task.updated_at,
  task.waiting_reason, task.error_kind, task.active_stages, task.pause_requested,
])

export function useTaskMonitor(pageId: string | undefined, onSnapshots?: (tasks: ProcessingTask[]) => void) {
  const snapshots = useRef(onSnapshots)
  snapshots.current = onSnapshots
  const taskMonitorGeneration=useRef(0)
  useEffect(() => {
    const generation = taskMonitorGeneration
    generation.current++
    return () => {generation.current++}
  }, [pageId])
  const waitForTask = useCallback(async (taskId: string, onUpdate?: TaskUpdateHandler) => {
    const generation = taskMonitorGeneration.current
    let transientFailures = 0
    let lastVersion = ''
    while (generation === taskMonitorGeneration.current) {
      try {
        const task = await api.tasks.get(taskId)
        if (generation !== taskMonitorGeneration.current) return null
        snapshots.current?.([task])
        transientFailures = 0
        const version = taskVersion(task)
        if (version !== lastVersion) {
          await onUpdate?.(task)
          lastVersion = version
        }
        if (['COMPLETED','FAILED','CANCELLED'].includes(task.status)) return task
        await new Promise(resolve => window.setTimeout(resolve, task.status === 'QUEUED' ? 1600 : 800))
      } catch (reason) {
        if (generation !== taskMonitorGeneration.current) return null
        const transientGatewayError = reason instanceof ApiError && [502, 503, 504].includes(reason.status)
        const transientNetworkError = reason instanceof TypeError
        if ((!transientGatewayError && !transientNetworkError) || transientFailures >= 20) throw reason
        transientFailures += 1
        await new Promise(resolve => window.setTimeout(resolve, Math.min(3000, 800 + transientFailures * 200)))
      }
    }
    return null
  }, [])

  const waitForTasks = useCallback(async (taskIds: string[], onUpdate?: TaskUpdateHandler) => {
    const generation = taskMonitorGeneration.current
    const pending = new Set(taskIds)
    const results = new Map<string, ProcessingTask>()
    const versions = new Map<string, string>()
    let transientFailures = 0
    while (pending.size && generation === taskMonitorGeneration.current) {
      const ids = [...pending]
      const updates: ProcessingTask[] = []
      try {
        // One request for a typical book. Bound requests and response sizes even
        // for projects with thousands of pages; completed tasks leave the set.
        for (let start = 0; start < ids.length; start += 400) {
          if (generation !== taskMonitorGeneration.current) return null
          updates.push(...await api.tasks.statuses(ids.slice(start, start + 400)))
        }
        transientFailures = 0
      } catch (reason) {
        if (generation !== taskMonitorGeneration.current) return null
        const transient = reason instanceof TypeError || reason instanceof ApiError && [502,503,504].includes(reason.status)
        if (!transient || transientFailures >= 20) throw reason
        transientFailures++
        await new Promise(resolve => window.setTimeout(resolve, Math.min(3000,800+transientFailures*200)))
        continue
      }
      if (generation !== taskMonitorGeneration.current) return null
      const returned = new Set(updates.map(task => task.id))
      if (ids.some(id => !returned.has(id))) throw new Error('任务状态返回不完整，请检查任务列表')
      snapshots.current?.(updates)
      for (const task of updates) {
        if (!pending.has(task.id)) continue
        const version = taskVersion(task)
        if (versions.get(task.id) !== version) {
          await onUpdate?.(task)
          if (generation !== taskMonitorGeneration.current) return null
          versions.set(task.id, version)
        }
        if (['COMPLETED','FAILED','CANCELLED'].includes(task.status)) {
          results.set(task.id, task)
          pending.delete(task.id)
        }
      }
      if (pending.size) await new Promise(resolve => window.setTimeout(resolve,
        updates.some(task => task.status === 'RUNNING' || task.status === 'CANCELLING') ? 800 : 1600))
    }
    return generation === taskMonitorGeneration.current ? taskIds.map(id => results.get(id)!) : null
  }, [])

  return {generation:taskMonitorGeneration, waitForTask, waitForTasks}
}

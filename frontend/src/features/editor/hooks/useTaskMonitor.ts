import {useCallback, useEffect, useRef} from 'react'
import {ApiError, api} from '../../../services/api'
import type {ProcessingTask} from '../../../types'

export type TaskUpdateHandler = (task: ProcessingTask) => void | Promise<void>

export function useTaskMonitor(pageId: string | undefined) {
  const taskMonitorGeneration=useRef(0)
  useEffect(() => {
    taskMonitorGeneration.current++
    return () => {taskMonitorGeneration.current++}
  }, [pageId])
  const waitForTask = useCallback(async (taskId: string, onUpdate?: TaskUpdateHandler) => {
    const generation = taskMonitorGeneration.current
    let transientFailures = 0
    let lastVersion = ''
    while (generation === taskMonitorGeneration.current) {
      try {
        const task = await api.tasks.get(taskId)
        if (generation !== taskMonitorGeneration.current) return null
        transientFailures = 0
        const version = `${task.status}:${task.current_stage || ''}:${task.progress}:${task.message}:${task.updated_at}`
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


  return {generation:taskMonitorGeneration, waitForTask}
}

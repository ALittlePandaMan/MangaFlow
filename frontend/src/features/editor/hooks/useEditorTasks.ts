import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {ApiError, api} from '../../../services/api'
import type {ProcessingTask} from '../../../types'
import {isActiveTask, isTerminalTask, TaskActivity} from '../lib/taskActivity'
import {useTaskMonitor, type TaskUpdateHandler} from './useTaskMonitor'

/** Monitor by project, so reading another page does not abandon running work. */
export function useEditorTasks(projectId: string, onRecoveredUpdate: TaskUpdateHandler, onRecoveredSettled?: () => Promise<unknown>) {
  const activity = useMemo(() => new TaskActivity(projectId), [projectId])
  const snapshots = useCallback((tasks: ProcessingTask[]) => {activity.setConnectionIssue(false); activity.update(tasks)}, [activity])
  const monitor = useTaskMonitor(projectId, snapshots)
  const {waitForTask: monitorTask, waitForTasks: monitorTasks} = monitor
  const owned = useRef(new Set<string>()).current
  const cancelling = useRef(new Set<string>()).current
  const [recoveryAttempt, setRecoveryAttempt] = useState(0)
  const reconnectTimer = useRef<number | undefined>(undefined)
  const scheduleReconnect = useCallback(() => {
    activity.setConnectionIssue(true)
    window.clearTimeout(reconnectTimer.current)
    reconnectTimer.current = window.setTimeout(() => setRecoveryAttempt(value => value + 1), 5000)
  }, [activity])
  useEffect(() => () => window.clearTimeout(reconnectTimer.current), [projectId])
  const recoveredUpdate = useRef(onRecoveredUpdate)
  recoveredUpdate.current = onRecoveredUpdate
  const recoveredSettled = useRef(onRecoveredSettled)
  recoveredSettled.current = onRecoveredSettled
  const waitForTask = useCallback(async (id: string, update?: TaskUpdateHandler) => {
    owned.add(id)
    try {return await monitorTask(id, update)} catch (reason) {scheduleReconnect(); throw reason} finally {owned.delete(id)}
  }, [owned, monitorTask, scheduleReconnect])
  const waitForTasks = useCallback(async (ids: string[], update?: TaskUpdateHandler) => {
    ids.forEach(id => owned.add(id))
    try {return await monitorTasks(ids, update)} catch (reason) {scheduleReconnect(); throw reason} finally {ids.forEach(id => owned.delete(id))}
  }, [owned, monitorTasks, scheduleReconnect])

  useEffect(() => {
    let disposed = false
    const recover = async () => {
      if (!disposed) activity.setRecovering(true)
      try {
        // At most five recovery tasks are observed at once, using the same
        // bounded status request as batch processing, not five new pollers.
        while (!disposed) {
          const tasks = await api.tasks.list(projectId, undefined, {limit:5, offset:0, activeFirst:true})
          if (disposed) return
          snapshots(tasks)
          const active = tasks.filter(task => isActiveTask(task) && !owned.has(task.id))
          if (!active.length) {activity.setRecovering(false); return}
          const ids = active.map(task => task.id)
          ids.forEach(id => owned.add(id))
          let result
          try {
            result = await monitorTasks(ids, task => {if (!disposed) return recoveredUpdate.current(task)})
          } finally {ids.forEach(id => owned.delete(id))}
          if (disposed || !result) return
          await recoveredSettled.current?.()
        }
      } catch {
        // A failed recovery request is not a failed inference. Existing work
        // still runs server-side; retry recovery without replaying the task.
        if (!disposed) {
          activity.setConnectionIssue(true)
          timer = window.setTimeout(() => void recover(), 5000)
        }
      }
    }
    let timer: number | undefined
    void recover()
    return () => {disposed = true; window.clearTimeout(timer)}
  }, [projectId, activity, owned, monitorTasks, snapshots, recoveryAttempt])

  const recordTask = useCallback((task: ProcessingTask | null) => {
    if (task) activity.record(task)
  }, [activity])
  const cancelTask = useCallback(async (id: string) => {
    const task = activity.getTask(id)
    if (!task || isTerminalTask(task) || task.status === 'CANCELLING' || cancelling.has(id)) return
    cancelling.add(id)
    try {
      try {snapshots([await api.tasks.cancel(id)])}
      catch (reason) {
        // Completion may win the race with the stop click. Re-read; never retry inference.
        if (reason instanceof ApiError && reason.status === 409) {
          const latest = await api.tasks.get(id)
          snapshots([latest])
          if (isTerminalTask(latest) || latest.status === 'CANCELLING') return
        }
        throw reason
      }
    } finally {cancelling.delete(id)}
  }, [activity, cancelling, snapshots])
  return {...monitor, waitForTask, waitForTasks, activity, recordTask, cancelTask}
}

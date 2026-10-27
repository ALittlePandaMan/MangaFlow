import {createPortal} from 'react-dom'
import {useEffect, useLayoutEffect, useMemo} from 'react'
import {create} from 'zustand'
import {CanvasTaskList} from '../features/editor/components/CanvasTaskList'
import {useActivityPanelSource} from '../features/editor/lib/activityPanelStore'
import {isMcpTask, TaskActivity} from '../features/editor/lib/taskActivity'
import type {ImagePage} from '../types'
import type {ProcessingTask} from '../types'
import {api} from '../services/api'

const useGlobalTasks = create<{tasks: ProcessingTask[]}>(() => ({tasks:[]}))
const noPages: ImagePage[] = []

export function GlobalActivityPanel() {
  const source = useActivityPanelSource(state => state.source)
  const tasks=useGlobalTasks(state => state.tasks)
  const activity=useMemo(() => new TaskActivity('',true),[])
  const editorActivity=source?.props.activity
  useLayoutEffect(() => {activity.update(tasks)},[activity,tasks])
  const cancel=async (id:string) => {
    if (editorActivity?.getTask(id) && source?.props.onCancelTask) await source.props.onCancelTask(id)
    else activity.update([await api.tasks.cancel(id)])
  }
  if (source) {
    const hasOtherWork=tasks.some(task => !isMcpTask(task) && !['COMPLETED','FAILED','CANCELLED'].includes(task.status) && !source.props.pages.some(page => page.id === task.image_id))
    return createPortal(<CanvasTaskList {...source.props} activity={hasOtherWork ? activity : source.props.activity} onCancelTask={cancel}/>, source.target)
  }
  return <div className="global-activity-panel"><CanvasTaskList activity={activity} pages={noPages} onCancelTask={cancel}/></div>
}

export function GlobalProcessingMonitor() {
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const tasks: ProcessingTask[]=[]
        for (let offset=0; active; offset+=400) {
          const response=await fetch(`/api/tasks?active_first=true&limit=400&offset=${offset}`)
          if (!response.ok) throw new Error('Task discovery failed')
          const page: ProcessingTask[]=await response.json()
          if (!Array.isArray(page)) throw new Error('Invalid task snapshot')
          tasks.push(...page)
          if (page.length<400 || page.some(task => ['COMPLETED','FAILED','CANCELLED','PAUSED'].includes(task.status))) break
        }
        if (!active) return
        useGlobalTasks.setState({tasks})
      } catch { /* Keep the last snapshot during temporary disconnections. */ }
      finally {if (active) timer = setTimeout(() => void poll(), 1500)}
    }
    void poll()
    return () => {active=false;clearTimeout(timer)}
  },[])
  return null
}

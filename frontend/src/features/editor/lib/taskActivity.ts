import type {ProcessingTask} from '../../../types'

export const MAX_VISIBLE_TASKS = 5
export const COMPLETION_NOTICE_MS = 15_000
export const isActiveTask = (task: ProcessingTask) => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(task.status)
export const isTerminalTask = (task: ProcessingTask) => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.status)
export const taskProgress = (task: ProcessingTask) => task.status === 'COMPLETED' ? 1
  : Math.max(0, Math.min(1, Number.isFinite(task.progress) ? task.progress : 0))
const priority = (task: ProcessingTask) => ({RUNNING:0, CANCELLING:0, QUEUED:1, PAUSED:2} as Record<string, number>)[task.status] ?? 3
const newestFirst = (left: ProcessingTask, right: ProcessingTask) =>
  (right.created_at || '').localeCompare(left.created_at || '') || right.id.localeCompare(left.id)

export interface TaskCompletion {
  id: number
  total: number
  completed: number
  failed: number
  cancelled: number
  failures: ProcessingTask[]
}

interface ActivitySnapshot {
  tasks: ProcessingTask[]
  active: number
  total: number
  completed: number
  currentActive: number
  progress: number
  recovering: boolean
  completion: TaskCompletion | null
  connectionIssue: boolean
}

/** Monitor all in-flight work, but display only the current operation, never history. */
export class TaskActivity {
  private tasks = new Map<string, ProcessingTask>()
  private knownIds = new Set<string>()
  private terminalVersions = new Map<string,string>()
  private currentIds = new Set<string>()
  private listeners = new Set<() => void>()
  private operation = 0
  private dismissed = false
  private snapshot: ActivitySnapshot = {tasks:[], active:0, total:0, completed:0, currentActive:0,
    progress:0, recovering:false, completion:null, connectionIssue:false}
  constructor(private projectId: string) {}

  subscribe = (listener: () => void) => {this.listeners.add(listener); return () => {this.listeners.delete(listener)}}
  getSnapshot = () => this.snapshot
  getTask = (id: string) => this.tasks.get(id)
  getPageTask = (imageId?: string) => imageId
    ? [...this.tasks.values()].find(task => task.image_id === imageId && isActiveTask(task)) || null
    : null

  setConnectionIssue = (connectionIssue: boolean) => {
    if (this.snapshot.connectionIssue === connectionIssue) return
    this.snapshot = {...this.snapshot, connectionIssue}
    this.emit()
  }
  setRecovering = (recovering: boolean) => {
    if (this.snapshot.recovering === recovering) return
    this.snapshot = {...this.snapshot, recovering}
    this.publish()
  }

  /** Explicit submission may already be terminal when a very fast operation returns. */
  begin = (tasks: ProcessingTask[]) => {
    const accepted = tasks.filter(this.belongs)
    if (tasks.length && !accepted.length) return
    this.operation++
    this.dismissed = false
    this.currentIds = new Set(accepted.map(task => task.id))
    for (const task of accepted) {
      this.knownIds.add(task.id)
      if (this.isFresh(task)) this.tasks.set(task.id, task)
    }
    this.publish()
  }
  record = (task: ProcessingTask) => {
    if (!this.belongs(task)) return
    if (!this.knownIds.has(task.id) || !this.currentIds.size) this.begin([task])
    else this.update([task])
  }

  update = (updates: ProcessingTask[]) => {
    const fresh = updates.filter(task => this.belongs(task) && this.isFresh(task))
    const live = fresh.filter(task => !isTerminalTask(task))
    const hasCurrentWork = [...this.currentIds].some(id => {
      const task = this.tasks.get(id)
      return task && !isTerminalTask(task)
    })
    const startsNewWork = live.some(task => {
      const previous = this.tasks.get(task.id)
      return !this.knownIds.has(task.id) || (previous ? isTerminalTask(previous) : this.terminalVersions.has(task.id))
    })
    if (live.length && (!this.currentIds.size || !hasCurrentWork && startsNewWork && !this.snapshot.recovering)) {
      this.begin(live)
    }
    let changed = false
    for (const task of fresh) {
      // A recovery query can include old completed/failed jobs. They are not this operation.
      if (!this.knownIds.has(task.id) && isTerminalTask(task)) continue
      const previous = this.tasks.get(task.id)
      if (previous && JSON.stringify(previous) === JSON.stringify(task)) continue
      if (!this.knownIds.has(task.id)) this.currentIds.add(task.id)
      this.knownIds.add(task.id)
      this.tasks.set(task.id, task)
      changed = true
    }
    if (changed) this.publish()
  }

  dismissCompletion = (id: number) => {
    if (id !== this.operation || !this.snapshot.completion) return
    this.dismissed = true
    this.publish()
  }

  private belongs = (task: ProcessingTask) => !task.project_id || task.project_id === this.projectId
  private isFresh = (task: ProcessingTask) => {
    const previous = this.tasks.get(task.id)
    if (!previous) {
      const terminalVersion = this.terminalVersions.get(task.id)
      return !terminalVersion || task.updated_at > terminalVersion || task.updated_at === terminalVersion && isTerminalTask(task)
    }
    if (task.updated_at < previous.updated_at) return false
    return !(task.updated_at === previous.updated_at && isTerminalTask(previous) && !isTerminalTask(task))
  }
  private emit = () => {for (const listener of this.listeners) listener()}
  private publish = () => {
    // Keep active jobs for page locks even if they belong to a different visible operation.
    for (const [id, task] of this.tasks) if (!this.currentIds.has(id) && isTerminalTask(task)) {
      this.terminalVersions.set(id,task.updated_at)
      this.tasks.delete(id)
    }
    const all = [...this.tasks.values()]
    const current = all.filter(task => this.currentIds.has(task.id))
    const pending = current.filter(task => !isTerminalTask(task))
    const completed = current.filter(task => task.status === 'COMPLETED').length
    const failed = current.filter(task => task.status === 'FAILED')
    const cancelled = current.filter(task => task.status === 'CANCELLED').length
    const completion = current.length && !pending.length && !this.snapshot.recovering && !this.dismissed
      ? {id:this.operation, total:current.length, completed, failed:failed.length, cancelled, failures:failed.slice(0, MAX_VISIBLE_TASKS)} : null
    this.snapshot = {...this.snapshot,
      tasks:pending.sort((a,b) => priority(a)-priority(b) || newestFirst(a,b)).slice(0, MAX_VISIBLE_TASKS),
      active:all.filter(isActiveTask).length, total:current.length, completed, currentActive:pending.length,
      progress:current.length ? current.reduce((sum, task) => sum + taskProgress(task), 0) / current.length : 0,
      completion,
    }
    this.emit()
  }
}

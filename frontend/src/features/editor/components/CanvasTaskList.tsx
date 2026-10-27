import {t, useLocale} from '../../../i18n'
import {Check, CircleAlert, LoaderCircle, Square, X} from 'lucide-react'
import {memo, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react'
import type {ImagePage, ProcessingTask} from '../../../types'
import type {ProcessingScope} from '../../../components/LoadingUI'
import {cn, compactIconButtonClass, scrollbarClass} from '../../../ui'
import {pageTaskBusyLabel, stageLabel} from '../lib/pagePresentation'
import {COMPLETION_NOTICE_MS, MAX_VISIBLE_TASKS, TaskActivity, taskProgress} from '../lib/taskActivity'
import {dismissFileJobs, fileJobTerminal, isMcpFileJob, useFileJobs} from '../../../services/fileJobs'
import {FileActivityRow, LocalActivityRow, UploadActivityRow} from '../../../components/FileActivityRow'

const statusLabel = (task: ProcessingTask) => ({QUEUED:t("排队中"), RUNNING:task.pause_requested ? t("暂停中") : t("处理中"),
  CANCELLING:t("正在停止"), PAUSED:t("已暂停")} as Record<string, string>)[task.status] || task.status
const activityLabel = (task: ProcessingTask) => task.status === 'RUNNING' && !task.pause_requested
  ? task.active_stages && task.active_stages.length > 1 ? task.active_stages.map(stageLabel).join(' / ')
    : task.current_stage ? stageLabel(task.current_stage) : t("处理中")
  : task.status === 'QUEUED' && (task.task_type !== 'full' || typeof task.payload.start_stage === 'string')
    ? `${stageLabel(task.task_type === 'full' ? String(task.payload.start_stage) : task.task_type)} · ${statusLabel(task)}`
    : statusLabel(task)

export interface CanvasTaskListProps {
  activity: TaskActivity, pages: ImagePage[], pendingLabel?: string, scope?: ProcessingScope,
  batch?: {label: string, progress: number | null, detail?: string, onCancel?: () => void} | null,
  onCancelTask?: (id: string) => Promise<void>,
}

export const CanvasTaskList = memo(function CanvasTaskList({activity, pages, pendingLabel, scope, batch, onCancelTask}: CanvasTaskListProps) {
  useLocale()
  const state = useSyncExternalStore(activity.subscribe, activity.getSnapshot)
  const {tasks, completion, connectionIssue} = state
  const jobs = useFileJobs(state => state.jobs)
  const uploads = useFileJobs(state => state.uploads)
  const visibleJobIds = useFileJobs(state => state.visibleJobIds)
  const visibleIds = new Set(visibleJobIds)
  const fileTasks = jobs.filter(job => !isMcpFileJob(job) && (!fileJobTerminal(job.status) || visibleIds.has(job.id)))
  const uploadTasks = Object.entries(uploads).filter(([id]) => !id.startsWith('mcp:'))
  const hasRows = tasks.length + fileTasks.length + uploadTasks.length > 0 || !!batch
  const rowIds = [...tasks.map(task => task.id), ...uploadTasks.map(([id]) => `upload:${id}`), ...fileTasks.map(job => `file:${job.id}`), ...(batch ? ['local'] : [])].join(',')
  const completedFileIds = fileTasks.filter(job => fileJobTerminal(job.status)).map(job => job.id)
  const taskList=useRef<HTMLOListElement>(null)
  const pageDetails = useMemo(() => new Map(pages.map((page,index) => [page.id,
    {filename:page.filename, number:Number.isFinite(page.order_index) ? page.order_index+1 : index+1}])), [pages])
  const [cancelErrors, setCancelErrors] = useState<Record<string,string>>({})
  const [cancelling, setCancelling] = useState<string[]>([])
  const cancelRequests = useRef(new Set<string>()).current
  const waiting = !!pendingLabel || !!batch
  const completionId = !waiting && completion ? completion.id : undefined
  const failed = completion?.failed || 0
  useEffect(() => {
    if (completionId === undefined || failed) return
    const timer = window.setTimeout(() => activity.dismissCompletion(completionId), COMPLETION_NOTICE_MS)
    return () => window.clearTimeout(timer)
  }, [activity, completionId, failed])

  useLayoutEffect(() => {
    const list=taskList.current
    if (!list) return
    // A viewport holds five real rows, including optional provider/error lines.
    // Native scrolling keeps its position on progress updates without a render
    // for every wheel event. A new operation gets a fresh list via its key.
    const rows=Array.from(list.children).slice(0,MAX_VISIBLE_TASKS)
    const measure=() => {
      const height=rows.reduce((sum,row) => sum+row.getBoundingClientRect().height,0)
      if (height > 0) list.style.maxHeight=`${height}px`
    }
    measure()
    const observer=typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    rows.forEach(row => observer?.observe(row))
    return () => observer?.disconnect()
  }, [rowIds,state.operationId])

  const stop = async (id: string) => {
    if (!onCancelTask || cancelRequests.has(id)) return
    cancelRequests.add(id)
    setCancelling(ids => [...ids,id])
    setCancelErrors(errors => ({...errors,[id]:''}))
    try {await onCancelTask(id)}
    catch (reason) {setCancelErrors(errors => ({...errors,[id]:reason instanceof Error ? reason.message : String(reason)}))}
    finally {cancelRequests.delete(id); setCancelling(ids => ids.filter(value => value !== id))}
  }
  const finished = completionId !== undefined ? completion : null
  if (!hasRows && !waiting && !finished && !connectionIssue) return null
  return <aside className="canvas-task-list rounded-[18px] border border-line-subtle bg-popover text-secondary shadow-panel"
    aria-label={t("任务进度")} onPointerDown={event => event.stopPropagation()} onWheel={event => event.stopPropagation()}
    onKeyDown={event => event.stopPropagation()}>
    <header className="flex min-h-10 items-center justify-between gap-3 border-b border-line-subtle px-3.5 py-2.5">
      <h3 className="m-0 text-base font-semibold text-ink">{t("活动")}</h3>
      {(finished || completedFileIds.length > 0) && <button type="button" className={compactIconButtonClass}
        aria-label={t("关闭本次操作提示")} title={t("关闭提示")} onClick={() => {
          if (finished) activity.dismissCompletion(finished.id)
          dismissFileJobs(completedFileIds)
        }}><X size={14}/></button>}
    </header>
    {connectionIssue && <p className="m-0 border-b border-line-subtle px-3.5 py-2 text-xs leading-relaxed text-warning" role="status">{t("进度连接中断，正在重连。后台任务不会重新执行。")}</p>}
    {pendingLabel && scope && <div className="sr-only" role="status"
      aria-label={`${scope.kind === 'regions' ? t("区域处理：{{v0}}", {v0: scope.regionKeys.join('、')}) : t("整页处理：{{v0}}", {v0: scope.filename})} · ${pendingLabel}`}>
      {scope.kind === 'regions' ? t("区域处理 · {{v0}} 个 {{v1}}", {v0: scope.regionKeys.length, v1: scope.regionKeys.join('、')}) : t("整页处理")} · {scope.filename} · {pendingLabel}
    </div>}
    {hasRows && <ol key={state.operationId} ref={taskList} tabIndex={0} className={cn('canvas-task-items m-0 list-none p-0',scrollbarClass)} aria-label={t("本次操作的活动任务")}>{tasks.map(task => {
      const page = task.image_id ? pageDetails.get(task.image_id) : undefined
      const filename = page?.filename || t("漫画页面")
      const target = page ? t("第 {{v0}} 页：{{v1}}", {v0: page.number, v1: filename}) : filename
      const label = activityLabel(task)
      const detail = task.status === 'QUEUED' && !task.waiting_reason ? t("等待处理名额") : pageTaskBusyLabel(task,null)
      const percent = Math.round(taskProgress(task)*100)
      const provider = typeof task.payload.provider === 'string' ? task.payload.provider.trim() : ''
      const stopping = cancelling.includes(task.id) || task.status === 'CANCELLING'
      return <li key={task.id} className="grid grid-cols-[7px_minmax(0,1fr)_var(--icon-button-compact-size)] items-start gap-x-2.5 border-b border-line-subtle px-3.5 py-3 last:border-b-0"
        aria-label={`${filename} · ${statusLabel(task)}`} title={detail}>
        <span aria-hidden="true" className={cn('mt-[7px] size-[7px] rounded-full',task.status === 'PAUSED' ? 'bg-warning' : 'bg-accent')}/>
        <div className="col-start-2 row-start-1 min-w-0">
          <div className="flex min-w-0 items-center justify-between gap-2"><strong className="min-w-0 truncate text-sm font-semibold text-ink" title={label}>{label}</strong><span className="shrink-0 text-xs tabular-nums text-ink">{percent}%</span></div>
          <p className="mb-0 mt-1 truncate text-[13px] leading-[18px] text-muted" title={target}>{target}</p>
          {!!provider && <p className="m-0 truncate text-[13px] leading-[18px] text-muted" title={provider}>{provider}</p>}
          <span className="sr-only">{detail}</span>
        </div>
        {onCancelTask && <button type="button" className={cn(compactIconButtonClass,'col-start-3 row-start-1')}
          aria-label={t("停止 {{v0}} 的任务", {v0: filename})} title={t("停止当前任务，在安全节点生效")} disabled={stopping} aria-busy={stopping || undefined} onClick={() => void stop(task.id)}>
          {stopping ? <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none"/> : <Square size={11} fill="currentColor"/>}
        </button>}
        <div className="col-span-2 col-start-2 row-start-2 mt-2.5 h-1 min-w-0 overflow-hidden rounded-full bg-raised" role="progressbar" aria-label={t("{{v0}}任务进度", {v0: filename})} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className="motion-progress-fill h-full rounded-full bg-accent" style={{transform:`scaleX(${percent / 100})`}}/>
        </div>
        {cancelErrors[task.id] && <p className="col-span-2 col-start-2 row-start-3 mb-0 mt-2 break-words text-[13px] text-danger-soft-ink" role="alert">{t("停止失败：")}{cancelErrors[task.id]}</p>}
      </li>
    })}
      {uploadTasks.map(([id, upload]) => <UploadActivityRow key={`upload:${id}`} upload={upload}/>)}
      {fileTasks.map(job => <FileActivityRow key={`file:${job.id}`} job={job}/>)}
      {batch && <LocalActivityRow {...batch}/>}
    </ol>}
    {!hasRows && waiting && <div className="flex items-center gap-2.5 px-3.5 py-3 text-xs text-muted" role="status" aria-label={pendingLabel}>
      <LoaderCircle size={15} className="shrink-0 animate-spin text-accent motion-reduce:animate-none"/>
      <span className="min-w-0 truncate" title={pendingLabel}>{pendingLabel}</span>
    </div>}
    {finished && <div className="min-h-0 overflow-y-auto overscroll-contain flex items-start gap-2.5 px-3.5 py-3">
      {finished.failed ? <CircleAlert size={17} className="mt-0.5 shrink-0 text-danger-soft-ink"/> : finished.cancelled ? <Square size={14} className="mt-1 shrink-0 text-muted"/> : <Check size={17} className="mt-0.5 shrink-0 text-success"/>}
      <div className="min-w-0 flex-1" role="status" aria-live="polite">
        <strong className="block text-sm font-semibold text-ink">{finished.failed ? t("部分任务未完成") : finished.cancelled ? t("已停止") : t("已完成")}</strong>
        <p className="mb-0 mt-1 text-[13px] leading-relaxed text-muted">{finished.failed || finished.cancelled
          ? t("本次 {{v0}}/{{v1}} 个任务完成{{v2}}{{v3}}。", {v0: finished.completed, v1: finished.total, v2: finished.failed ? t("，{{v0}} 个失败", {v0: finished.failed}) : '', v3: finished.cancelled ? t("，{{v0}} 个已停止", {v0: finished.cancelled}) : ''})
          : t("本次操作已完成{{v0}}。", {v0: finished.total>1 ? t("，共 {{v0}} 个任务", {v0: finished.total}) : ''})}</p>
        {!!finished.failed && <details className="mt-2 text-[13px] text-danger-soft-ink"><summary className="cursor-pointer">{t("查看错误")}</summary>{finished.failures.map(task => <p key={task.id} className="mb-0 mt-1 break-words whitespace-pre-wrap">{task.image_id ? pageDetails.get(task.image_id)?.filename || t("漫画页面") : t("任务")}：{task.error_message || t("处理失败，请核对页面状态。")}</p>)}</details>}
      </div>
    </div>}
  </aside>
})

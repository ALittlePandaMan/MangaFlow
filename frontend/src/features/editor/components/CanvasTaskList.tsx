import {Check, CircleAlert, LoaderCircle, Square, X} from 'lucide-react'
import {memo, useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react'
import type {ImagePage, ProcessingTask} from '../../../types'
import type {ProcessingScope} from '../../../components/LoadingUI'
import {cn} from '../../../ui'
import {pageTaskBusyLabel, stageLabel} from '../lib/pagePresentation'
import {COMPLETION_NOTICE_MS, TaskActivity, taskProgress} from '../lib/taskActivity'

const statusLabel = (task: ProcessingTask) => ({QUEUED:'排队中', RUNNING:task.pause_requested ? '暂停中' : '处理中',
  CANCELLING:'正在停止', PAUSED:'已暂停'} as Record<string, string>)[task.status] || task.status
const activityLabel = (task: ProcessingTask) => task.status === 'RUNNING' && !task.pause_requested
  ? task.active_stages && task.active_stages.length > 1 ? task.active_stages.map(stageLabel).join(' / ')
    : task.current_stage ? stageLabel(task.current_stage) : '处理中'
  : statusLabel(task)

export const CanvasTaskList = memo(function CanvasTaskList({activity, pages, pendingLabel, scope, batch, onCancelTask}: {
  activity: TaskActivity, pages: ImagePage[], pendingLabel?: string, scope?: ProcessingScope,
  batch?: {label: string, progress: number | null} | null,
  onCancelTask?: (id: string) => Promise<void>,
}) {
  const state = useSyncExternalStore(activity.subscribe, activity.getSnapshot)
  const {tasks, completion, connectionIssue} = state
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
  if (!tasks.length && !waiting && !finished && !connectionIssue) return null
  return <aside className="canvas-task-list rounded-[18px] border border-line-subtle bg-popover text-secondary shadow-panel"
    aria-label="任务进度" onPointerDown={event => event.stopPropagation()} onWheel={event => event.stopPropagation()}
    onKeyDown={event => event.stopPropagation()}>
    <header className="flex min-h-10 items-center border-b border-line-subtle px-3.5 py-2.5"><h3 className="m-0 text-[13px] font-semibold text-ink">活动</h3></header>
    {connectionIssue && <p className="m-0 border-b border-line-subtle px-3.5 py-2 text-xs leading-relaxed text-warning" role="status">进度连接中断，正在重连。后台任务不会重新执行。</p>}
    {pendingLabel && scope && <div className="sr-only" role="status"
      aria-label={`${scope.kind === 'regions' ? `区域处理：${scope.regionKeys.join('、')}` : `整页处理：${scope.filename}`} · ${pendingLabel}`}>
      {scope.kind === 'regions' ? `区域处理 · ${scope.regionKeys.length} 个 ${scope.regionKeys.join('、')}` : '整页处理'} · {scope.filename} · {pendingLabel}
    </div>}
    {!!tasks.length && <ol className="m-0 list-none p-0" aria-label="本次操作的活动任务（最多 5 个）">{tasks.map(task => {
      const page = task.image_id ? pageDetails.get(task.image_id) : undefined
      const filename = page?.filename || '漫画页面'
      const target = page ? `第 ${page.number} 页：${filename}` : filename
      const label = activityLabel(task)
      const detail = task.status === 'QUEUED' && !task.waiting_reason ? '等待处理名额' : pageTaskBusyLabel(task,null)
      const percent = Math.round(taskProgress(task)*100)
      const provider = typeof task.payload.provider === 'string' ? task.payload.provider.trim() : ''
      const stopping = cancelling.includes(task.id) || task.status === 'CANCELLING'
      return <li key={task.id} className="grid grid-cols-[7px_minmax(0,1fr)_24px] items-start gap-x-2.5 border-b border-line-subtle px-3.5 py-3 last:border-b-0"
        aria-label={`${filename} · ${statusLabel(task)}`} title={detail}>
        <span aria-hidden="true" className={cn('mt-[7px] size-[7px] rounded-full',task.status === 'PAUSED' ? 'bg-warning' : 'bg-accent')}/>
        <div className="min-w-0">
          <div className="flex min-w-0 items-center justify-between gap-2"><strong className="min-w-0 truncate text-sm font-semibold text-ink" title={label}>{label}</strong><span className="shrink-0 text-xs tabular-nums text-ink">{percent}%</span></div>
          <p className="mb-0 mt-1 truncate text-xs leading-[18px] text-muted" title={target}>{target}</p>
          {!!provider && <p className="m-0 truncate text-xs leading-[18px] text-muted" title={provider}>{provider}</p>}
          <span className="sr-only">{detail}</span>
          <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-raised" role="progressbar" aria-label={`${filename}任务进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <div className="h-full rounded-full bg-accent transition-[width] motion-reduce:transition-none" style={{width:`${percent}%`}}/>
          </div>
          {cancelErrors[task.id] && <p className="mb-0 mt-2 break-words text-xs text-danger-soft-ink" role="alert">停止失败：{cancelErrors[task.id]}</p>}
        </div>
        {onCancelTask && <button type="button" className="grid size-6 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-ink outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-wait disabled:opacity-40"
          aria-label={`停止 ${filename} 的任务`} title="停止当前任务，在安全节点生效" disabled={stopping} aria-busy={stopping || undefined} onClick={() => void stop(task.id)}>
          {stopping ? <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none"/> : <Square size={11} fill="currentColor"/>}
        </button>}
      </li>
    })}</ol>}
    {!tasks.length && waiting && <div className="flex items-center gap-2.5 px-3.5 py-3 text-xs text-muted" role="status" aria-label={batch ? `正在${batch.label}…` : pendingLabel}>
      <LoaderCircle size={15} className="shrink-0 animate-spin text-accent motion-reduce:animate-none"/>
      <span className="min-w-0 truncate" title={pendingLabel || batch?.label}>{pendingLabel || `正在${batch?.label}…`}</span>
    </div>}
    {state.total > 1 && !finished && <footer className="border-t border-line-subtle px-3.5 py-2 text-xs text-muted">
      <div className="flex items-center justify-between gap-2"><span>本次完成 {state.completed}/{state.total}</span><span className="tabular-nums">{Math.round(state.progress*100)}%</span></div>
      {state.currentActive > tasks.length && <span className="mt-1 block">另有 {state.currentActive-tasks.length} 个任务</span>}
    </footer>}
    {finished && <div className="flex items-start gap-2.5 px-3.5 py-3">
      {finished.failed ? <CircleAlert size={17} className="mt-0.5 shrink-0 text-danger-soft-ink"/> : finished.cancelled ? <Square size={14} className="mt-1 shrink-0 text-muted"/> : <Check size={17} className="mt-0.5 shrink-0 text-success"/>}
      <div className="min-w-0 flex-1" role="status" aria-live="polite">
        <strong className="block text-sm font-semibold text-ink">{finished.failed ? '部分任务未完成' : finished.cancelled ? '已停止' : '已完成'}</strong>
        <p className="mb-0 mt-1 text-xs leading-relaxed text-muted">{finished.failed || finished.cancelled
          ? `本次 ${finished.completed}/${finished.total} 个任务完成${finished.failed ? `，${finished.failed} 个失败` : ''}${finished.cancelled ? `，${finished.cancelled} 个已停止` : ''}。`
          : `本次操作已完成${finished.total>1 ? `，共 ${finished.total} 个任务` : ''}。`}</p>
        {!!finished.failed && <details className="mt-2 text-xs text-danger-soft-ink"><summary className="cursor-pointer">查看错误</summary>{finished.failures.map(task => <p key={task.id} className="mb-0 mt-1 break-words whitespace-pre-wrap">{task.image_id ? pageDetails.get(task.image_id)?.filename || '漫画页面' : '任务'}：{task.error_message || '处理失败，请核对页面状态。'}</p>)}</details>}
      </div>
      <button type="button" className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-muted outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label="关闭本次操作提示" title="关闭提示" onClick={() => activity.dismissCompletion(finished.id)}><X size={14}/></button>
    </div>}
  </aside>
})

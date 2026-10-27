import {useEffect, useRef, useState, type ReactNode} from 'react'
import {Download, LoaderCircle, Square, Type, X} from 'lucide-react'
import {useGlobalDialog} from './GlobalDialog'
import {cancelFileJob, dismissFileJobs, downloadFileJob, fileJobTerminal, type FileJob} from '../services/fileJobs'
import {COMPLETION_NOTICE_MS} from '../features/editor/lib/taskActivity'
import {t} from '../i18n'
import {cn, compactIconButtonClass} from '../ui'

function ActivityRow({label, filename, progress, status, detail, action, children}: {
  label: string, filename: string, progress: number | null, status: string, detail?: string, action?: ReactNode, children?: ReactNode,
}) {
  const percent = Math.round(Math.max(0, Math.min(1, progress !== null && Number.isFinite(progress) ? progress : 0)) * 100)
  return <li className="grid grid-cols-[7px_minmax(0,1fr)_var(--icon-button-compact-size)] items-start gap-x-2.5 border-b border-line-subtle px-3.5 py-3 last:border-b-0"
    aria-label={`${filename} · ${label}`}>
    <span aria-hidden="true" className={cn('mt-[7px] size-[7px] rounded-full',
      status === 'FAILED' ? 'bg-danger' : status === 'COMPLETED' ? 'bg-success' : status === 'CANCELLED' ? 'bg-muted' : 'bg-accent')}/>
    <div className="col-start-2 row-start-1 min-w-0">
      <div className="flex min-w-0 items-center justify-between gap-2"><strong className="min-w-0 truncate text-sm font-semibold text-ink" title={label}>{label}</strong>{progress !== null && <span className="shrink-0 text-xs tabular-nums text-ink">{percent}%</span>}</div>
      {!!filename && <p className="mb-0 mt-1 truncate text-[13px] leading-[18px] text-muted" title={filename}>{filename}</p>}
      {!!detail && <p className="mb-0 mt-1 truncate text-[13px] leading-[18px] text-muted" role="status" title={detail}>{detail}</p>}
    </div>
    {action && <div className="col-start-3 row-start-1">{action}</div>}
    <div className="col-span-2 col-start-2 row-start-2 mt-2.5 h-1 min-w-0 overflow-hidden rounded-full bg-raised" role="progressbar"
      aria-label={t('{{v0}}任务进度', {v0: filename || label})} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress === null ? undefined : percent}>
      <div className={cn('motion-progress-fill h-full rounded-full bg-accent', progress === null && 'animate-pulse motion-reduce:animate-none')} style={{transform: `scaleX(${progress === null ? .35 : percent / 100})`}}/>
    </div>
    {children && <div className="col-span-2 col-start-2 row-start-3 min-w-0">{children}</div>}
  </li>
}

function RequiredFontsButton({fonts}: {fonts: string[]}) {
  const {openDialog} = useGlobalDialog()
  return <button type="button" className={compactIconButtonClass} title={t('所需字体')} aria-label={t('所需字体')}
    onClick={() => openDialog({title: t('所需字体'), content: <ul className="m-0 space-y-2 p-5 pl-10 text-sm">{fonts.map(font => <li key={font}>{font}</li>)}</ul>})}>
    <Type size={14}/>
  </button>
}

export function UploadActivityRow({upload}: {upload: {filename: string, progress: number}}) {
  return <ActivityRow label={t('上传文件')} filename={upload.filename} progress={upload.progress} status="RUNNING"/>
}

export function LocalActivityRow({label, progress, detail = '', onCancel}: {
  label: string, progress: number | null, detail?: string, onCancel?: () => void,
}) {
  return <ActivityRow label={label} filename={detail} progress={progress} status="RUNNING"
    action={onCancel && <button type="button" className={compactIconButtonClass}
      aria-label={t('停止 {{v0}} 的任务', {v0: label})} title={t('停止当前任务，在安全节点生效')} onClick={onCancel}>
      <Square size={11} fill="currentColor"/>
    </button>}/>
}

export function FileActivityRow({job}: {job: FileJob}) {
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState('')
  const requested = useRef(false)
  const terminal = fileJobTerminal(job.status)
  useEffect(() => {
    if (!['COMPLETED', 'CANCELLED'].includes(job.status)) return
    const timer = setTimeout(() => dismissFileJobs([job.id]), COMPLETION_NOTICE_MS)
    return () => clearTimeout(timer)
  }, [job.id, job.status])
  const stop = async () => {
    if (requested.current || job.status === 'CANCELLING') return
    requested.current = true
    setCancelling(true); setError('')
    try {await cancelFileJob(job.id)}
    catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
    finally {requested.current = false; setCancelling(false)}
  }
  const kind = ({import:t('导入漫画'), cbz:t('导出 CBZ'), psd:t('导出可编辑 PSD'),
    project:t('导出源项目'), translated:t('导出翻译后图片'), clean:t('导出净图')} as Record<string, string>)[job.kind] || t('导出文件')
  const status = ({QUEUED:t('排队中'), RUNNING:t('处理中'), CANCELLING:t('正在停止'),
    COMPLETED:t('已完成'), CANCELLED:t('已取消'), FAILED:t('文件任务失败')} as Record<string, string>)[job.status] || job.status
  const filename = job.filename || kind
  const stopping = cancelling || job.status === 'CANCELLING'
  const progressDetail = job.result.progress
  const phase = progressDetail && ({snapshot:t('准备导出快照'), fonts:t('准备字体'), rendering:t('生成译图'), packaging:t('打包文件')} as Record<string, string>)[progressDetail.stage]
  const detail = !terminal && phase ? `${phase}${progressDetail?.total ? ` · ${progressDetail.completed || 0}/${progressDetail.total}` : ''}` : undefined
  return <ActivityRow label={`${kind} · ${status}`} filename={filename} progress={job.status === 'COMPLETED' ? 1 : job.progress} status={job.status}
    detail={detail}
    action={job.status === 'COMPLETED' && job.download_url
      ? <button type="button" className={compactIconButtonClass} aria-label={t('下载')} title={t('下载')} onClick={() => downloadFileJob(job)}><Download size={14}/></button>
      : terminal ? <button type="button" className={compactIconButtonClass} aria-label={t('关闭')} onClick={() => dismissFileJobs([job.id])}><X size={14}/></button>
      : <button type="button" className={compactIconButtonClass} aria-label={t('停止 {{v0}} 的任务', {v0: filename})}
        title={t('停止当前任务，在安全节点生效')} disabled={stopping} aria-busy={stopping || undefined} onClick={() => void stop()}>
        {stopping ? <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none"/> : <Square size={11} fill="currentColor"/>}
      </button>}>
    {(job.download_url && !!job.result.fonts?.length || job.status === 'FAILED' && !!job.message || !!error) && <>
    {job.download_url && !!job.result.fonts?.length && <div className="mt-2 flex items-center gap-1">
      <RequiredFontsButton fonts={job.result.fonts}/>
    </div>}
    {job.status === 'FAILED' && job.message && <details className="mt-2 text-[13px] text-danger-soft-ink"><summary className="cursor-pointer">{t('查看错误')}</summary><p className="mb-0 mt-1 break-words whitespace-pre-wrap">{job.message}</p></details>}
    {error && <p className="mb-0 mt-2 break-words text-[13px] text-danger-soft-ink" role="alert">{t('停止失败：')}{error}</p>}
    </>}
  </ActivityRow>
}

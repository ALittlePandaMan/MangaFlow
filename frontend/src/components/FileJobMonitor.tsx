import {useEffect, useRef} from 'react'
import {useGlobalAlert} from './GlobalAlert'
import {downloadFileJob, fileJobTerminal, isMcpFileJob, refreshFileJobs, useFileJobs} from '../services/fileJobs'
import {t} from '../i18n'

/** One route-independent monitor. Progress is rendered by the shared activity panel. */
export function FileJobMonitor() {
  const errors = useFileJobs(state => state.errors)
  const {alert} = useGlobalAlert()
  const seen = useRef(new Map<string, string>())
  const mountedAt = useRef(Date.now())
  useEffect(() => {
    let active = true
    let polling = false
    let refreshRequested = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      if (polling) {refreshRequested = true; return}
      polling = true
      try {
        const current = await refreshFileJobs()
        if (!active) return
        for (const job of current) {
          const previous = seen.current.get(job.id)
          if (isMcpFileJob(job)) {
            // Keep project data in sync without announcing/downloading Agent work.
            if (job.kind === 'import' && job.status === 'COMPLETED' && previous !== job.status
              && (previous || job.created_at && Date.parse(job.created_at) >= mountedAt.current)) {
              window.dispatchEvent(new CustomEvent('mangaflow:imported', {detail:job.project_id}))
            }
            seen.current.set(job.id,job.status)
            continue
          }
          if (previous !== job.status && fileJobTerminal(job.status)
            && (previous && !fileJobTerminal(previous) || useFileJobs.getState().visibleJobIds.includes(job.id))) {
            if (job.status === 'COMPLETED') {
              alert({message: job.kind === 'import' ? t('漫画导入完成') : t('导出文件已准备好'), tone: 'success'})
              if (job.kind === 'import') window.dispatchEvent(new CustomEvent('mangaflow:imported', {detail: job.project_id}))
              else downloadFileJob(job)
            } else if (job.status === 'FAILED') alert({message: job.message || t('文件任务失败'), tone: 'danger'})
          }
          seen.current.set(job.id, job.status)
        }
      } catch { /* Retry monitoring without blocking editing or resubmitting work. */ }
      const hasActive = useFileJobs.getState().jobs.some(job => !fileJobTerminal(job.status))
      polling = false
      if (active) timer = setTimeout(() => void poll(), refreshRequested ? 0 : hasActive ? 500 : 2000)
      refreshRequested = false
    }
    const unsubscribe = useFileJobs.subscribe((state, previous) => {
      if (state.jobs === previous.jobs) return
      const known = new Set(previous.jobs.map(job => job.id))
      if (state.jobs.some(job => !known.has(job.id)) && !polling) {
        clearTimeout(timer)
        void poll()
      }
    })
    void poll()
    return () => {active = false; unsubscribe(); clearTimeout(timer)}
  }, [alert])
  useEffect(() => {
    if (!errors.length) return
    for (const message of errors) alert({message, tone: 'danger'})
    useFileJobs.setState({errors: []})
  }, [errors, alert])
  return null
}

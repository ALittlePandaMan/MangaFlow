import {create} from 'zustand'

export interface FileJob {
  id: string
  project_id: string
  kind: string
  status: string
  progress: number
  filename: string
  message: string
  created_at?: string
  source?: 'web' | 'mcp'
  download_url: string | null
  result: {page_count?: number, fonts?: string[], submitted_via?: string, workflow_run_id?: string, import_operation_id?: string, progress?: {stage: string, completed?: number, total?: number}}
}
export const isMcpFileJob = (job: FileJob) => job.source === 'mcp' || job.result.submitted_via === 'mcp' || Boolean(job.result.workflow_run_id || job.result.import_operation_id)
export const fileJobTerminal = (status: string) => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(status)
export const useFileJobs = create<{
  jobs: FileJob[], visibleJobIds: string[], uploads: Record<string, {filename: string, progress: number}>, errors: string[]
}>(() => ({jobs: [], visibleJobIds: [], uploads: {}, errors: []}))

export function dismissFileJobs(ids: string[]) {
  const hidden = new Set(ids)
  useFileJobs.setState(state => ({visibleJobIds: state.visibleJobIds.filter(id => !hidden.has(id))}))
}

let mutationRevision = 0
const jobRevisions = new Map<string, number>()
let refreshSequence = 0
let appliedRefresh = 0
const monitorStartedAt = Date.now()

function receiveFileJobs(incoming: FileJob[], observedRevision: number) {
  useFileJobs.setState(state => {
    const previous = new Map(state.jobs.map(job => [job.id, job]))
    const jobs = incoming.map(job => {
      const local = previous.get(job.id)
      // A poll started before submission/cancellation must not undo that update.
      if (local && ((jobRevisions.get(job.id) || 0) > observedRevision
        || fileJobTerminal(local.status) && !fileJobTerminal(job.status)
        || local.status === 'CANCELLING' && !fileJobTerminal(job.status))) return local
      return job
    })
    const receivedIds = new Set(jobs.map(job => job.id))
    for (const job of state.jobs) {
      if (!receivedIds.has(job.id) && (jobRevisions.get(job.id) || 0) > observedRevision) jobs.unshift(job)
    }
    const visible = new Set(state.visibleJobIds)
    for (const job of jobs) {
      if (isMcpFileJob(job)) {visible.delete(job.id);continue}
      const newExternalCompletion = !previous.has(job.id) && job.created_at && Date.parse(job.created_at) >= monitorStartedAt
      if (!fileJobTerminal(job.status) || newExternalCompletion || previous.has(job.id) && !fileJobTerminal(previous.get(job.id)!.status)) visible.add(job.id)
    }
    const current = new Set(jobs.map(job => job.id))
    for (const id of jobRevisions.keys()) if (!current.has(id)) jobRevisions.delete(id)
    return {jobs, visibleJobIds: [...visible].filter(id => current.has(id))}
  })
}

function receiveSubmittedJob(job: FileJob) {
  jobRevisions.set(job.id, ++mutationRevision)
  useFileJobs.setState(state => ({jobs: [job, ...state.jobs.filter(item => item.id !== job.id)],
    visibleJobIds: [...new Set([...state.visibleJobIds, job.id])]}))
}

async function responseValue(response: Response) {
  const value = await response.json()
  if (!response.ok) throw new Error(value.detail || response.statusText)
  return value
}
export async function refreshFileJobs() {
  const sequence = ++refreshSequence
  const observedRevision = mutationRevision
  const before = useFileJobs.getState()
  const visible = new Set(before.visibleJobIds)
  const tracked = before.jobs.filter(job => !fileJobTerminal(job.status) || visible.has(job.id)).map(job => job.id)
  const jobs: FileJob[] = await responseValue(await fetch('/api/file-jobs'))
  // The server returns all live work, but a completion may already be outside
  // its bounded history window. Recover tracked rows in bounded batches so we
  // still publish their final status/download instead of dropping the notice.
  const received = new Set(jobs.map(job => job.id))
  const missing = tracked.filter(id => !received.has(id))
  for (let offset = 0; offset < missing.length; offset += 400) {
    const recovered: FileJob[] = await responseValue(await fetch('/api/file-jobs/status', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(missing.slice(offset,offset + 400)),
    }))
    jobs.push(...recovered)
  }
  if (sequence > appliedRefresh) {
    appliedRefresh = sequence
    receiveFileJobs(jobs, observedRevision)
  }
  return useFileJobs.getState().jobs
}
export async function queueExport(projectId: string, kind: string) {
  const job: FileJob = await responseValue(await fetch(`/api/projects/${projectId}/exports`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind}),
  }))
  receiveSubmittedJob(job)
  return job
}
export async function cancelFileJob(id: string) {
  const job: FileJob = await responseValue(await fetch(`/api/file-jobs/${id}/cancel`, {method: 'POST'}))
  receiveSubmittedJob(job)
}
export function downloadFileJob(job: FileJob) {
  if (!job.download_url) return
  const link = document.createElement('a')
  link.href = job.download_url
  link.download = ''
  document.body.appendChild(link)
  link.click()
  link.remove()
}

// This queue outlives route components; navigation never aborts an upload.
let uploads = Promise.resolve()
export function uploadContainers(projectId: string, files: File[]) {
  for (const file of files) {
    const id = crypto.randomUUID()
    useFileJobs.setState(state => ({uploads: {...state.uploads, [id]: {filename: file.name, progress: 0}}}))
    uploads = uploads.then(() => new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api/projects/${projectId}/imports`)
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) useFileJobs.setState(state => ({uploads: {
          ...state.uploads, [id]: {filename: file.name, progress: event.loaded / event.total},
        }}))
      }
      xhr.onerror = () => reject(new Error(file.name + ': ' + xhr.statusText))
      xhr.onload = () => {
        try {
          const job = JSON.parse(xhr.responseText)
          if (xhr.status < 200 || xhr.status >= 300) throw new Error(job.detail || xhr.statusText)
          receiveSubmittedJob(job)
          resolve()
        } catch (error) {reject(error)}
      }
      const form = new FormData()
      form.append('file', file)
      xhr.send(form)
    })).catch(error => {
      useFileJobs.setState(state => ({errors: [...state.errors, String(error)]}))
    }).finally(() => {
      useFileJobs.setState(state => {
        const remaining = {...state.uploads}; delete remaining[id]
        return {uploads: remaining}
      })
    })
  }
}

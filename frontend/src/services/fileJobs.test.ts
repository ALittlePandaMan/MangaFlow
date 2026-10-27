import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {cancelFileJob, dismissFileJobs, queueExport, refreshFileJobs, useFileJobs, type FileJob} from './fileJobs'

const job = (status: string, id = 'export'): FileJob => ({id, project_id:'project', kind:'cbz', status,
  progress:.5, filename:'comic.cbz', message:'', download_url:null, result:{}})
const response = (value: unknown) => new Response(JSON.stringify(value), {status:200})
const deferred = () => {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>(done => {resolve = done})
  return {promise, resolve}
}

beforeEach(() => useFileJobs.setState({jobs:[], visibleJobIds:[], uploads:{}, errors:[]}))
afterEach(() => vi.unstubAllGlobals())

it('shows active jobs and their completion, but not old completed history or dismissed notices', async () => {
  const fetcher = vi.fn().mockResolvedValue(response([job('COMPLETED','old'), job('RUNNING')]))
  vi.stubGlobal('fetch', fetcher)
  await refreshFileJobs()
  expect(useFileJobs.getState().visibleJobIds).toEqual(['export'])
  fetcher.mockResolvedValue(response([job('COMPLETED','old'), job('COMPLETED')]))
  await refreshFileJobs()
  expect(useFileJobs.getState().visibleJobIds).toEqual(['export'])
  dismissFileJobs(['export'])
  fetcher.mockResolvedValue(response([job('COMPLETED','old'), job('COMPLETED')]))
  await refreshFileJobs()
  expect(useFileJobs.getState().visibleJobIds).toEqual([])
})

it('keeps a newly submitted task when an earlier in-flight list response arrives without it', async () => {
  const older = deferred()
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(older.promise).mockResolvedValueOnce(response(job('COMPLETED'))))
  const poll = refreshFileJobs()
  await queueExport('project', 'cbz')
  older.resolve(response([]))
  expect(await poll).toEqual([job('COMPLETED')])
  expect(useFileJobs.getState().visibleJobIds).toEqual(['export'])
})

it('ignores an older response arriving after a newer refresh', async () => {
  const older = deferred()
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(older.promise).mockResolvedValueOnce(response([job('COMPLETED')])) )
  const poll = refreshFileJobs()
  await refreshFileJobs()
  older.resolve(response([job('RUNNING')]))
  await poll
  expect(useFileJobs.getState().jobs[0].status).toBe('COMPLETED')
})

it('publishes cancellation immediately and never regresses it to running from a stale poll', async () => {
  useFileJobs.setState({jobs:[job('RUNNING')], visibleJobIds:['export']})
  const older = deferred()
  const fetcher = vi.fn().mockReturnValueOnce(older.promise).mockResolvedValueOnce(response(job('CANCELLING')))
  vi.stubGlobal('fetch', fetcher)
  const poll = refreshFileJobs()
  await cancelFileJob('export')
  expect(fetcher).toHaveBeenLastCalledWith('/api/file-jobs/export/cancel', {method:'POST'})
  expect(useFileJobs.getState().jobs[0].status).toBe('CANCELLING')
  older.resolve(response([job('RUNNING')]))
  await poll
  expect(useFileJobs.getState().jobs[0].status).toBe('CANCELLING')
})

it('removes deleted jobs on a fresh poll instead of retaining phantom progress', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(job('QUEUED'))).mockImplementation(async()=>response([])))
  await queueExport('project','cbz')
  await refreshFileJobs()
  expect(useFileJobs.getState().jobs).toEqual([])
  expect(useFileJobs.getState().visibleJobIds).toEqual([])
})

it('keeps every live file task when more than 100 tasks are queued', async () => {
  const jobs = [job('RUNNING','oldest'), ...Array.from({length:120}, (_, index) => job('QUEUED',`queued-${index}`))]
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(jobs)))
  await refreshFileJobs()
  expect(useFileJobs.getState().jobs).toHaveLength(121)
  expect(useFileJobs.getState().visibleJobIds).toContain('oldest')
})

it('recovers a tracked completion outside recent history instead of dropping its download', async () => {
  useFileJobs.setState({jobs:[job('RUNNING','oldest')],visibleJobIds:['oldest']})
  const finished = {...job('COMPLETED','oldest'),download_url:'/api/file-jobs/oldest/download'}
  const fetcher = vi.fn().mockResolvedValueOnce(response(Array.from({length:100}, (_, index) => job('COMPLETED',`new-${index}`))))
    .mockResolvedValueOnce(response([finished]))
  vi.stubGlobal('fetch',fetcher)
  await refreshFileJobs()
  expect(fetcher).toHaveBeenLastCalledWith('/api/file-jobs/status', {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(['oldest']),
  })
  expect(useFileJobs.getState().jobs).toContainEqual(finished)
  expect(useFileJobs.getState().visibleJobIds).toEqual(['oldest'])
})

it('recovers missing tracked statuses in bounded batches and retains the last snapshot if recovery fails', async () => {
  const jobs = Array.from({length:805}, (_, index) => job('RUNNING',String(index)))
  useFileJobs.setState({jobs,visibleJobIds:jobs.map(value => value.id)})
  const fetcher = vi.fn().mockImplementation(async (path:string, options?:RequestInit) => {
    if (path === '/api/file-jobs') return response([])
    const ids:string[] = JSON.parse(options!.body as string)
    expect(ids.length).toBeLessThanOrEqual(400)
    return response(ids.map(id => job('COMPLETED',id)))
  })
  vi.stubGlobal('fetch',fetcher)
  await refreshFileJobs()
  expect(fetcher).toHaveBeenCalledTimes(4)
  expect(useFileJobs.getState().jobs).toHaveLength(805)
  const previous = useFileJobs.getState().jobs
  fetcher.mockResolvedValueOnce(response([])).mockRejectedValueOnce(new Error('connection interrupted'))
  await expect(refreshFileJobs()).rejects.toThrow('connection interrupted')
  expect(useFileJobs.getState().jobs).toBe(previous)
})

it('does not undo a cancellation while recovering an older tracked status', async () => {
  useFileJobs.setState({jobs:[job('RUNNING')],visibleJobIds:['export']})
  const older = deferred()
  const fetcher = vi.fn().mockResolvedValueOnce(response([])).mockReturnValueOnce(older.promise)
    .mockResolvedValueOnce(response(job('CANCELLING')))
  vi.stubGlobal('fetch',fetcher)
  const poll = refreshFileJobs()
  await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2))
  await cancelFileJob('export')
  older.resolve(response([job('RUNNING')]))
  await poll
  expect(useFileJobs.getState().jobs[0].status).toBe('CANCELLING')
})

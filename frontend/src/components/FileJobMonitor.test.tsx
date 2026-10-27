import {act, render, screen} from '@testing-library/react'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {FileJobMonitor} from './FileJobMonitor'
import {GlobalAlertProvider} from './GlobalAlert'
import {queueExport, useFileJobs, type FileJob} from '../services/fileJobs'

const job = (patch: Partial<FileJob> = {}): FileJob => ({id:'file', project_id:'project', kind:'cbz',
  status:'COMPLETED', progress:1, filename:'comic.cbz', message:'', download_url:'/api/file-jobs/file/download', result:{}, ...patch})
const response = (value: unknown) => new Response(JSON.stringify(value), {status:200})
const mount = () => render(<GlobalAlertProvider><FileJobMonitor/><button>继续编辑</button></GlobalAlertProvider>)

beforeEach(() => {
  vi.useFakeTimers()
  useFileJobs.setState({jobs:[], visibleJobIds:[], uploads:{}, errors:[]})
})
afterEach(() => {vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('silently adopts MCP imports and never downloads or announces Agent exports', async () => {
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
  const imported=vi.fn()
  window.addEventListener('mangaflow:imported',imported)
  const remote=job({source:'mcp',status:'RUNNING',kind:'import'})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response([remote]))
    .mockResolvedValueOnce(response([{...remote,status:'COMPLETED'},job({id:'export',source:'mcp',created_at:new Date().toISOString()})])))
  try {
    await act(async () => {mount()})
    expect(useFileJobs.getState().visibleJobIds).toEqual([])
    await act(async () => {await vi.advanceTimersByTimeAsync(500)})
    expect(imported).toHaveBeenCalledOnce()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(click).not.toHaveBeenCalled()
    expect(useFileJobs.getState().visibleJobIds).toEqual([])
  } finally {window.removeEventListener('mangaflow:imported',imported)}
})

it('does not announce or redownload historical completions on startup or later polls', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const fetcher = vi.fn().mockResolvedValueOnce(response([job()])).mockResolvedValueOnce(response([job(),job({id:'old'})]))
  vi.stubGlobal('fetch',fetcher)
  await act(async () => {mount()})
  await act(async () => {await vi.advanceTimersByTimeAsync(2000)})
  expect(click).not.toHaveBeenCalled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('announces and directly downloads a newly submitted job even when it finishes before the first poll', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(job())).mockResolvedValueOnce(response([job()])).mockResolvedValueOnce(response([job()])))
  await queueExport('project','cbz')
  await act(async () => {mount()})
  expect(screen.getByRole('status')).toHaveTextContent('导出文件已准备好')
  expect(click).toHaveBeenCalledOnce()
  await act(async () => {await vi.advanceTimersByTimeAsync(2000)})
  expect(click).toHaveBeenCalledOnce()
  expect(screen.getByRole('button',{name:'继续编辑'})).toBeEnabled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('notifies import completion exactly once and requests a page-list refresh without a second progress UI', async () => {
  const imported = vi.fn()
  window.addEventListener('mangaflow:imported',imported)
  try {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response([job({kind:'import',status:'RUNNING'})]))
      .mockResolvedValueOnce(response([job({kind:'import'})])).mockResolvedValueOnce(response([job({kind:'import'})])))
    await act(async () => {mount()})
    await act(async () => {await vi.advanceTimersByTimeAsync(4000)})
    expect(imported).toHaveBeenCalledOnce()
    expect((imported.mock.calls[0][0] as CustomEvent).detail).toBe('project')
    expect(screen.getByRole('status')).toHaveTextContent('漫画导入完成')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.queryByText('文件任务')).not.toBeInTheDocument()
  } finally {window.removeEventListener('mangaflow:imported',imported)}
})

it('announces an MCP import that finishes between polls without announcing historical imports', async () => {
  const imported=vi.fn()
  window.addEventListener('mangaflow:imported',imported)
  const fast=job({kind:'import',download_url:null,created_at:new Date(Date.now()+100).toISOString()})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response([]))
    .mockResolvedValueOnce(response([fast,job({id:'historical',kind:'import',created_at:'2020-01-01T00:00:00Z'})])))
  try {
    await act(async () => {mount()})
    await act(async () => {await vi.advanceTimersByTimeAsync(2000)})
    expect(imported).toHaveBeenCalledOnce()
    expect(useFileJobs.getState().visibleJobIds).toEqual(['file'])
    expect(screen.getByRole('status')).toHaveTextContent('漫画导入完成')
  } finally {window.removeEventListener('mangaflow:imported',imported)}
})

it('polls every half second while exporting and returns to the idle interval after completion', async () => {
  const fetcher = vi.fn().mockImplementation(async () => response([job({status:'RUNNING', progress:.1})]))
  vi.stubGlobal('fetch',fetcher)
  await act(async () => {mount()})
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => {await vi.advanceTimersByTimeAsync(499)})
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => {await vi.advanceTimersByTimeAsync(1)})
  expect(fetcher).toHaveBeenCalledTimes(2)
  fetcher.mockImplementation(async () => response([job({status:'CANCELLED'})]))
  await act(async () => {await vi.advanceTimersByTimeAsync(500)})
  expect(fetcher).toHaveBeenCalledTimes(3)
  await act(async () => {await vi.advanceTimersByTimeAsync(1999)})
  expect(fetcher).toHaveBeenCalledTimes(3)
  await act(async () => {await vi.advanceTimersByTimeAsync(1)})
  expect(fetcher).toHaveBeenCalledTimes(4)
})

it('refreshes immediately when an export is submitted during the idle interval', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response([])).mockResolvedValueOnce(response(job({status:'QUEUED'})))
    .mockResolvedValueOnce(response([job({status:'RUNNING', progress:.2})]))
  vi.stubGlobal('fetch',fetcher)
  await act(async () => {mount()})
  await act(async () => {await queueExport('project','cbz')})
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(useFileJobs.getState().jobs[0].progress).toBe(.2)
})

it('announces and downloads a tracked completion missing from recent history exactly once', async () => {
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
  useFileJobs.setState({jobs:[job({status:'RUNNING'})],visibleJobIds:['file']})
  const history=Array.from({length:100},(_,index)=>job({id:`recent-${index}`}))
  const fetcher=vi.fn().mockImplementation(async(path:string)=>response(path === '/api/file-jobs/status' ? [job()] : history))
  vi.stubGlobal('fetch',fetcher)
  await act(async()=>{mount()})
  expect(screen.getByRole('status')).toHaveTextContent('导出文件已准备好')
  expect(click).toHaveBeenCalledOnce()
  expect(useFileJobs.getState().jobs.find(item=>item.id === 'file')?.status).toBe('COMPLETED')
  await act(async()=>{await vi.advanceTimersByTimeAsync(2000)})
  expect(click).toHaveBeenCalledOnce()
})

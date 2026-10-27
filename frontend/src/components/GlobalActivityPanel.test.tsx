import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {StrictMode} from 'react'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {GlobalActivityPanel, GlobalProcessingMonitor} from './GlobalActivityPanel'
import {EditorActivitySource} from './EditorActivitySource'
import {GlobalDialogProvider} from './GlobalDialog'
import {CanvasTaskList} from '../features/editor/components/CanvasTaskList'
import {COMPLETION_NOTICE_MS, TaskActivity} from '../features/editor/lib/taskActivity'
import {taskFixture} from '../features/editor/lib/taskActivity.testUtils'
import {useActivityPanelSource} from '../features/editor/lib/activityPanelStore'
import {useFileJobs, type FileJob} from '../services/fileJobs'

const job = (patch: Partial<FileJob> = {}): FileJob => ({id:'file', project_id:'project', kind:'import',
  status:'RUNNING', progress:.4, filename:'comic.cbz', message:'', download_url:null, result:{}, ...patch})

beforeEach(() => {
  useFileJobs.setState({jobs:[], visibleJobIds:[], uploads:{}, errors:[]})
  useActivityPanelSource.setState({source:null})
})
afterEach(() => {vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('does not display Agent processing, file exports or uploads as web activity', () => {
  const activity=new TaskActivity('project')
  activity.update([taskFixture('remote',{status:'RUNNING',payload:{options:{submitted_via:'mcp'}}})])
  useFileJobs.setState({jobs:[job({id:'remote-export',source:'mcp'})],visibleJobIds:['remote-export'],
    uploads:{'mcp:remote':{filename:'agent.png',progress:.5}}})
  render(<CanvasTaskList activity={activity} pages={[]}/>)
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(activity.getPageTask('page-remote')?.id).toBe('remote')
})

it('does not discover MCP uploads or notify Agent task completions in the global web monitor', async () => {
  vi.useFakeTimers()
  const running=taskFixture('remote',{status:'RUNNING',payload:{options:{submitted_via:'mcp'}}})
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([running])))
    .mockResolvedValueOnce(new Response(JSON.stringify([{...running,status:'COMPLETED'}])))
  vi.stubGlobal('fetch',fetcher)
  await act(async () => {render(<><GlobalProcessingMonitor/><GlobalActivityPanel/></>)})
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  await act(async () => {await vi.advanceTimersByTimeAsync(1500)})
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(fetcher.mock.calls.every(([path]) => String(path).startsWith('/api/tasks?'))).toBe(true)
})

it('puts recognition, upload, import, export and local submission progress in one existing activity list', () => {
  const activity = new TaskActivity('project')
  activity.begin([taskFixture('ocr')])
  useFileJobs.setState({jobs:[job(), job({id:'psd', kind:'psd', filename:'editable.psd', progress:.7})],
    uploads:{upload:{filename:'book.pdf', progress:.25}}})
  render(<CanvasTaskList activity={activity} pages={[]} batch={{label:'批量检测并识别', progress:.5}}/>)
  expect(screen.getAllByRole('complementary')).toHaveLength(1)
  expect(screen.getAllByRole('heading', {name:'活动'})).toHaveLength(1)
  const list = screen.getByRole('list')
  expect(within(list).getAllByRole('listitem')).toHaveLength(5)
  expect(within(list).getAllByRole('progressbar')).toHaveLength(5)
  expect(within(list).getByRole('progressbar', {name:'book.pdf任务进度'})).toHaveAttribute('aria-valuenow','25')
  expect(within(list).getByRole('progressbar', {name:'comic.cbz任务进度'})).toHaveAttribute('aria-valuenow','40')
  expect(within(list).getByRole('progressbar', {name:'editable.psd任务进度'})).toHaveAttribute('aria-valuenow','70')
  expect(within(list).getByRole('progressbar', {name:'批量检测并识别任务进度'})).toHaveAttribute('aria-valuenow','50')
  expect(screen.queryByText('文件任务')).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('uses the editor corner and falls back to the global top-right position after leaving the editor', () => {
  const activity = new TaskActivity('project')
  activity.begin([taskFixture('ocr')])
  useFileJobs.setState({jobs:[job()]})
  const {rerender} = render(<StrictMode><main data-testid="canvas" className="relative"><EditorActivitySource activity={activity} pages={[]}/></main><GlobalActivityPanel/></StrictMode>)
  expect(screen.getAllByRole('complementary')).toHaveLength(1)
  expect(screen.getByTestId('canvas')).toContainElement(screen.getByRole('complementary'))
  expect(screen.getAllByRole('progressbar')).toHaveLength(2)
  rerender(<StrictMode><main>设置</main><GlobalActivityPanel/></StrictMode>)
  const panel = screen.getByRole('complementary')
  expect(screen.getAllByRole('complementary')).toHaveLength(1)
  expect(panel.closest('.global-activity-panel')).not.toBeNull()
  expect(screen.getAllByRole('progressbar')).toHaveLength(1)
  expect(panel).toHaveTextContent('comic.cbz')
})

it('stops only the file task clicked, without sending duplicate requests or cancelling recognition', async () => {
  const activity = new TaskActivity('project'), cancelRecognition = vi.fn()
  activity.begin([taskFixture('ocr')])
  useFileJobs.setState({jobs:[job()]})
  let release!: (response: Response) => void
  const fetcher = vi.fn(() => new Promise<Response>(done => {release = done}))
  vi.stubGlobal('fetch', fetcher)
  render(<CanvasTaskList activity={activity} pages={[]} onCancelTask={cancelRecognition}/>)
  const stop = screen.getByRole('button', {name:'停止 comic.cbz 的任务'})
  fireEvent.click(stop);fireEvent.click(stop)
  expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/file-jobs/file/cancel', {method:'POST'})
  expect(stop).toBeDisabled()
  await act(async () => release(new Response(JSON.stringify(job({status:'CANCELLING'})))))
  expect(screen.getByText('导入漫画 · 正在停止')).toBeInTheDocument()
  expect(cancelRecognition).not.toHaveBeenCalled()
})

it('preserves direct download and required font actions in the shared list', () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  useFileJobs.setState({jobs:[job({status:'COMPLETED', kind:'psd', download_url:'/api/file-jobs/file/download', result:{fonts:['Noto Sans']}})], visibleJobIds:['file']})
  render(<GlobalDialogProvider><GlobalActivityPanel/></GlobalDialogProvider>)
  fireEvent.click(screen.getByRole('button', {name:'下载'}))
  expect(click).toHaveBeenCalledOnce()
  expect(click.mock.instances[0]).toHaveAttribute('href','/api/file-jobs/file/download')
  fireEvent.click(screen.getByRole('button', {name:'所需字体'}))
  expect(screen.getByRole('dialog')).toHaveTextContent('Noto Sans')
})

it('replaces the completed export row close button with a single download button above the progress track', () => {
  useFileJobs.setState({jobs:[job({status:'COMPLETED', kind:'translated', download_url:'/api/file-jobs/file/download'})], visibleJobIds:['file']})
  render(<GlobalActivityPanel/>)
  const row = screen.getByRole('listitem')
  const button = within(row).getByRole('button', {name:'下载'})
  expect(button.parentElement).toHaveClass('col-start-3','row-start-1')
  expect(within(row).getAllByRole('button')).toHaveLength(1)
  expect(within(row).queryByRole('button',{name:'关闭'})).not.toBeInTheDocument()
  expect(row.querySelector('.row-start-3')).toBeNull()
  expect(screen.getByRole('button',{name:'关闭本次操作提示'})).toBeInTheDocument()
})

it('shows the current export phase and page count while retaining the real numeric progress', () => {
  useFileJobs.setState({jobs:[job({kind:'translated', result:{progress:{stage:'packaging', completed:4, total:10}}, progress:.86})]})
  render(<GlobalActivityPanel/>)
  expect(screen.getByRole('status')).toHaveTextContent('打包文件 · 4/10')
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','86')
})

it('dismisses successful file rows after 15 seconds but retains failures for inspection', () => {
  vi.useFakeTimers()
  useFileJobs.setState({jobs:[job({status:'COMPLETED'}), job({id:'failed', status:'FAILED', filename:'bad.pdf', message:'损坏文件'})], visibleJobIds:['file','failed']})
  render(<GlobalActivityPanel/>)
  act(() => vi.advanceTimersByTime(COMPLETION_NOTICE_MS))
  expect(screen.queryByText('comic.cbz')).not.toBeInTheDocument()
  expect(screen.getByText('bad.pdf')).toBeInTheDocument()
  fireEvent.click(screen.getByText('查看错误'))
  expect(screen.getByText('损坏文件')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'关闭本次操作提示'}))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('shows and cancels ordinary image uploads in the same activity list without a modal', () => {
  const cancel = vi.fn()
  render(<CanvasTaskList activity={new TaskActivity('project')} pages={[]} batch={{label:'正在导入图片', progress:.6, onCancel:cancel}}/>)
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','60')
  fireEvent.click(screen.getByRole('button', {name:'停止 正在导入图片 的任务'}))
  expect(cancel).toHaveBeenCalledOnce()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('does not invent a percentage while a local submission has no measurable progress', () => {
  render(<CanvasTaskList activity={new TaskActivity('project')} pages={[]} batch={{label:'正在导出', progress:null}}/>)
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
})

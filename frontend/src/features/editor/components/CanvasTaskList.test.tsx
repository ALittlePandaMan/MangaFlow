import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import type {ImagePage} from '../../../types'
import {COMPLETION_NOTICE_MS, TaskActivity} from '../lib/taskActivity'
import {taskFixture} from '../lib/taskActivity.testUtils'
import {CanvasTaskList} from './CanvasTaskList'

afterEach(()=>{vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
const pages=[{id:'page-0',filename:'00000003.jpg',order_index:2} as ImagePage]

it('keeps all current jobs in the scrollable list without a batch-summary footer',()=>{
  const activity=new TaskActivity('project')
  activity.begin(Array.from({length:6},(_,i)=>taskFixture(`${i}`,{status:i===0?'RUNNING':'QUEUED',current_stage:'inpainting',progress:.5,payload:{provider:'openai-compatible'}})))
  render(<CanvasTaskList activity={activity} pages={pages} onCancelTask={vi.fn()}/>)
  const panel=screen.getByRole('complementary',{name:'任务进度'})
  expect(within(panel).getByRole('heading',{name:'活动'})).toBeInTheDocument()
  expect(within(panel).getAllByRole('listitem')).toHaveLength(6)
  expect(screen.getByText('第 3 页：00000003.jpg')).toBeInTheDocument()
  expect(within(panel).getAllByText('openai-compatible')).toHaveLength(6)
  expect(screen.getByRole('progressbar',{name:'00000003.jpg任务进度'})).toHaveAttribute('aria-valuenow','50')
  expect(screen.getByRole('button',{name:'停止 00000003.jpg 的任务'})).toHaveClass('icon-button','icon-button-compact','icon-button-ghost')
  expect(panel).toHaveTextContent('修复')
  expect(within(panel).getByRole('list',{name:'本次操作的活动任务'})).toHaveClass('canvas-task-items')
  expect(panel.querySelector('footer')).toBeNull()
  expect(panel).not.toHaveTextContent('本次完成')
  expect(panel).not.toHaveTextContent(/另有.*任务/)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:/折叠|清除已结束/})).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'关闭本次操作提示'})).not.toBeInTheDocument()
})

it.each([true,false])('extends the progress track below the stop-button column with provider=%s',provider=>{
  const activity=new TaskActivity('project'),cancel=vi.fn()
  activity.begin([taskFixture('0',{status:'RUNNING',progress:.6,payload:provider ? {provider:'openai-compatible'} : {}})])
  const {rerender}=render(<CanvasTaskList activity={activity} pages={pages} onCancelTask={cancel}/>)
  const row=screen.getByRole('listitem'),track=screen.getByRole('progressbar')
  const stop=screen.getByRole('button',{name:'停止 00000003.jpg 的任务'})
  expect(track.parentElement).toBe(row)
  expect(track).toHaveClass('col-start-2','col-span-2','row-start-2')
  expect(stop).toHaveClass('col-start-3','row-start-1')
  expect(track).toHaveAttribute('aria-valuenow','60')
  expect(track.firstElementChild).toHaveStyle({width:'60%'})
  expect(screen.getByText('第 3 页：00000003.jpg').parentElement).toHaveClass('col-start-2','row-start-1')
  rerender(<CanvasTaskList activity={activity} pages={pages}/>)
  expect(screen.queryByRole('button',{name:/停止.*任务/})).not.toBeInTheDocument()
  expect(screen.getByRole('progressbar')).toHaveClass('col-start-2','col-span-2','row-start-2')
})

it('sizes the scroll viewport to five measured rows and follows provider/error row height changes',()=>{
  let rowHeight=82, resize!:() => void
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement) {
    return new DOMRect(0,0,288,this.tagName==='LI' ? rowHeight : 0)
  })
  const disconnect=vi.fn()
  vi.stubGlobal('ResizeObserver',class {
    constructor(callback:() => void) {resize=callback}
    observe() {}
    disconnect() {disconnect()}
  })
  const activity=new TaskActivity('project')
  activity.begin(Array.from({length:8},(_,i) => taskFixture(`${i}`)))
  const {unmount}=render(<CanvasTaskList activity={activity} pages={pages}/>)
  const list=screen.getByRole('list',{name:'本次操作的活动任务'})
  expect(list).toHaveStyle({maxHeight:'410px'})
  expect(within(list).getAllByRole('listitem')).toHaveLength(8)
  rowHeight=100
  act(() => resize())
  expect(list).toHaveStyle({maxHeight:'500px'})
  act(() => activity.update(Array.from({length:5},(_,i) => taskFixture(`${i}`,{status:'COMPLETED'}))))
  expect(list).toHaveStyle({maxHeight:'300px'})
  unmount()
  expect(disconnect).toHaveBeenCalled()
})

it('preserves native scrolling during progress updates, isolates the wheel from the canvas and resets new operations',()=>{
  const activity=new TaskActivity('project'),canvasWheel=vi.fn()
  activity.begin(Array.from({length:8},(_,i) => taskFixture(`${i}`)))
  render(<div onWheel={canvasWheel}><CanvasTaskList activity={activity} pages={pages}/></div>)
  const list=screen.getByRole('list',{name:'本次操作的活动任务'})
  expect(list).toHaveAttribute('tabindex','0')
  list.scrollTop=240
  const wheel=new WheelEvent('wheel',{deltaY:100,bubbles:true,cancelable:true})
  fireEvent(list,wheel)
  expect(canvasWheel).not.toHaveBeenCalled()
  expect(wheel.defaultPrevented).toBe(false)
  act(() => activity.update([taskFixture('7',{status:'RUNNING',progress:.5})]))
  expect(screen.getByRole('list',{name:'本次操作的活动任务'})).toBe(list)
  expect(list.scrollTop).toBe(240)
  act(() => activity.begin([taskFixture('new')]))
  const next=screen.getByRole('list',{name:'本次操作的活动任务'})
  expect(next).not.toBe(list)
  expect(next.scrollTop).toBe(0)
})

it('can stop a task beyond the first five and waits for it before reporting completion',async()=>{
  const activity=new TaskActivity('project'),cancel=vi.fn(async()=>{})
  const jobs=Array.from({length:7},(_,i) => taskFixture(`${i}`))
  activity.begin(jobs)
  render(<CanvasTaskList activity={activity} pages={jobs.map(task => ({id:task.image_id,filename:`page-${task.id}.png`,order_index:Number(task.id)} as ImagePage))} onCancelTask={cancel}/>)
  screen.getByRole('list').scrollTop=500
  const stop=screen.getByRole('button',{name:'停止 page-0.png 的任务'})
  await act(async()=>fireEvent.click(stop))
  expect(cancel).toHaveBeenCalledExactlyOnceWith('0')
  act(()=>activity.update(jobs.slice(1).map(task => ({...task,status:'COMPLETED'}))))
  expect(screen.queryByText('已完成')).not.toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(1)
  act(()=>activity.update([{...jobs[0],status:'CANCELLED'}]))
  expect(screen.getByText('已停止')).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})

it('does not display previously completed or failed recognition history',()=>{
  const activity=new TaskActivity('project')
  activity.update([taskFixture('0',{status:'COMPLETED'}),taskFixture('1',{status:'FAILED'})])
  render(<CanvasTaskList activity={activity} pages={pages}/>)
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('shows completion without old task rows and automatically closes it after 15 seconds',()=>{
  vi.useFakeTimers()
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0')])
  render(<CanvasTaskList activity={activity} pages={pages}/>)
  act(()=>activity.update([taskFixture('0',{status:'COMPLETED',progress:1})]))
  expect(screen.getByText('已完成')).toBeInTheDocument()
  expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  act(()=>vi.advanceTimersByTime(COMPLETION_NOTICE_MS-1))
  expect(screen.getByText('已完成')).toBeInTheDocument()
  act(()=>vi.advanceTimersByTime(1))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('manually closes completion without cancelling or resurrecting a finished task',()=>{
  const activity=new TaskActivity('project'),cancel=vi.fn()
  const done=taskFixture('0',{status:'COMPLETED'})
  activity.begin([done])
  render(<CanvasTaskList activity={activity} pages={pages} onCancelTask={cancel}/>)
  const heading=screen.getByRole('heading',{name:'活动'})
  const header=heading.closest('header')!
  expect(heading).toHaveClass('text-base')
  expect(header).toHaveClass('justify-between')
  const close=within(header).getByRole('button',{name:'关闭本次操作提示'})
  expect(screen.getAllByRole('button',{name:'关闭本次操作提示'})).toHaveLength(1)
  expect(screen.getByRole('status')).not.toContainElement(close)
  fireEvent.click(close)
  act(()=>activity.update([{...done}]))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  expect(cancel).not.toHaveBeenCalled()
})

it('does not let an older completion timer close a newer operation',()=>{
  vi.useFakeTimers()
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'COMPLETED'})])
  render(<CanvasTaskList activity={activity} pages={pages}/>)
  act(()=>vi.advanceTimersByTime(10_000))
  act(()=>activity.begin([taskFixture('1')]))
  act(()=>activity.update([taskFixture('1',{status:'COMPLETED'})]))
  act(()=>vi.advanceTimersByTime(5_000))
  expect(screen.getByText('已完成')).toBeInTheDocument()
  act(()=>vi.advanceTimersByTime(10_000))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('waits for final page synchronization before starting the completion timeout',()=>{
  vi.useFakeTimers()
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'COMPLETED'})])
  const {rerender}=render(<CanvasTaskList activity={activity} pages={pages} pendingLabel="正在同步任务结果…"/>)
  act(()=>vi.advanceTimersByTime(30_000))
  expect(screen.queryByText('已完成')).not.toBeInTheDocument()
  expect(screen.getByText('正在同步任务结果…')).toBeInTheDocument()
  rerender(<CanvasTaskList activity={activity} pages={pages}/>)
  expect(screen.getByText('已完成')).toBeInTheDocument()
  act(()=>vi.advanceTimersByTime(COMPLETION_NOTICE_MS))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('keeps errors available, never reports partial failure as success and permits manual dismissal',()=>{
  vi.useFakeTimers()
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'FAILED',error_message:'GPU unavailable'}),taskFixture('1',{status:'COMPLETED'})])
  render(<CanvasTaskList activity={activity} pages={pages}/>)
  expect(screen.getByText('部分任务未完成')).toBeInTheDocument()
  expect(screen.getByText('本次 1/2 个任务完成，1 个失败。')).toBeInTheDocument()
  act(()=>vi.advanceTimersByTime(60_000))
  fireEvent.click(screen.getByText('查看错误'))
  expect(screen.getByText('00000003.jpg：GPU unavailable')).toBeVisible()
  expect(screen.queryByRole('button',{name:'重试'})).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'关闭本次操作提示'}))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('stops only the clicked task and prevents duplicate stop requests',async()=>{
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'RUNNING'})])
  let release!:()=>void
  const cancel=vi.fn(()=>new Promise<void>(resolve=>{release=resolve}))
  render(<CanvasTaskList activity={activity} pages={pages} onCancelTask={cancel}/>)
  const stop=screen.getByRole('button',{name:'停止 00000003.jpg 的任务'})
  fireEvent.click(stop);fireEvent.click(stop)
  expect(cancel).toHaveBeenCalledExactlyOnceWith('0')
  expect(stop).toBeDisabled()
  await act(async()=>release())
  act(()=>activity.update([taskFixture('0',{status:'CANCELLED'})]))
  expect(screen.getByText('已停止')).toBeInTheDocument()
  expect(screen.queryByText('已完成')).not.toBeInTheDocument()
})

it('shows a stop failure and allows an explicit retry without hiding the running task',async()=>{
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'RUNNING'})])
  const cancel=vi.fn(async()=>{throw new Error('停止请求失败')})
  render(<CanvasTaskList activity={activity} pages={pages} onCancelTask={cancel}/>)
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'停止 00000003.jpg 的任务'})))
  expect(screen.getByRole('alert')).toHaveTextContent('停止请求失败')
  expect(screen.getByRole('button',{name:'停止 00000003.jpg 的任务'})).toBeEnabled()
  expect(activity.getSnapshot().active).toBe(1)
})

it('still displays automatic rendering and preserves accessible selected-region progress',()=>{
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'RUNNING',current_stage:'rendering',progress:.4})])
  render(<CanvasTaskList activity={activity} pages={pages} pendingLabel="正在排版…" scope={{kind:'regions',filename:'00000003.jpg',regionKeys:['R001','R002']}}/>)
  expect(screen.getByRole('status',{name:/^区域处理：R001、R002/})).toHaveTextContent('区域处理 · 2 个')
  expect(screen.getByRole('complementary')).toHaveTextContent('正在生成译文排版…')
  expect(screen.queryByRole('button',{name:/重新排版/})).not.toBeInTheDocument()
})

it.each([[NaN,0],[Infinity,0],[-.5,0],[1.5,100]])('keeps progress %s inside the accessible range', (progress,expected)=>{
  const activity=new TaskActivity('project')
  activity.begin([taskFixture('0',{status:'RUNNING',progress})])
  render(<CanvasTaskList activity={activity} pages={pages}/>)
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow',String(expected))
})

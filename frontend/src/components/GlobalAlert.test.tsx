import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {StrictMode, useState} from 'react'
import {Link, MemoryRouter, Route, Routes} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalAlertProvider, useGlobalAlert} from './GlobalAlert'
import {GlobalDialogProvider, useGlobalDialog} from './GlobalDialog'

function Harness() {
  const {alert} = useGlobalAlert()
  const dialog = useGlobalDialog()
  const [clicks, setClicks] = useState(0)
  return <>
    <input aria-label="继续编辑"/>
    <button onClick={() => alert({title:'保存成功',message:'配置已保存',tone:'success'})}>保存</button>
    <button onClick={() => alert({title:'测试失败',message:'无法连接服务',tone:'danger'})}>失败</button>
    <button onClick={() => {for (const message of ['甲','乙','丙','丁']) alert(message)}}>多条提示</button>
    <button onClick={() => setClicks(value => value+1)}>继续操作{clicks}</button>
    <button onClick={() => void dialog.confirm({title:'安装推荐配置',message:'是否安装？'})}>确认安装</button>
    <span data-testid="modal-open">{String(dialog.isOpen)}</span>
  </>
}

function open() {
  return render(<StrictMode><GlobalDialogProvider><Harness/></GlobalDialogProvider></StrictMode>)
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('global non-modal alerts', () => {
  it('appears at the top center without a mask, focus change, or modal state and expires after 3 seconds', () => {
    open()
    const input=screen.getByLabelText('继续编辑')
    input.focus()
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    const toast=screen.getByRole('status',{name:'保存成功'})
    expect(toast).toHaveTextContent('配置已保存')
    expect(toast).toHaveClass('py-1', 'items-center')
    const statusIcon = toast.querySelector(':scope > span[aria-hidden="true"]')!
    expect(statusIcon).toHaveClass('inline-flex', 'h-8', 'items-center', 'justify-center', 'self-center', 'leading-none')
    expect(statusIcon.querySelector('svg')).toHaveClass('block', 'shrink-0')
    expect(toast).not.toHaveAttribute('aria-modal')
    expect(toast.closest('.global-alerts')).toHaveClass('fixed','inset-x-0','items-center','pointer-events-none')
    expect(toast.closest('.global-alerts')).toHaveStyle({top:'76px'})
    expect(document.querySelector('[data-dialog-overlay]')).toBeNull()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('modal-open')).toHaveTextContent('false')
    expect(input).toHaveFocus()
    expect(screen.queryByText('保存成功')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'继续操作0'}))
    fireEvent.change(input,{target:{value:'继续输入'}})
    expect(screen.getByRole('button',{name:'继续操作1'})).toBeVisible()
    expect(input).toHaveValue('继续输入')
    act(() => vi.advanceTimersByTime(2999))
    expect(toast).toBeVisible()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.queryByRole('status',{name:'保存成功'})).not.toBeInTheDocument()
  })

  it('keeps errors visible longer, but still dismisses them automatically', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'失败'}))
    const toast=screen.getByRole('alert',{name:'测试失败'})
    act(() => vi.advanceTimersByTime(5999))
    expect(toast).toBeVisible()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not expire unread alerts while the browser tab is hidden', () => {
    const visibility=vi.spyOn(document,'visibilityState','get').mockReturnValue('visible')
    open()
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    visibility.mockReturnValue('hidden')
    fireEvent(document,new Event('visibilitychange'))
    act(() => vi.advanceTimersByTime(60000))
    expect(screen.getByText('配置已保存')).toBeInTheDocument()
    visibility.mockReturnValue('visible')
    fireEvent(document,new Event('visibilitychange'))
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.queryByText('配置已保存')).not.toBeInTheDocument()
    visibility.mockRestore()
  })

  it('keeps stacked alerts scrollable within the viewport below the navigation', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'多条提示'}))
    const stack=document.querySelector('.global-alerts')!
    expect(stack).toHaveClass('overflow-y-auto','overscroll-contain')
    expect(stack).toHaveStyle({maxHeight:'calc(100dvh - 88px)'})
    for (const card of stack.children) expect(card).toHaveClass('shrink-0')
  })

  it('merges repeated messages and refreshes their expiry without stale timers', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    act(() => vi.advanceTimersByTime(2500))
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    expect(screen.getAllByRole('status',{name:'保存成功'})).toHaveLength(1)
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.getByText('配置已保存')).toBeVisible()
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.queryByText('配置已保存')).not.toBeInTheDocument()
  })

  it('caps visible messages at three and cleans up all timers when unmounted', () => {
    const {unmount}=open()
    fireEvent.click(screen.getByRole('button',{name:'多条提示'}))
    expect(screen.queryByText('甲')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.global-alert')).toHaveLength(3)
    expect(screen.getByText('丁')).toBeVisible()
    unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(document.querySelector('.global-alerts')).toBeNull()
  })

  it('pauses while hovered so a message does not disappear while being read', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    const toast=screen.getByRole('status',{name:'保存成功'})
    fireEvent.mouseEnter(toast)
    act(() => vi.advanceTimersByTime(10000))
    expect(toast).toBeVisible()
    fireEvent.mouseLeave(toast)
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.queryByText('配置已保存')).not.toBeInTheDocument()
  })

  it('pauses on keyboard focus and supports manual dismissal', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    const toast=screen.getByRole('status',{name:'保存成功'})
    const close=within(toast).getByRole('button',{name:'关闭提示'})
    act(() => close.focus())
    act(() => vi.advanceTimersByTime(10000))
    expect(toast).toBeVisible()
    fireEvent.click(close)
    expect(screen.queryByText('配置已保存')).not.toBeInTheDocument()
  })

  it('does not queue behind or close a real confirmation dialog', () => {
    open()
    fireEvent.click(screen.getByRole('button',{name:'确认安装'}))
    act(() => vi.advanceTimersByTime(20))
    const dialog=screen.getByRole('dialog',{name:'安装推荐配置'})
    const focus=document.activeElement
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    expect(screen.getByRole('status',{name:'保存成功'})).toBeVisible()
    expect(document.activeElement).toBe(focus)
    expect(screen.getByTestId('modal-open')).toHaveTextContent('true')
    act(() => vi.advanceTimersByTime(3000))
    expect(dialog).toBeVisible()
    expect(screen.queryByText('配置已保存')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button',{name:'取消'}))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('ignores empty messages and provides alerts independently from modal dialogs', () => {
    function Standalone() {
      const {alert}=useGlobalAlert()
      return <button onClick={() => alert('   ')}>空消息</button>
    }
    render(<GlobalAlertProvider><Standalone/></GlobalAlertProvider>)
    fireEvent.click(screen.getByRole('button',{name:'空消息'}))
    expect(document.querySelector('.global-alerts')).toBeNull()
  })

  it('uses the same global notification across route changes rather than mounting one in each page', () => {
    function Page({name}: {name:string}) {
      const {alert}=useGlobalAlert()
      return <><button onClick={() => alert(name+'已保存')}>保存{name}</button><Link to={name === '设置' ? '/editor' : '/settings'}>切换页面</Link></>
    }
    render(<GlobalAlertProvider><MemoryRouter initialEntries={['/settings']}><Routes>
      <Route path="/settings" element={<Page name="设置"/>}/>
      <Route path="/editor" element={<Page name="编辑器"/>}/>
    </Routes></MemoryRouter></GlobalAlertProvider>)
    fireEvent.click(screen.getByRole('button',{name:'保存设置'}))
    fireEvent.click(screen.getByRole('link',{name:'切换页面'}))
    expect(screen.getByText('设置已保存')).toBeVisible()
    fireEvent.click(screen.getByRole('button',{name:'保存编辑器'}))
    expect(screen.getByText('编辑器已保存')).toBeVisible()
    expect(document.querySelectorAll('.global-alerts')).toHaveLength(1)
    act(() => vi.advanceTimersByTime(3000))
    expect(document.querySelector('.global-alerts')).toBeNull()
  })

  it('exposes scoped dismissal so progress can be replaced without clearing another page notification', () => {
    function Page() {
      const {alert,dismiss}=useGlobalAlert()
      const [progress,setProgress]=useState<number|null>(null)
      return <>
        <button onClick={() => setProgress(alert('OCR正在识别'))}>开始识别</button>
        <button onClick={() => {if (progress !== null) dismiss(progress); alert('OCR已完成')}}>完成识别</button>
        <button onClick={() => alert('设置已保存')}>保存设置</button>
      </>
    }
    render(<GlobalAlertProvider><Page/></GlobalAlertProvider>)
    fireEvent.click(screen.getByRole('button',{name:'开始识别'}))
    fireEvent.click(screen.getByRole('button',{name:'保存设置'}))
    fireEvent.click(screen.getByRole('button',{name:'完成识别'}))
    expect(screen.queryByText('OCR正在识别')).not.toBeInTheDocument()
    expect(screen.getByText('OCR已完成')).toBeVisible()
    expect(screen.getByText('设置已保存')).toBeVisible()
  })

  it('anchors 12px below the main navigation regardless of editor toolbar wrapping', () => {
    const {unmount}=render(<GlobalDialogProvider><header className="app-header"/><div className="editor-toolbar"/><Harness/></GlobalDialogProvider>)
    const header=document.querySelector('.app-header')!
    const toolbar=document.querySelector('.editor-toolbar')!
    const headerRect=vi.spyOn(header,'getBoundingClientRect').mockReturnValue(new DOMRect(0,0,1000,64))
    const rect=vi.spyOn(toolbar,'getBoundingClientRect').mockReturnValue(new DOMRect(0,64,1000,48))
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    expect(document.querySelector('.global-alerts')).toHaveStyle({top:'76px'})
    rect.mockReturnValue(new DOMRect(0,64,320,92))
    fireEvent(window,new Event('resize'))
    expect(document.querySelector('.global-alerts')).toHaveStyle({top:'76px'})
    headerRect.mockReturnValue(new DOMRect(0,0,320,80))
    fireEvent(window,new Event('resize'))
    expect(document.querySelector('.global-alerts')).toHaveStyle({top:'92px'})
    unmount()
    vi.restoreAllMocks()
  })

  it('keeps the same vertical placement on pages without an editor toolbar', () => {
    const {unmount}=render(<GlobalDialogProvider><header className="app-header"/><Harness/></GlobalDialogProvider>)
    vi.spyOn(document.querySelector('.app-header')!,'getBoundingClientRect').mockReturnValue(new DOMRect(0,0,1000,80))
    fireEvent.click(screen.getByRole('button',{name:'保存'}))
    expect(document.querySelector('.global-alerts')).toHaveStyle({top:'92px'})
    unmount()
    vi.restoreAllMocks()
  })
})

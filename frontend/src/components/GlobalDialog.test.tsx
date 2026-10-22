import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {StrictMode, useState} from 'react'
import {describe, expect, it, vi} from 'vitest'

import {GlobalDialogProvider, useGlobalDialog} from './GlobalDialog'
import {ColorControl, SelectControl} from './FormControls'

function DialogHarness() {
  const dialog = useGlobalDialog()
  const [result, setResult] = useState('等待操作')
  const openConfirmation = async () => {
    const accepted = await dialog.confirm({title:'删除区域？', message:'此操作无法撤销。', confirmLabel:'删除区域'})
    setResult(accepted ? '已确认' : '已取消')
  }
  return <>
    <button onClick={() => void openConfirmation()}>打开确认</button>
    <output>{result}</output>
  </>
}

describe('GlobalDialogProvider', () => {
  it('resolves a confirmation after the user accepts it', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><DialogHarness/></GlobalDialogProvider>)

    await user.click(screen.getByRole('button', {name:'打开确认'}))
    expect(screen.getByRole('dialog', {name:'删除区域？'})).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'关闭'})).toHaveClass('icon-button','icon-button-ghost')
    expect(screen.getByText('此操作无法撤销。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', {name:'删除区域'}))
    await waitFor(() => expect(screen.getByText('已确认')).toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('resolves a confirmation as cancelled when Escape is pressed', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><DialogHarness/></GlobalDialogProvider>)

    await user.click(screen.getByRole('button', {name:'打开确认'}))
    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.getByText('已取消')).toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('gives long confirmation content its own scroll area without shrinking the actions', async () => {
    render(<GlobalDialogProvider><DialogHarness/></GlobalDialogProvider>)
    await userEvent.click(screen.getByRole('button',{name:'打开确认'}))
    const dialog=screen.getByRole('dialog')
    expect(dialog).toHaveClass('flex','flex-col','min-h-0')
    expect(screen.getByText('此操作无法撤销。')).toHaveClass('min-h-0','flex-1','overflow-auto','[overflow-wrap:anywhere]')
    expect(screen.getByRole('button',{name:'删除区域'}).parentElement).toHaveClass('shrink-0','flex-wrap')
    expect(document.getElementById('global-dialog-title')?.parentElement?.parentElement).toHaveClass('shrink-0','grid-cols-[44px_minmax(0,1fr)]')
  })

  it('isolates the background, traps focus and restores the trigger and scroll state after the queue drains', async () => {
    const user=userEvent.setup()
    function Queue() {
      const {confirm}=useGlobalDialog()
      return <button onClick={() => {void confirm('第一个'); void confirm('第二个')}}>打开队列</button>
    }
    const {container}=render(<GlobalDialogProvider><Queue/></GlobalDialogProvider>)
    const trigger=screen.getByRole('button',{name:'打开队列'})
    await user.click(trigger)
    expect(container).toHaveAttribute('inert')
    expect(document.body.style.overflow).toBe('hidden')
    expect(screen.getByRole('button',{name:'取消'})).toHaveFocus()
    screen.getByRole('button',{name:'确认'}).focus()
    await user.tab()
    expect(screen.getByRole('button',{name:'关闭'})).toHaveFocus()
    await user.tab({shift:true})
    expect(screen.getByRole('button',{name:'确认'})).toHaveFocus()
    trigger.focus()
    expect(screen.getByRole('button',{name:'取消'})).toHaveFocus()
    await user.click(screen.getByRole('button',{name:'确认'}))
    expect(screen.getByText('第二个')).toBeInTheDocument()
    expect(container).toHaveAttribute('inert')
    await user.keyboard('{Escape}')
    expect(container).not.toHaveAttribute('inert')
    expect(document.body.style.overflow).toBe('')
    expect(trigger).toHaveFocus()
  })

  it('cancels a queued content dialog without closing the active one or reviving the cancelled dialog', async () => {
    let api!: ReturnType<typeof useGlobalDialog>
    function Harness() {api=useGlobalDialog(); return null}
    render(<GlobalDialogProvider><Harness/></GlobalDialogProvider>)
    let cancelQueued!: () => void
    let first!: Promise<boolean>
    act(() => {
      first=api.confirm('先确认')
      cancelQueued=api.openDialog({title:'已过期的内容',content:<p>不能再出现</p>})
      cancelQueued()
    })
    expect(screen.getByText('先确认')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'确认'}))
    await expect(first).resolves.toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    act(() => {api.openDialog({title:'新的内容',content:<p>保留</p>}); cancelQueued()})
    expect(screen.getByRole('dialog',{name:'新的内容'})).toBeInTheDocument()
  })

  it('settles every pending confirmation on unmount and ignores late requests', async () => {
    let api!: ReturnType<typeof useGlobalDialog>
    function Harness() {api=useGlobalDialog(); return null}
    const {unmount}=render(<StrictMode><GlobalDialogProvider><Harness/></GlobalDialogProvider></StrictMode>)
    let requests!: Promise<boolean>[]
    act(() => {requests=[api.confirm('一'),api.confirm('二')]})
    unmount()
    await expect(Promise.all(requests)).resolves.toEqual([false,false])
    await expect(api.confirm('已离开')).resolves.toBe(false)
    expect(document.querySelector('[data-dialog-overlay]')).toBeNull()
  })

  it('focuses an empty non-dismissible dialog and blocks Escape, backdrop and Tab from escaping', async () => {
    function Harness() {
      const {openDialog}=useGlobalDialog()
      return <button onClick={() => openDialog({title:'正在安装',dismissible:false,content:<p>等待服务</p>})}>打开</button>
    }
    render(<GlobalDialogProvider><Harness/></GlobalDialogProvider>)
    await userEvent.click(screen.getByRole('button',{name:'打开'}))
    const dialog=screen.getByRole('dialog',{name:'正在安装'})
    expect(dialog).toHaveFocus()
    await userEvent.keyboard('{Tab}{Escape}')
    fireEvent.mouseDown(dialog.parentElement!)
    expect(dialog).toHaveFocus()
    expect(dialog).toBeInTheDocument()
  })

  it('keeps portalled select and colour controls interactive inside a content dialog', async () => {
    const changed=vi.fn()
    function Content() {
      return <><SelectControl ariaLabel="语言" value="ja" options={[{value:'ja',label:'日语'},{value:'en',label:'英语'}]} onChange={changed}/><ColorControl ariaLabel="颜色" value="#111111" onChange={changed}/></>
    }
    function Harness() {
      const {openDialog}=useGlobalDialog()
      return <button onClick={() => openDialog({title:'样式',content:<Content/>})}>打开</button>
    }
    const user=userEvent.setup()
    render(<GlobalDialogProvider><Harness/></GlobalDialogProvider>)
    await user.click(screen.getByRole('button',{name:'打开'}))
    const dialog=screen.getByRole('dialog',{name:'样式'})
    await user.click(within(dialog).getByRole('button',{name:'语言'}))
    expect(screen.getByRole('listbox')).toHaveAttribute('data-modal-owner',dialog.id)
    expect(screen.getByRole('listbox')).not.toHaveAttribute('inert')
    await user.click(screen.getByRole('option',{name:'英语'}))
    expect(changed).toHaveBeenCalledWith('en')
    await user.click(within(dialog).getByRole('button',{name:/颜色/}))
    const popup=screen.getByRole('dialog',{name:'颜色'})
    const input=within(popup).getByRole('textbox')
    await user.click(input)
    expect(input).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(popup).not.toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button',{name:'语言'}))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
  })
})

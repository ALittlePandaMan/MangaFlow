import {fireEvent, render as renderView, screen, waitFor, within} from '@testing-library/react'
import type {ReactElement} from 'react'
import {GlobalDialogProvider} from './GlobalDialog'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {DEFAULT_SHORTCUTS, useShortcutStore} from '../features/shortcuts/store'
import {ShortcutSettings} from './ShortcutSettings'

const render=(element:ReactElement)=>renderView(element,{wrapper:GlobalDialogProvider})

beforeEach(() => useShortcutStore.getState().resetShortcuts())

describe('grouped shortcut settings', () => {
  it('retains drafts between groups, saves, and leaves the form usable',async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<ShortcutSettings onSaved={onSaved}/>)
    const record = screen.getByRole('button',{name:'设置拖拽画布快捷键'})
    await user.click(record)
    fireEvent.keyDown(record,{code:'KeyG',key:'g'})
    await user.click(screen.getByRole('button',{name:/页面与流程/}))
    expect(screen.getByRole('button',{name:'设置当前页重新检测快捷键'})).toBeVisible()
    await user.click(screen.getByRole('button',{name:/工具/}))
    expect(screen.getByRole('button',{name:'设置拖拽画布快捷键'})).toHaveTextContent('G')
    await user.click(screen.getByRole('button',{name:'保存快捷键'}))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({...DEFAULT_SHORTCUTS,'tool.pan':'KeyG'}))
    expect(screen.getByRole('button',{name:'恢复默认'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'保存快捷键'})).toBeDisabled()
    const dialog=await screen.findByRole('status',{name:'保存成功'})
    expect(dialog).toHaveTextContent('快捷键设置已保存。')
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.getByRole('button',{name:'恢复默认'})).toBeEnabled()
  })
  it('checks conflicts across hidden groups and permits recovery',async () => {
    const user = userEvent.setup()
    render(<ShortcutSettings onSaved={vi.fn()}/>)
    const record = screen.getByRole('button',{name:'设置拖拽画布快捷键'})
    await user.click(record)
    fireEvent.keyDown(record,{code:'KeyD',key:'d'})
    expect(screen.getByRole('alert')).toHaveTextContent('下一张图片')
    expect(screen.getByRole('button',{name:'保存快捷键'})).toBeDisabled()
    await user.click(screen.getByRole('button',{name:'恢复默认'}))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('keeps unsaved shortcuts after a failure and applies them only when retry succeeds',async () => {
    const onSaved=vi.fn().mockRejectedValueOnce(new Error('无法保存快捷键')).mockResolvedValue(undefined)
    const user=userEvent.setup()
    render(<ShortcutSettings onSaved={onSaved}/>)
    const record=screen.getByRole('button',{name:'设置拖拽画布快捷键'})
    await user.click(record)
    fireEvent.keyDown(record,{code:'KeyG',key:'g'})
    await user.click(screen.getByRole('button',{name:'保存快捷键'}))
    const dialog=await screen.findByRole('alert',{name:'保存失败'})
    expect(dialog).toHaveTextContent('无法保存快捷键')
    expect(document.querySelector('.settings-shortcuts .settings-error')).toBeNull()
    expect(useShortcutStore.getState().shortcuts['tool.pan']).toBe(DEFAULT_SHORTCUTS['tool.pan'])
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(record).toHaveTextContent('G')
    await user.click(screen.getByRole('button',{name:'保存快捷键'}))
    expect(await screen.findByRole('status',{name:'保存成功'})).toBeVisible()
    expect(useShortcutStore.getState().shortcuts['tool.pan']).toBe('KeyG')
    expect(onSaved).toHaveBeenCalledTimes(2)
  })
})

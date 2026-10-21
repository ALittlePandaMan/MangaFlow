import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {DEFAULT_SHORTCUTS, useShortcutStore} from '../features/shortcuts/store'
import {ShortcutSettings} from './ShortcutSettings'

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
})

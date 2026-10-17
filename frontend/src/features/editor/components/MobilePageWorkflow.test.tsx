import {ScanText} from 'lucide-react'
import {act, fireEvent, render, screen, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {EditorToolbar} from './EditorToolbar'
import {MobilePageWorkflow} from './MobilePageWorkflow'

const actions = [
  {stage:'ocr' as const, label:'重新检测', title:'重新检测当前页并识字', icon:ScanText, disabled:false},
  {stage:'inpainting' as const, label:'重新修复', title:'修复并自动排版', icon:ScanText, disabled:false},
  {stage:'translation' as const, label:'重新翻译', title:'使用最新原文重新翻译', icon:ScanText, disabled:false},
]
function props() {
  return {actions, recommended:'translation' as const, disabled:false, running:null, resetting:false, resetDisabled:false, onRun:vi.fn(), onReset:vi.fn()}
}
afterEach(() => vi.unstubAllGlobals())

describe('phone page workflow', () => {
  it('shows four direct actions without a rendering action or an expandable menu', async () => {
    const user = userEvent.setup(), properties = props()
    render(<MobilePageWorkflow {...properties}/>)
    expect(within(screen.getByRole('group', {name:'当前页处理流程'})).getAllByRole('button').map(button => button.textContent)).toEqual(['检测', '修复', '翻译', '重置'])
    expect(screen.queryByRole('button', {name:'重新排版'})).not.toBeInTheDocument()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', {name:'更多当前页处理操作'})).not.toBeInTheDocument()
    for (const action of actions.filter(action => !action.disabled)) {
      await user.click(screen.getByRole('button', {name:action.label}))
      expect(properties.onRun).toHaveBeenLastCalledWith(action.stage)
    }
    expect(properties.onRun).toHaveBeenCalledTimes(3)
  })
  it('supports native keyboard navigation and skips unavailable actions', async () => {
    const user = userEvent.setup(), properties = props()
    render(<MobilePageWorkflow {...properties} actions={actions.map(action => ({...action, disabled:action.stage === 'translation'}))}/>)
    await user.tab()
    expect(screen.getByRole('button', {name:'重新检测'})).toHaveFocus()
    await user.tab()
    await user.keyboard('{Enter}')
    expect(properties.onRun).toHaveBeenCalledExactlyOnceWith('inpainting')
    await user.tab()
    expect(screen.getByRole('button', {name:'重置当前页'})).toHaveFocus()
    expect(screen.getByRole('button', {name:'重新翻译'})).toBeDisabled()
    fireEvent.click(screen.getByRole('button', {name:'重新翻译'}))
    expect(properties.onRun).toHaveBeenCalledOnce()
  })
  it('uses the existing reset callback and respects reset availability', async () => {
    const user = userEvent.setup(), properties = props()
    const {rerender} = render(<MobilePageWorkflow {...properties}/>)
    await user.click(screen.getByRole('button', {name:'重置当前页'}))
    expect(properties.onReset).toHaveBeenCalledOnce()
    rerender(<MobilePageWorkflow {...properties} resetDisabled/>)
    await user.click(screen.getByRole('button', {name:'重置当前页'}))
    expect(properties.onReset).toHaveBeenCalledOnce()
  })
  it('blocks all actions while busy and preserves labels during progress', () => {
    const properties = props()
    const {rerender} = render(<MobilePageWorkflow {...properties} running="ocr"/>)
    expect(screen.getByRole('status', {name:'检测处理中…'})).toBeVisible()
    expect(screen.getByRole('button', {name:'重新检测'})).toHaveAttribute('aria-busy', 'true')
    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(properties.onRun).not.toHaveBeenCalled()
    expect(properties.onReset).not.toHaveBeenCalled()
    rerender(<MobilePageWorkflow {...properties} resetting/>)
    expect(screen.getByRole('status', {name:'恢复中…'})).toBeVisible()
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
    rerender(<MobilePageWorkflow {...properties} disabled/>)
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  })
  it('keeps all completed-page actions available with no suggested selection', () => {
    render(<MobilePageWorkflow {...props()} actions={actions.map(action => ({...action, disabled:false}))} recommended={null}/>)
    expect(screen.getAllByRole('button')).toHaveLength(4)
    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeEnabled()
      expect(button).not.toHaveClass('bg-accent-soft')
    }
  })
  it('changes only below 640px and preserves desktop/tablet actions', () => {
    let width = 390
    const listeners = new Set<() => void>()
    vi.stubGlobal('matchMedia', (query: string) => ({matches:width <= Number(query.match(/\d+/)?.[0]), addEventListener:(_: string, fn: () => void) => listeners.add(fn), removeEventListener:(_: string, fn: () => void) => listeners.delete(fn)}))
    render(<EditorToolbar onUndo={vi.fn()} onRedo={vi.fn()} canUndo={false} canRedo={false} rightActions={<div><button>原有桌面操作</button></div>} mobileActions={<MobilePageWorkflow {...props()}/>}/>)
    expect(within(screen.getByRole('group', {name:'当前页处理流程'})).getAllByRole('button')).toHaveLength(4)
    for (const next of [640, 768, 1440]) {
      act(() => {width = next; listeners.forEach(fn => fn())})
      expect(screen.queryByRole('group', {name:'当前页处理流程'})).not.toBeInTheDocument()
      expect(screen.getByRole('button', {name:'原有桌面操作'})).toBeVisible()
    }
    act(() => {width = 639; listeners.forEach(fn => fn())})
    expect(within(screen.getByRole('group', {name:'当前页处理流程'})).getAllByRole('button')).toHaveLength(4)
  })
})

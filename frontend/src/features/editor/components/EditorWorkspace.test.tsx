import {act, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {EditorWorkspace} from './EditorWorkspace'

function media(initial = true) {
  let compact = initial
  const listeners = new Set<() => void>()
  vi.stubGlobal('matchMedia', () => ({matches: compact, addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn)}))
  return (next: boolean) => act(() => {compact = next; listeners.forEach(fn => fn())})
}
const props = {pages: <aside><button>第一页</button></aside>, canvas: <main>画布内容</main>, properties: <aside><input aria-label="译文草稿" defaultValue="保留的内容"/></aside>, pageId: 'a', selectedCount: 1}

afterEach(() => vi.unstubAllGlobals())
describe('responsive editor panels', () => {
  it('keeps the sidebars visible at 1000px and folds them below 960px', () => {
    let width = 1000
    const listeners = new Set<() => void>()
    vi.stubGlobal('matchMedia', (query: string) => ({matches: width < Number(query.match(/\d+/)?.[0]), addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn)}))
    render(<EditorWorkspace {...props}/>)
    expect(screen.getByRole('button', {name:'第一页'})).toBeVisible()
    expect(screen.getByLabelText('译文草稿')).toBeVisible()
    expect(screen.queryByRole('navigation', {name:'工作台面板'})).not.toBeInTheDocument()
    act(() => {width = 960; listeners.forEach(fn => fn())})
    expect(screen.queryByRole('navigation', {name:'工作台面板'})).not.toBeInTheDocument()
    act(() => {width = 959.5; listeners.forEach(fn => fn())})
    expect(screen.getByRole('navigation', {name:'工作台面板'})).toBeVisible()
    expect(screen.queryByRole('button', {name:'第一页'})).not.toBeInTheDocument()
  })
  it('places page navigation at both ends and disables unavailable directions', () => {
    media()
    const next = vi.fn(), previous = vi.fn()
    const {rerender} = render(<EditorWorkspace {...props} onNextPage={next}/>)
    const dock = screen.getByRole('navigation', {name: '工作台面板'})
    expect(dock.firstElementChild).toBe(screen.getByRole('button', {name: '上一页'}))
    expect(dock.lastElementChild).toBe(screen.getByRole('button', {name: '下一页'}))
    expect(screen.getByRole('button', {name: '上一页'})).toBeDisabled()
    fireEvent.click(screen.getByRole('button', {name: '下一页'}))
    expect(next).toHaveBeenCalledOnce()
    rerender(<EditorWorkspace {...props} onPreviousPage={previous}/>)
    expect(screen.getByRole('button', {name: '下一页'})).toBeDisabled()
    fireEvent.click(screen.getByRole('button', {name: '上一页'}))
    expect(previous).toHaveBeenCalledOnce()
  })
  it('keeps the canvas visible and opens/closes each panel with focus restoration', () => {
    media()
    render(<EditorWorkspace {...props}/>)
    expect(screen.getByText('画布内容')).toBeVisible()
    expect(screen.queryByRole('button', {name: '第一页'})).not.toBeInTheDocument()
    const pages = screen.getByRole('button', {name: '页面'})
    fireEvent.click(pages)
    expect(screen.getByRole('button', {name: '第一页'})).toBeVisible()
    expect(screen.getByRole('button', {name: '收起面板'})).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('button', {name: '收起面板'}), {key: 'Escape'})
    expect(pages).toHaveFocus()
    expect(pages).toHaveAttribute('aria-expanded', 'false')
  })
  it('retains a single editor and its draft while resizing and hides page sheet after switching', () => {
    const resize = media()
    const {rerender} = render(<EditorWorkspace {...props}/>)
    fireEvent.click(screen.getByRole('button', {name: '属性 1'}))
    fireEvent.change(screen.getByLabelText('译文草稿'), {target: {value: '尚未提交的编辑'}})
    resize(false)
    expect(screen.getAllByLabelText('译文草稿')).toHaveLength(1)
    expect(screen.getByLabelText('译文草稿')).toHaveValue('尚未提交的编辑')
    expect(screen.getByRole('button', {name: '第一页'})).toBeVisible()
    resize(true)
    fireEvent.click(screen.getByRole('button', {name: '页面'}))
    rerender(<EditorWorkspace {...props} pageId="b"/>)
    expect(screen.getByRole('button', {name: '页面'})).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', {name: '第一页'})).not.toBeInTheDocument()
  })
  it('reveals an unsaved-mask confirmation instead of covering it with the page sheet', () => {
    media()
    const {rerender} = render(<EditorWorkspace {...props}/>)
    fireEvent.click(screen.getByRole('button', {name:'页面'}))
    expect(screen.getByRole('button', {name:'第一页'})).toBeVisible()
    rerender(<EditorWorkspace {...props} canvasPromptOpen/>)
    expect(screen.queryByRole('button', {name:'第一页'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'页面'})).toHaveAttribute('aria-expanded','false')
  })
})

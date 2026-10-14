import {render, screen} from '@testing-library/react'
import {describe, expect, it} from 'vitest'

import {ActivitySpinner, BlockingLoader, EditorSkeleton, ProgressBar} from './LoadingUI'

describe('LoadingUI', () => {
  it('clamps progress values to the accessible percentage range', () => {
    const {rerender} = render(<ProgressBar label="导出进度" value={1.25}/>)

    expect(screen.getByRole('progressbar', {name:'导出进度'})).toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByText('100%')).toBeInTheDocument()

    rerender(<ProgressBar label="导出进度" value={-1}/>)
    expect(screen.getByRole('progressbar', {name:'导出进度'})).toHaveAttribute('aria-valuenow', '0')
  })

  it('exposes an accessible status label for loading operations', () => {
    render(<ActivitySpinner label="正在重新翻译"/>)

    expect(screen.getByRole('status', {name:'正在重新翻译'})).toBeInTheDocument()
  })

  it('uses the editor sidebar widths while loading', () => {
    render(<EditorSkeleton/>)

    expect(screen.getByLabelText('正在打开工作台')).toHaveClass('grid-cols-[200px_minmax(0,1fr)_320px]')
  })

  it('distinguishes regional progress from a page-wide task', () => {
    const {rerender} = render(<BlockingLoader label="正在重新 OCR 区域 R002…" progress={.4} scope={{kind:'regions',filename:'page.png',regionKeys:['R002']}}/>)
    const regional = screen.getByRole('status',{name:/^区域处理：R002/})
    expect(regional).toHaveClass('items-center','backdrop-blur-[3px]')
    expect(regional).not.toHaveClass('items-end')
    expect(screen.getByText('区域处理 · 1 个')).toBeInTheDocument()
    expect(screen.getByText('page.png')).toBeInTheDocument()
    expect(screen.getByText(/仅处理这些区域，不执行整页重跑/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar',{name:'区域进度'})).toHaveAttribute('aria-valuenow','40')
    rerender(<BlockingLoader label="正在重新 OCR…" progress={.4} scope={{kind:'page',filename:'page.png'}}/>)
    const wholePage = screen.getByRole('status',{name:/^整页处理：page.png/})
    expect(wholePage).toHaveClass('items-center','backdrop-blur-[3px]')
    expect(screen.getByText(/处理范围：本页全部文字区域/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar',{name:'整页进度'})).toHaveAttribute('aria-valuenow','40')
    expect(screen.queryByText('R002')).not.toBeInTheDocument()
  })
})

import {render, screen} from '@testing-library/react'
import {describe, expect, it} from 'vitest'

import {ActivitySpinner, ProgressBar} from './LoadingUI'

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
})

import {render, screen, fireEvent} from '@testing-library/react'
import {expect, it, vi} from 'vitest'
import {ClassificationStatus} from './ClassificationStatus'

it('makes fail-open visible and exposes explicit recovery', () => {
  const retry=vi.fn()
  render(<ClassificationStatus summary={{version:1,status:'unavailable',excluded_count:0}} busy={false} onRetry={retry}/>)
  expect(screen.getByRole('alert')).toHaveTextContent('已保留所有候选')
  fireEvent.click(screen.getByRole('button',{name:'重试分类'}))
  expect(retry).toHaveBeenCalledOnce()
})

it('does not show an error for completed classification', () => {
  render(<ClassificationStatus summary={{version:1,status:'completed',excluded_count:2}} busy={false} onRetry={vi.fn()}/>)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it, vi} from 'vitest'
import {TextClassificationFields} from './TextClassificationFields'

describe('text classification settings', () => {
  it('defaults to filtering and submits adjustable confidence', async () => {
    const submit = vi.fn()
    render(<form onSubmit={event => {event.preventDefault(); submit(new FormData(event.currentTarget))}}><TextClassificationFields config={undefined}/><button>保存</button></form>)
    expect(screen.getByRole('button', {name: '拟声词处理'})).toHaveTextContent('分类并过滤拟声词')
    fireEvent.change(screen.getByLabelText('拟声词过滤置信度'), {target: {value: '0.8'}})
    await userEvent.click(screen.getByRole('button', {name: '保存'}))
    expect(submit.mock.calls[0][0].get('classification_mode')).toBe('filter')
    expect(submit.mock.calls[0][0].get('sfx_confidence')).toBe('0.8')
  })

  it('restores keep mode and allows disabling without a runtime request', async () => {
    render(<TextClassificationFields config={{filter_sfx: false, min_sfx_confidence: 0.75}}/>)
    expect(screen.getByRole('button', {name: '拟声词处理'})).toHaveTextContent('分类但保留拟声词')
    await userEvent.click(screen.getByRole('button', {name: '拟声词处理'}))
    await userEvent.click(screen.getByRole('option', {name: '关闭分类'}))
    expect(screen.getByLabelText('拟声词过滤置信度')).toBeDisabled()
  })

  it('restores legacy boolean opt-out', () => {
    render(<TextClassificationFields config={false}/>)
    expect(screen.getByRole('button', {name: '拟声词处理'})).toHaveTextContent('关闭分类')
  })
})

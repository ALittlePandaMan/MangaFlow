import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it} from 'vitest'
import {OCRConcurrencyFields} from './OCRConcurrencyFields'

describe('OCR concurrency settings', () => {
  it('defaults to adaptive with bounded parallelism and pacing', () => {
    render(<OCRConcurrencyFields defaults={{}}/>)
    expect(screen.getByRole('button', {name:'OCR 并发模式'})).toHaveTextContent('自适应并发')
    expect(screen.getByLabelText('OCR 并发上限')).toHaveValue('8')
    expect(screen.getByLabelText('OCR 每分钟请求上限')).toHaveValue('60')
  })
  it('restores saved values, switches mode, and submits numeric fields', async () => {
    const user = userEvent.setup()
    const {container} = render(<form><OCRConcurrencyFields defaults={{concurrency_mode:'fixed', max_concurrency:4, requests_per_minute:90}}/></form>)
    expect(screen.getByRole('button', {name:'OCR 并发模式'})).toHaveTextContent('固定并发')
    fireEvent.change(screen.getByLabelText('OCR 并发上限'), {target:{value:'6'}})
    expect(new FormData(container.querySelector('form')!).get('max_concurrency')).toBe('6')
    await user.click(screen.getByRole('button', {name:'OCR 并发模式'}))
    await user.click(screen.getByRole('option', {name:'串行识别'}))
    expect(screen.queryByLabelText('OCR 并发上限')).not.toBeInTheDocument()
    expect(new FormData(container.querySelector('form')!).get('concurrency_mode')).toBe('serial')
    expect(screen.getByLabelText('OCR 每分钟请求上限')).toHaveValue('90')
  })
})

import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it, vi} from 'vitest'
import {ApiKeyInput} from './ApiKeyInput'

describe('saved API key input',() => {
  it('shows a mask without putting a fake key into the input or form data',() => {
    render(<form><ApiKeyInput name="api_key" aria-label="API Key" hasSavedKey/></form>)
    const input = screen.getByLabelText<HTMLInputElement>('API Key')
    expect(input).toHaveAttribute('type','password')
    expect(input).toHaveAttribute('placeholder','••••••••')
    expect(input).toHaveAttribute('autocomplete','new-password')
    expect(input).toHaveValue('')
    expect(new FormData(input.form!).get('api_key')).toBe('')
  })

  it('shows an empty field on focus and restores the mask when leaving it empty',async () => {
    const user = userEvent.setup(), onFocus = vi.fn(), onBlur = vi.fn()
    render(<><ApiKeyInput aria-label="API Key" hasSavedKey onFocus={onFocus} onBlur={onBlur}/><button>其他控件</button></>)
    const input = screen.getByLabelText('API Key')
    await user.click(input)
    expect(input).toHaveValue('')
    expect(input).toHaveAttribute('placeholder','')
    await user.tab()
    expect(input).toHaveAttribute('placeholder','••••••••')
    expect(onFocus).toHaveBeenCalledOnce()
    expect(onBlur).toHaveBeenCalledOnce()
  })

  it('keeps a replacement draft when focusing again and submits only what was typed',async () => {
    const user = userEvent.setup(), onChange = vi.fn()
    render(<form><ApiKeyInput name="api_key" aria-label="API Key" hasSavedKey onChange={onChange}/><button type="button">其他控件</button></form>)
    const input = screen.getByLabelText<HTMLInputElement>('API Key')
    await user.type(input,'replacement-test-key')
    await user.tab()
    await user.click(input)
    expect(input).toHaveValue('replacement-test-key')
    expect(new FormData(input.form!).get('api_key')).toBe('replacement-test-key')
    expect(onChange).toHaveBeenCalled()
  })

  it('does not pretend a missing or unusable saved key exists',() => {
    const {rerender} = render(<ApiKeyInput aria-label="API Key" hasSavedKey/>)
    rerender(<ApiKeyInput aria-label="API Key" hasSavedKey={false} required placeholder="输入新接口密钥"/>)
    const input = screen.getByLabelText('API Key')
    expect(input).toHaveAttribute('placeholder','输入新接口密钥')
    expect(input).toBeRequired()
    fireEvent.focus(input)
    expect(input).toHaveAttribute('placeholder','输入新接口密钥')
  })
})

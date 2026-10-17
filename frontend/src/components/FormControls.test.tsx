import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it, vi} from 'vitest'
import {NumberControl} from './FormControls'

describe('NumberControl committed input', () => {
  const renderControl = (props = {}) => {
    const onChange = vi.fn()
    render(<NumberControl ariaLabel="value" value={1.15} min={0.5} max={3} step={0.05} commitOnBlur onChange={onChange} {...props}/>)
    const input = screen.getByRole('textbox', {name: 'value'})
    fireEvent.focus(input)
    return {input, onChange}
  }

  it('keeps incomplete decimals local and commits once on blur', () => {
    const {input, onChange} = renderControl()
    for (const value of ['0', '0.', '0.9']) fireEvent.change(input, {target: {value}})
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.9)
  })

  it('commits Enter once without a duplicate save on blur', () => {
    const {input, onChange} = renderControl()
    fireEvent.change(input, {target: {value: '2.25'}})
    fireEvent.keyDown(input, {key: 'Enter'})
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledExactlyOnceWith(2.25)
  })

  it.each(['', '-', '.'])('discards the invalid draft %s without saving', value => {
    const {input, onChange} = renderControl()
    fireEvent.change(input, {target: {value}})
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
    expect(input).toHaveValue('1.15')
  })

  it('normalizes only the completed value and ignores unchanged values', () => {
    const {input, onChange} = renderControl()
    fireEvent.change(input, {target: {value: '1.150'}})
    fireEvent.keyDown(input, {key: 'Enter'})
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.change(input, {target: {value: '0'}})
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.5)
  })

  it('reports an explicit unchanged commit once without changing normal change semantics', () => {
    const onCommit = vi.fn()
    const {input, onChange} = renderControl({value:0, min:-180, max:180, step:1, onCommit})
    fireEvent.blur(input)
    expect(onCommit).not.toHaveBeenCalled()
    fireEvent.focus(input)
    fireEvent.change(input, {target:{value:'0.0'}})
    fireEvent.keyDown(input, {key:'Enter'})
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(0)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('keeps step buttons immediate without saving again on blur', () => {
    const {input, onChange} = renderControl()
    fireEvent.click(screen.getByRole('button', {name: 'value增大'}))
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledExactlyOnceWith(1.2)
  })

  it('cancels a draft with Escape', () => {
    const {input, onChange} = renderControl()
    fireEvent.change(input, {target: {value: '2'}})
    fireEvent.keyDown(input, {key: 'Escape'})
    fireEvent.blur(input)
    expect(input).toHaveValue('1.15')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('never publishes out-of-range intermediate values even in live mode', () => {
    const {input, onChange} = renderControl({commitOnBlur: false})
    for (const value of ['0', '0.', '0.9']) fireEvent.change(input, {target: {value}})
    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.9)
  })

  it('steps mixed values together, but respects an explicitly typed replacement', () => {
    const onStep=vi.fn()
    const {input,onChange}=renderControl({mixed:true,value:undefined,onStep})
    fireEvent.click(screen.getByRole('button',{name:'value增大'}))
    expect(onStep).toHaveBeenCalledExactlyOnceWith(1)
    fireEvent.change(input,{target:{value:'2'}})
    fireEvent.click(screen.getByRole('button',{name:'value增大'}))
    fireEvent.blur(input)
    expect(onStep).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledExactlyOnceWith(2.05)
  })

  it('allows decreasing mixed values without interpreting their blank draft as zero', () => {
    const onStep=vi.fn()
    const {input,onChange}=renderControl({mixed:true,value:undefined,min:1,max:512,step:1,onStep})
    const decrease=screen.getByRole('button',{name:'value减小'})
    expect(decrease).toBeEnabled()
    fireEvent.click(decrease)
    expect(onStep).toHaveBeenCalledExactlyOnceWith(-1)
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.change(input,{target:{value:'1'}})
    expect(decrease).toBeDisabled()
    fireEvent.change(input,{target:{value:'512'}})
    expect(screen.getByRole('button',{name:'value增大'})).toBeDisabled()
  })

  it('uses the current value when stepping a cleared input', () => {
    const {input,onChange}=renderControl({value:24,min:1,max:512,step:1})
    fireEvent.change(input,{target:{value:''}})
    fireEvent.click(screen.getByRole('button',{name:'value减小'}))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(23)
  })

  it.each(['blur','Enter','Escape'] as const)('finishes %s before outside pointer handlers without duplicate saves',async finish => {
    const user=userEvent.setup(), onChange=vi.fn(), callsAtPointer=vi.fn()
    render(<><NumberControl ariaLabel="value" value={24} min={1} max={512} commitOnBlur onChange={onChange}/><div data-testid="canvas" onPointerDown={() => callsAtPointer(onChange.mock.calls.length)}/></>)
    const input=screen.getByRole('textbox',{name:'value'})
    await user.click(input)
    await user.clear(input)
    await user.type(input,'36')
    if(finish !== 'blur') await user.keyboard(`{${finish}}`)
    await user.click(screen.getByTestId('canvas'))
    expect(callsAtPointer).toHaveBeenCalledExactlyOnceWith(finish === 'Escape' ? 0 : 1)
    if(finish === 'Escape') expect(onChange).not.toHaveBeenCalled()
    else expect(onChange).toHaveBeenCalledExactlyOnceWith(36)
  })

  it('does not blur or double-commit when clicking its own step buttons',async () => {
    const user=userEvent.setup(), onChange=vi.fn()
    render(<NumberControl ariaLabel="value" value={24} min={1} max={512} commitOnBlur onChange={onChange}/>)
    const input=screen.getByRole('textbox',{name:'value'})
    await user.click(input)
    await user.clear(input)
    await user.type(input,'36')
    await user.click(screen.getByRole('button',{name:'value减小'}))
    expect(input).toHaveFocus()
    expect(onChange).toHaveBeenCalledExactlyOnceWith(35)
    await user.tab()
    expect(onChange).toHaveBeenCalledTimes(1)
  })
})

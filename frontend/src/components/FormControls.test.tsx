import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it, vi} from 'vitest'
import {NumberControl, SelectControl} from './FormControls'

describe('SelectControl typography',() => {
  const options = [{value:'a',label:'自定义云端翻译'},{value:'b',label:'不翻译（原文直通）'}]
  it('keeps the full selected label available when the compact trigger ellipsizes it', () => {
    const label='VeryLongCustomModelIdentifier'.repeat(6)
    render(<SelectControl compact ariaLabel="模型" value="a" options={[{value:'a',label}]} onChange={vi.fn()}/>)
    expect(screen.getByTitle(label)).toHaveTextContent(label)
    expect(screen.getByTitle(label)).toHaveClass('truncate')
  })
  it('carries the trigger font size into the portalled options',async () => {
    render(<SelectControl ariaLabel="服务商" value="a" options={options} onChange={vi.fn()}/>)
    const trigger = screen.getByRole('button',{name:'服务商'})
    trigger.style.fontSize = '16px'
    await userEvent.click(trigger)
    const list = screen.getByRole('listbox',{name:'服务商'})
    expect(list.parentElement).toBe(document.body)
    expect(list).toHaveStyle({fontSize:'16px'})
    for (const option of screen.getAllByRole('option')) {
      expect(option).toHaveStyle({fontSize:'inherit'})
      expect(option).toHaveClass('min-h-11')
      expect(option).not.toHaveClass('text-xs')
    }
    trigger.style.fontSize = '18px'
    fireEvent(window,new Event('resize'))
    expect(list).toHaveStyle({fontSize:'18px'})
  })
  it('keeps keyboard selection and focus restoration when using larger options',async () => {
    const onChange = vi.fn(), user = userEvent.setup()
    render(<SelectControl ariaLabel="运行设备" value="a" options={[options[0],{value:'disabled',label:'不可用',disabled:true},options[1]]} onChange={onChange}/>)
    const trigger = screen.getByRole('button',{name:'运行设备'})
    trigger.focus()
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onChange).toHaveBeenCalledExactlyOnceWith('b')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
  it('carries scoped radius and padding into the menu without changing other controls',async () => {
    render(<><SelectControl ariaLabel="设置模型" value="a" options={options} onChange={vi.fn()}/><SelectControl ariaLabel="编辑器模型" value="a" options={options} onChange={vi.fn()}/></>)
    const trigger = screen.getByRole('button',{name:'设置模型'})
    trigger.style.setProperty('--control-radius','10px')
    trigger.style.setProperty('--control-padding-x','14px')
    await userEvent.click(trigger)
    const list = screen.getByRole('listbox',{name:'设置模型'})
    expect(list.style.getPropertyValue('--control-radius')).toBe('10px')
    expect(list.style.getPropertyValue('--control-padding-x')).toBe('14px')
    for (const option of screen.getAllByRole('option')) {
      expect(option.style.borderRadius).toBe('var(--control-radius)')
      expect(option.style.paddingInline).toBe('var(--control-padding-x)')
    }
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button',{name:'编辑器模型'}))
    const editorList = screen.getByRole('listbox',{name:'编辑器模型'})
    expect(editorList.style.getPropertyValue('--control-radius')).toBe('8px')
    expect(editorList.style.getPropertyValue('--control-padding-x')).toBe('12px')
  })
})

describe('SelectControl popup sizing',() => {
  it('keeps keyboard navigation visible without resetting manual menu scrolling',async () => {
    const user = userEvent.setup()
    const options = Array.from({length:9},(_,index) => ({value:String(index),label:`字重 ${index + 1}`}))
    render(<SelectControl ariaLabel="默认字重" value="0" options={options} onChange={vi.fn()}/>)
    const trigger = screen.getByRole('button',{name:'默认字重'})
    await user.click(trigger)
    const list = screen.getByRole('listbox',{name:'默认字重'})
    vi.spyOn(list,'clientHeight','get').mockReturnValue(100)
    const items = screen.getAllByRole('option')
    items.forEach((item,index) => {
      vi.spyOn(item,'offsetTop','get').mockReturnValue(4 + index * 48)
      vi.spyOn(item,'offsetHeight','get').mockReturnValue(44)
    })
    try {
      await user.keyboard('{End}')
      expect(list.scrollTop).toBe(332)
      expect(items[8]).toHaveClass('bg-hover')
      await user.keyboard('{Home}')
      expect(list.scrollTop).toBe(4)
      await user.keyboard('{ArrowUp}')
      expect(list.scrollTop).toBe(332)
      expect(trigger).toHaveFocus()
      list.scrollTop = 150
      fireEvent.scroll(list)
      expect(list.scrollTop).toBe(150)
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    } finally {vi.restoreAllMocks()}
  })

  it.each([2,20])('uses a content-sized menu with a shared height limit for %i options',async count => {
    const options = Array.from({length:count},(_,index) => ({value:String(index),label:`模型选项 ${index + 1}`}))
    render(<SelectControl ariaLabel="当前模型" value="0" options={options} onChange={vi.fn()}/>)
    await userEvent.click(screen.getByRole('button',{name:'当前模型'}))
    const list = screen.getByRole('listbox',{name:'当前模型'})
    expect(list).toHaveStyle({maxHeight:'320px'})
    expect(list.style.height).toBe('')
    expect(screen.getAllByRole('option')).toHaveLength(count)
  })

  it('opens upward near the viewport bottom and keeps the scroll limit',async () => {
    render(<SelectControl ariaLabel="运行设备" value="auto" options={[{value:'auto',label:'自动'},{value:'cpu',label:'CPU'}]} onChange={vi.fn()}/>)
    const trigger = screen.getByRole('button',{name:'运行设备'})
    const rect = new DOMRect(20,window.innerHeight - 60,320,38)
    const measure = vi.spyOn(trigger,'getBoundingClientRect').mockReturnValue(rect)
    try {
      await userEvent.click(trigger)
      const list = screen.getByRole('listbox',{name:'运行设备'})
      expect(list).toHaveStyle({bottom:'66px',maxHeight:'320px'})
      expect(list.style.top).toBe('')
    } finally {measure.mockRestore()}
  })

  it('constrains the menu to the available viewport space',async () => {
    render(<SelectControl ariaLabel="运行设备" value="auto" options={[{value:'auto',label:'自动'},{value:'cpu',label:'CPU'}]} onChange={vi.fn()}/>)
    const trigger = screen.getByRole('button',{name:'运行设备'})
    const measure = vi.spyOn(trigger,'getBoundingClientRect').mockReturnValue(new DOMRect(20,window.innerHeight - 238,320,38))
    try {
      await userEvent.click(trigger)
      expect(screen.getByRole('listbox',{name:'运行设备'})).toHaveStyle({maxHeight:'186px'})
    } finally {measure.mockRestore()}
  })
})

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

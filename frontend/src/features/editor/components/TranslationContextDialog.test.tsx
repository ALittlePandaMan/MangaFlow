import {fireEvent, render, screen, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {useCallback, useState} from 'react'
import {describe, expect, it, vi} from 'vitest'
import {TranslationContextDialog} from './TranslationContextDialog'

function harnessProps() {
  return {text:'{}',mode:'snapshot' as const,error:'',saving:false,onTextChange:vi.fn(),onModeChange:vi.fn(),onError:vi.fn(),onClose:vi.fn(),onSave:vi.fn()}
}

describe('translation context dialog',() => {
  it('uses a padded dialog body and native mode controls with concise labels',async () => {
    const user = userEvent.setup()
    const props = harnessProps()
    render(<TranslationContextDialog {...props}/>)
    const dialog = screen.getByRole('dialog',{name:'翻译上下文'})
    expect(dialog).toHaveClass('translation-context-dialog')
    expect(dialog.parentElement).toHaveClass('translation-context-overlay')
    expect(screen.queryByText('PROJECT CONTEXT')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('radio',{name:'提交时快照'})).toBeChecked()
    await user.click(within(dialog).getByText('依赖前页翻译'))
    expect(props.onModeChange).toHaveBeenCalledWith('previous_page')
    const editor = screen.getByRole('textbox',{name:'项目翻译上下文 JSON'})
    expect(editor).toHaveAttribute('wrap','soft')
    expect(editor.parentElement).toHaveClass('translation-context-editor')
    await user.click(screen.getByRole('button',{name:'保存上下文'}))
    expect(props.onSave).toHaveBeenCalledOnce()
  })
  it('formats the draft without submitting or changing the selected mode',async () => {
    const props = {...harnessProps(),text:'{"names":{"hero":"熊"},"terms":["你好"]}'}
    render(<TranslationContextDialog {...props}/>)
    await userEvent.click(screen.getByRole('button',{name:'格式化'}))
    expect(props.onTextChange).toHaveBeenCalledWith(JSON.stringify(JSON.parse(props.text),null,2))
    expect(props.onSave).not.toHaveBeenCalled()
    expect(props.onModeChange).not.toHaveBeenCalled()
  })
  it.each(['[]','null','not json'])('leaves an invalid JSON draft unchanged: %s',async text => {
    const props = {...harnessProps(),text}
    render(<TranslationContextDialog {...props}/>)
    await userEvent.click(screen.getByRole('button',{name:'格式化'}))
    expect(props.onError).toHaveBeenCalledWith(expect.any(String))
    expect(props.onTextChange).not.toHaveBeenCalled()
    expect(props.onSave).not.toHaveBeenCalled()
  })
  it('keeps focus in the dialog, restores it on unmount, and does not close during save',async () => {
    const props = harnessProps()
    const outside = document.createElement('button')
    document.body.append(outside); outside.focus()
    const {rerender,unmount} = render(<TranslationContextDialog {...props}/>)
    const user = userEvent.setup()
    expect(screen.getByRole('button',{name:'关闭翻译上下文'})).toHaveFocus()
    await user.tab({shift:true})
    expect(screen.getByRole('button',{name:'保存上下文'})).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button',{name:'关闭翻译上下文'})).toHaveFocus()
    rerender(<TranslationContextDialog {...props} saving/>)
    await user.keyboard('{Escape}')
    fireEvent.submit(screen.getByRole('dialog'))
    expect(props.onClose).not.toHaveBeenCalled()
    expect(props.onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox')).toBeDisabled()
    expect(screen.getByRole('radio',{name:'提交时快照'})).toBeDisabled()
    unmount()
    expect(outside).toHaveFocus()
    outside.remove()
  })
  it('focuses a validation error and clears the stale message while editing',async () => {
    function ControlledDialog() {
      const [text,setText] = useState('[]')
      const [error,setError] = useState('')
      const close = useCallback(() => {},[])
      return <TranslationContextDialog text={text} mode="snapshot" saving={false} error={error} onTextChange={setText} onError={setError} onModeChange={() => {}} onClose={close} onSave={() => {}}/>
    }
    render(<ControlledDialog/>)
    await userEvent.click(screen.getByRole('button',{name:'格式化'}))
    expect(screen.getByRole('alert')).toHaveTextContent('上下文需要是 JSON 对象')
    expect(screen.getByRole('alert')).toHaveFocus()
    fireEvent.change(screen.getByRole('textbox'),{target:{value:'{}'}})
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

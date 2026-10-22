import {fireEvent, render, screen} from '@testing-library/react'
import {useRef} from 'react'
import {createPortal} from 'react-dom'
import {describe, expect, it, vi} from 'vitest'
import {useModalFocus} from './useModalFocus'

function Modal({id, onEscape}: {id:string, onEscape?:() => void}) {
  const ref=useRef<HTMLDivElement>(null)
  useModalFocus(ref, {onEscape})
  return createPortal(<div><div ref={ref} id={id} tabIndex={-1} role="dialog" aria-modal="true" aria-label={id}><button>{id}操作</button></div></div>, document.body)
}

describe('shared modal focus lifecycle', () => {
  it('keeps the top portal interactive when both modal layers mount in one commit', () => {
    const {rerender}=render(<><Modal id="底层"/><Modal id="顶层"/></>)
    const top=screen.getByRole('dialog',{name:'顶层'})
    expect(top.closest('[inert]')).toBeNull()
    expect(screen.getByRole('button',{name:'顶层操作'})).toHaveFocus()
    rerender(<Modal id="底层"/>)
    expect(screen.getByRole('dialog',{name:'底层'}).closest('[inert]')).toBeNull()
    expect(screen.getByRole('button',{name:'底层操作'})).toHaveFocus()
  })
  it('keeps the top modal isolated if the underlying modal unmounts first', () => {
    const onEscape=vi.fn()
    function Scene({first, second}: {first:boolean, second:boolean}) {
      return <><button>背景</button>{first && <Modal id="一"/>}{second && <Modal id="二" onEscape={onEscape}/>}</>
    }
    const {container,rerender}=render(<Scene first second={false}/>)
    rerender(<Scene first second/>)
    expect(screen.getByRole('button',{name:'二操作'})).toHaveFocus()
    rerender(<Scene first={false} second/>)
    expect(container).toHaveAttribute('inert')
    expect(document.body.style.overflow).toBe('hidden')
    expect(screen.getByRole('button',{name:'二操作'})).toHaveFocus()
    fireEvent.keyDown(document,{key:'Escape'})
    expect(onEscape).toHaveBeenCalledOnce()
    rerender(<Scene first={false} second={false}/>)
    expect(container).not.toHaveAttribute('inert')
    expect(document.body.style.overflow).toBe('')
  })

  it('preserves existing inert attributes and body overflow when the modal closes', () => {
    const previous=document.body.style.overflow
    document.body.style.overflow='clip'
    const background=document.createElement('aside')
    background.setAttribute('inert','')
    document.body.append(background)
    const {unmount}=render(<Modal id="保留原状态"/>)
    unmount()
    expect(background).toHaveAttribute('inert')
    expect(document.body.style.overflow).toBe('clip')
    background.remove()
    document.body.style.overflow=previous
  })
})

import {fireEvent, render, screen} from '@testing-library/react'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {useEditorStore} from '../store'
import {EditorToolbar} from './EditorToolbar'

beforeEach(() => useEditorStore.setState({zoom: 1, viewReset: 0}))

describe('editor toolbar', () => {
  it('uses the same compact icon style for tools and undo/redo',() => {
    render(<EditorToolbar onUndo={vi.fn()} onRedo={vi.fn()} canUndo canRedo/>)
    for (const name of ['拖拽画布','选择 / 变换','矩形区域','撤销','重做']) {
      expect(screen.getByRole('button',{name})).toHaveClass('icon-button','icon-button-compact','icon-button-ghost')
    }
  })

  it('keeps tool selection without rendering floating zoom controls', () => {
    render(<EditorToolbar onUndo={vi.fn()} onRedo={vi.fn()} canUndo canRedo/>)
    fireEvent.click(screen.getByRole('button', {name:'矩形区域'}))
    expect(useEditorStore.getState().tool).toBe('rectangle')
    expect(screen.queryByLabelText('画布缩放')).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:/放大画布|缩小画布|重置画布缩放/})).not.toBeInTheDocument()
  })

  it('keeps all controls disabled while the canvas is unavailable', () => {
    render(<EditorToolbar onUndo={vi.fn()} onRedo={vi.fn()} canUndo canRedo disabled/>)
    screen.getAllByRole('button').forEach(button => expect(button).toBeDisabled())
  })
})

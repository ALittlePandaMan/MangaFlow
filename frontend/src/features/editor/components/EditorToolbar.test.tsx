import {fireEvent, render, screen, within} from '@testing-library/react'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {useEditorStore} from '../store'
import {CanvasZoomControls, EditorToolbar} from './EditorToolbar'

beforeEach(() => useEditorStore.setState({zoom: 1, viewReset: 0}))

describe('canvas zoom controls', () => {
  it('stacks the original controls vertically in the same order', () => {
    render(<CanvasZoomControls/>)
    const controls = screen.getByLabelText('画布缩放')
    expect(controls).toHaveClass('flex', 'flex-col')
    expect(within(controls).getAllByRole('button').map(button => button.getAttribute('aria-label')))
      .toEqual(['缩小画布', '重置画布缩放', '放大画布'])
    within(controls).getAllByRole('button').forEach(button => expect(button).toHaveClass('icon-button','icon-button-compact','icon-button-ghost'))
  })

  it('uses the same compact icon style for tools, undo/redo and zoom',() => {
    render(<><EditorToolbar onUndo={vi.fn()} onRedo={vi.fn()} canUndo canRedo/><CanvasZoomControls/></>)
    for (const name of ['拖拽画布','选择 / 变换','矩形区域','撤销','重做','缩小画布','重置画布缩放','放大画布']) {
      expect(screen.getByRole('button',{name})).toHaveClass('icon-button','icon-button-compact','icon-button-ghost')
    }
  })

  it('preserves zoom and fit-to-canvas actions', () => {
    render(<CanvasZoomControls/>)
    fireEvent.click(screen.getByRole('button', {name: '缩小画布'}))
    expect(useEditorStore.getState().zoom).toBeCloseTo(1 / 1.15)
    fireEvent.click(screen.getByRole('button', {name: '放大画布'}))
    expect(useEditorStore.getState().zoom).toBeCloseTo(1)
    fireEvent.click(screen.getByRole('button', {name: '重置画布缩放'}))
    expect(useEditorStore.getState().viewReset).toBe(1)
  })

  it('keeps all controls disabled while the canvas is unavailable', () => {
    render(<CanvasZoomControls disabled/>)
    within(screen.getByLabelText('画布缩放')).getAllByRole('button').forEach(button => expect(button).toBeDisabled())
  })
})

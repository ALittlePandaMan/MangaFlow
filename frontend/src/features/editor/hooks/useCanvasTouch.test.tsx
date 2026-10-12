import {fireEvent, render, screen} from '@testing-library/react'
import {useRef} from 'react'
import {describe, expect, it, vi} from 'vitest'
import {pinchTransform, useCanvasTouch} from './useCanvasTouch'
import {useEditorStore} from '../store'

const start = {zoom: 1, pan: {x: 0, y: 0}, viewport: {width: 400, height: 600}, content: {width: 400, height: 600}, fitScale: 1}
describe('canvas touch navigation', () => {
  it('pans with one finger only in hand mode and upgrades to a two-finger pinch', () => {
    const onStart = vi.fn(), onEnd = vi.fn(), onChange = vi.fn(), canvasTouch = vi.fn()
    function Fixture() {
      const ref = useRef<HTMLDivElement>(null)
      useCanvasTouch(ref, {...start, singleFingerPan: true, onStart, onEnd, onChange})
      return <div ref={ref}><canvas data-testid="pan-canvas" onTouchStart={canvasTouch}/></div>
    }
    render(<Fixture/>)
    const canvas = screen.getByTestId('pan-canvas')
    fireEvent.touchStart(canvas, {touches: [{clientX: 100, clientY: 200}]})
    fireEvent.touchMove(canvas, {touches: [{clientX: 130, clientY: 220}]})
    expect(onChange).toHaveBeenLastCalledWith({zoom: 1, pan: {x: 30, y: 20}})
    expect(canvasTouch).not.toHaveBeenCalled()
    fireEvent.touchStart(canvas, {touches: [{clientX: 100, clientY: 200}, {clientX: 200, clientY: 200}]})
    fireEvent.touchMove(canvas, {touches: [{clientX: 50, clientY: 200}, {clientX: 250, clientY: 200}]})
    expect(onChange.mock.lastCall?.[0].zoom).toBe(2)
    expect(onStart).toHaveBeenCalledOnce()
    fireEvent.touchEnd(canvas, {touches: []})
    expect(onEnd).toHaveBeenCalledOnce()
  })
  it('signals a recenter even when zoom already equals one', () => {
    useEditorStore.setState({zoom: 1, viewReset: 0})
    useEditorStore.getState().resetView()
    expect(useEditorStore.getState().zoom).toBe(1)
    expect(useEditorStore.getState().viewReset).toBe(1)
    useEditorStore.getState().resetView()
    expect(useEditorStore.getState().viewReset).toBe(2)
  })
  it('keeps the image point under the pinch midpoint and clamps zoom', () => {
    expect(pinchTransform(start, {x: 100, y: 200}, {x: 120, y: 230}, 2)).toEqual({zoom: 2, pan: {x: 120, y: 130}})
    expect(pinchTransform(start, {x: 200, y: 300}, {x: 220, y: 310}, 1)).toEqual({zoom: 1, pan: {x: 20, y: 10}})
    expect(pinchTransform(start, {x: 200, y: 300}, {x: 200, y: 300}, 100).zoom).toBe(6)
    expect(pinchTransform(start, {x: 200, y: 300}, {x: 200, y: 300}, 0).zoom).toBe(.05)
  })
  it('consumes multi-touch until every finger lifts, leaving controls and single taps alone', () => {
    const onStart = vi.fn(), onEnd = vi.fn(), onChange = vi.fn(), canvasTouch = vi.fn()
    function Fixture() {
      const ref = useRef<HTMLDivElement>(null)
      useCanvasTouch(ref, {...start, onStart, onEnd, onChange})
      return <div ref={ref}><canvas data-testid="canvas" onTouchStart={canvasTouch}/><button>图层</button></div>
    }
    render(<Fixture/>)
    const canvas = screen.getByTestId('canvas')
    const one = [{clientX: 100, clientY: 200}]
    const two = [...one, {clientX: 200, clientY: 200}]
    fireEvent.touchStart(screen.getByRole('button'), {touches: two})
    expect(onStart).not.toHaveBeenCalled()
    fireEvent.touchStart(canvas, {touches: one})
    expect(canvasTouch).toHaveBeenCalledOnce()
    fireEvent.touchStart(canvas, {touches: two})
    expect(onStart).toHaveBeenCalledOnce()
    expect(canvasTouch).toHaveBeenCalledOnce()
    fireEvent.touchMove(canvas, {touches: [{clientX: 50, clientY: 200}, {clientX: 250, clientY: 200}]})
    expect(onChange.mock.calls[0][0].zoom).toBe(2)
    fireEvent.touchEnd(canvas, {touches: one})
    expect(onEnd).not.toHaveBeenCalled()
    fireEvent.touchEnd(canvas, {touches: []})
    expect(onEnd).toHaveBeenCalledOnce()
  })
})

import {act, render} from '@testing-library/react'
import {forwardRef, type ReactNode} from 'react'
import type Konva from 'konva'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {RegionPolygonEditor} from './RegionPolygonEditor'

const {rects} = vi.hoisted(() => ({rects:vi.fn()}))
vi.mock('konva', () => ({default:{}}))
vi.mock('./RegionLabel', () => ({RegionLabel:() => null}))
vi.mock('react-konva', () => ({
  Group:forwardRef(function MockGroup({children}: {children?:ReactNode},_ref) {return <>{children}</>}),
  Line:() => null,
  Circle:() => null,
  Rect:(props:Konva.RectConfig) => {rects(props); return null},
}))

const polygon = [[180,100],[440,170],[370,430],[110,360]]
type DragHandler = (event:Konva.KonvaEventObject<DragEvent>) => void
type Handle = {x:number,y:number,height:number,draggable?:boolean,onDragStart:DragHandler,onDragMove:DragHandler,onDragEnd:DragHandler}
const handles = (height:number) => rects.mock.calls.map(([props]) => props as Handle).filter(props => props.draggable && props.height === height).slice(-4)
const node = (x:number,y:number) => ({
  x:() => x,y:() => y,
  position:(point:{x:number,y:number}) => {x = point.x; y = point.y},
  getStage:() => ({container:() => ({style:{cursor:''}})}),
})
const event = (target:ReturnType<typeof node>) => ({target,evt:new Event('drag'),cancelBubble:false}) as unknown as Konva.KonvaEventObject<DragEvent>
const props = () => ({polygon,pageWidth:600,pageHeight:600,scale:1,labelScale:1,label:'R011 · 译文框',labelColor:'#2665af',visible:true,
  onSelect:vi.fn(),onContextMenu:vi.fn(),onPreview:vi.fn<(polygon:number[][],rotationDelta?:number) => void>(),
  onCommit:vi.fn(async (_polygon:number[][],_rotationDelta?:number) => {})})
const expectParallel = (before:number[][],after:number[][]) => before.forEach((point,index) => {
  const next = (index + 1) % before.length
  const dx = before[next][0] - point[0], dy = before[next][1] - point[1]
  const nx = after[next][0] - after[index][0], ny = after[next][1] - after[index][1]
  expect((dx * ny - dy * nx) / Math.hypot(dx,dy)).toBeCloseTo(0,8)
})

describe('polygon edge handle interaction', () => {
  beforeEach(() => rects.mockClear())

  it.each([0,1,2,3])('compresses edge %s of a rotated rectangle in parallel and commits only at drag end', index => {
    const callbacks = props()
    render(<RegionPolygonEditor {...callbacks}/>)
    const handle = handles(14)[index], target = node(handle.x,handle.y), drag = event(target)
    act(() => handle.onDragStart(drag))
    target.position({x:handle.x + 20,y:handle.y + 30})
    act(() => handle.onDragMove(drag))
    const next = callbacks.onPreview.mock.lastCall![0]
    expectParallel(polygon,next)
    expect(next).not.toEqual(polygon)
    expect(next[(index + 2) % 4]).toEqual(polygon[(index + 2) % 4])
    expect(next[(index + 3) % 4]).toEqual(polygon[(index + 3) % 4])
    expect(callbacks.onCommit).not.toHaveBeenCalled()
    act(() => handles(14)[index].onDragEnd(drag))
    expect(callbacks.onCommit).toHaveBeenCalledExactlyOnceWith(next,0)
  })

  it('keeps all edges parallel when compression stops at the minimum size or page boundary', () => {
    const callbacks = props()
    render(<RegionPolygonEditor {...callbacks}/>)
    const handle = handles(14)[1], target = node(handle.x,handle.y), drag = event(target)
    act(() => handle.onDragStart(drag))
    for (const position of [{x:-1000,y:-1000},{x:2000,y:2000}]) {
      target.position(position)
      act(() => handles(14)[1].onDragMove(drag))
      const next = callbacks.onPreview.mock.lastCall![0]
      expectParallel(polygon,next)
      next.forEach(point => point.forEach(value => expect(value).toBeGreaterThanOrEqual(0)))
      next.forEach(point => point.forEach(value => expect(value).toBeLessThanOrEqual(600)))
      for (let index = 0; index < next.length; index++) {
        const end = next[(index + 1) % next.length]
        expect(Math.hypot(end[0] - next[index][0],end[1] - next[index][1])).toBeGreaterThanOrEqual(4)
      }
    }
    act(() => handles(14)[1].onDragEnd(drag))
    expect(callbacks.onCommit).toHaveBeenCalledOnce()
  })

  it('keeps corner handles free for perspective reshaping', () => {
    const callbacks = props()
    render(<RegionPolygonEditor {...callbacks}/>)
    const handle = handles(11)[0], target = node(handle.x,handle.y), drag = event(target)
    act(() => handle.onDragStart(drag))
    target.position({x:190,y:130})
    act(() => handle.onDragMove(drag))
    const next = callbacks.onPreview.mock.lastCall![0]
    expect(next[0]).toEqual([190,130])
    expect(next.slice(1)).toEqual(polygon.slice(1))
    act(() => handles(11)[0].onDragEnd(drag))
    expect(callbacks.onCommit).toHaveBeenCalledExactlyOnceWith(next,0)
  })
})

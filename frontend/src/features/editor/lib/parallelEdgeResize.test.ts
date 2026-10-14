import {describe, expect, it} from 'vitest'
import {createParallelEdgeResize, parallelEdgeCursor, resizeParallelEdge, type ResizePoint} from './parallelEdgeResize'

const rotate = (points: ResizePoint[], degrees: number): ResizePoint[] => {
  const radians = degrees * Math.PI / 180
  return points.map(([x,y]) => [200 + x * Math.cos(radians) - y * Math.sin(radians), 200 + x * Math.sin(radians) + y * Math.cos(radians)])
}
const rectangle: ResizePoint[] = [[0,0],[120,0],[120,100],[0,100]]
const expectParallel = (before: ResizePoint[], after: ResizePoint[]) => {
  for (let index = 0; index < before.length; index++) {
    const next = (index + 1) % before.length
    const dx = before[next][0] - before[index][0], dy = before[next][1] - before[index][1]
    const nx = after[next][0] - after[index][0], ny = after[next][1] - after[index][1]
    expect((dx * ny - dy * nx) / Math.hypot(dx,dy)).toBeCloseTo(0,9)
  }
}

describe('parallel edge resizing', () => {
  it.each([0,15,45,70,90,-30,-135])('preserves all edge directions and right angles after rotating %s degrees', degrees => {
    const before = rotate(rectangle,degrees)
    for (let edge = 0; edge < 4; edge++) {
      const drag = createParallelEdgeResize(before,edge)!
      for (const distance of [-20,20]) {
        const after = resizeParallelEdge(drag,[drag.normal[0] * distance,drag.normal[1] * distance])
        expectParallel(before,after)
        const top = [after[1][0] - after[0][0],after[1][1] - after[0][1]]
        const side = [after[2][0] - after[1][0],after[2][1] - after[1][1]]
        expect(top[0] * side[0] + top[1] * side[1]).toBeCloseTo(0,8)
        expect(after[(edge + 2) % 4]).toEqual(before[(edge + 2) % 4])
        expect(after[(edge + 3) % 4]).toEqual(before[(edge + 3) % 4])
      }
    }
  })

  it.each([false,true])('preserves perspective trapezoid edge directions with reverse winding=%s', reverse => {
    const points: ResizePoint[] = [[20,20],[140,40],[125,170],[50,160]]
    const before = reverse ? [...points].reverse() : points
    for (let index = 0; index < before.length; index++) {
      const drag = createParallelEdgeResize(before,index)!
      const after = resizeParallelEdge(drag,[25,-15])
      expectParallel(before,after)
      expect(after).not.toEqual(before)
    }
  })

  it('ignores pointer movement along the edge and uses the same baseline on every move', () => {
    const before = rotate(rectangle,24)
    const saved = structuredClone(before)
    const drag = createParallelEdgeResize(before,0)!
    const edge = [before[1][0] - before[0][0],before[1][1] - before[0][1]] as ResizePoint
    const unchanged = resizeParallelEdge(drag,edge)
    unchanged.forEach((point,index) => point.forEach((value,axis) => expect(value).toBeCloseTo(before[index][axis],9)))
    const firstMove = resizeParallelEdge(drag,[10,10])
    resizeParallelEdge(drag,[30,30])
    expect(resizeParallelEdge(drag,[10,10])).toEqual(firstMove)
    expect(before).toEqual(saved)
    expect(drag.start).toEqual(saved)
  })

  it.each([0,1,5])('moves collinear boundary vertices together without introducing kinks for edge %s', index => {
    const before: ResizePoint[] = [[10,20],[50,20],[90,20],[100,90],[10,90],[10,50]]
    const drag = createParallelEdgeResize(before,index)!
    const after = resizeParallelEdge(drag,[drag.normal[0] * 10,drag.normal[1] * 10])
    expectParallel(before,after)
    if (index < 2) {
      expect(after[0][1]).toBe(30)
      expect(after[1][1]).toBe(30)
      expect(after[2][1]).toBe(30)
      expect(after[3]).toEqual(before[3])
      expect(after[4]).toEqual(before[4])
    } else {
      expect(after[4][0]).toBe(20)
      expect(after[5][0]).toBe(20)
      expect(after[0][0]).toBe(20)
    }
  })

  it('handles triangles without changing edge directions', () => {
    const before: ResizePoint[] = [[20,20],[140,30],[90,150]]
    const drag = createParallelEdgeResize(before,0)!
    expectParallel(before,resizeParallelEdge(drag,[0,10]))
  })

  it('rejects invalid or degenerate geometry without producing unstable offsets', () => {
    expect(createParallelEdgeResize([],0)).toBeNull()
    expect(createParallelEdgeResize(rectangle,-1)).toBeNull()
    expect(createParallelEdgeResize(rectangle,4)).toBeNull()
    expect(createParallelEdgeResize([[0,0],[0,0],[20,20]],0)).toBeNull()
    expect(createParallelEdgeResize([[0,0],[10,0],[20,0]],0)).toBeNull()
    expect(createParallelEdgeResize([[NaN,0],[10,0],[20,20]],0)).toBeNull()
  })

  it('uses diagonal resize cursors for rotated boundaries', () => {
    expect(parallelEdgeCursor([0,0],[100,0])).toBe('ns-resize')
    expect(parallelEdgeCursor([0,0],[0,100])).toBe('ew-resize')
    expect(parallelEdgeCursor([0,0],[100,100])).toBe('nesw-resize')
    expect(parallelEdgeCursor([0,0],[100,-100])).toBe('nwse-resize')
  })
})

import {useEffect, useLayoutEffect, useRef, type RefObject} from 'react'

type Point = {x: number, y: number}
type Transform = {zoom: number, pan: Point}
type Snapshot = Transform & {viewport: {width: number, height: number}, content: {width: number, height: number}, fitScale: number}
type Options = Snapshot & {singleFingerPan?: boolean, onStart: () => void, onEnd: () => void, onChange: (view: Transform) => void}

export function pinchTransform(start: Snapshot, anchor: Point, center: Point, ratio: number): Transform {
  const zoom = Math.max(.05, Math.min(6, start.zoom * ratio))
  const scale = Math.max(.01, start.fitScale * start.zoom)
  const nextScale = Math.max(.01, start.fitScale * zoom)
  const origin = {
    x: (start.viewport.width - start.content.width * scale) / 2 + start.pan.x,
    y: (start.viewport.height - start.content.height * scale) / 2 + start.pan.y,
  }
  return {zoom, pan: {
    x: center.x - (anchor.x - origin.x) / scale * nextScale - (start.viewport.width - start.content.width * nextScale) / 2,
    y: center.y - (anchor.y - origin.y) / scale * nextScale - (start.viewport.height - start.content.height * nextScale) / 2,
  }}
}

/** Capture multi-touch before Konva can turn the second finger into a region edit. */
export function useCanvasTouch(container: RefObject<HTMLDivElement>, options: Options) {
  const latest = useRef(options)
  useLayoutEffect(() => {latest.current = options})
  useEffect(() => {
    const element = container.current
    if (!element) return
    let gesture: {start: Snapshot, anchor: Point, distance: number, fingers: number} | null = null
    const pair = (touches: TouchList) => {
      const rect = element.getBoundingClientRect()
      const second = touches[1] || touches[0]
      return {
        center: {x: (touches[0].clientX + second.clientX) / 2 - rect.left, y: (touches[0].clientY + second.clientY) / 2 - rect.top},
        distance: Math.max(1, Math.hypot(second.clientX - touches[0].clientX, second.clientY - touches[0].clientY)),
      }
    }
    const consume = (event: TouchEvent) => {
      if (event.cancelable) event.preventDefault()
      event.stopPropagation()
    }
    const start = (event: TouchEvent) => {
      if (!event.touches.length || !(event.target instanceof HTMLCanvasElement)) return
      if (event.touches.length < 2 && !latest.current.singleFingerPan && !gesture) return
      if (gesture && gesture.fingers === Math.min(2, event.touches.length)) {consume(event); return}
      const points = pair(event.touches)
      const alreadyNavigating = Boolean(gesture)
      gesture = {start: latest.current, anchor: points.center, distance: points.distance, fingers: Math.min(2, event.touches.length)}
      consume(event)
      if (!alreadyNavigating) latest.current.onStart()
    }
    const move = (event: TouchEvent) => {
      if (!gesture) return
      consume(event)
      if (event.touches.length < gesture.fingers) return
      const points = pair(event.touches)
      latest.current.onChange(pinchTransform(gesture.start, gesture.anchor, points.center, points.distance / gesture.distance))
    }
    const end = (event: TouchEvent) => {
      if (!gesture) return
      consume(event)
      // Suppress the final finger's tap too: it must not create or select a region.
      if (!event.touches.length || event.type === 'touchcancel') {
        gesture = null
        latest.current.onEnd()
      }
    }
    const settings = {capture: true, passive: false}
    element.addEventListener('touchstart', start, settings)
    element.addEventListener('touchmove', move, settings)
    element.addEventListener('touchend', end, settings)
    element.addEventListener('touchcancel', end, settings)
    return () => {
      element.removeEventListener('touchstart', start, true)
      element.removeEventListener('touchmove', move, true)
      element.removeEventListener('touchend', end, true)
      element.removeEventListener('touchcancel', end, true)
    }
  }, [container])
}

import {useLayoutEffect, useRef} from 'react'
import type {TextRegion} from '../../../types'
import {cn, textareaClass} from '../../../ui'
import {perspectiveQuadSize} from './perspectiveText'
import {hasCurrentComputedFont, sizeText, usesAutomaticFontSize} from './fontSizing'

export function currentFontSize(region: TextRegion): number {
  if (!region.translated_text.trim() || !usesAutomaticFontSize(region) || hasCurrentComputedFont(region)) return region.font_size
  const rect = region.perspective_warp ? perspectiveQuadSize(region.translated_polygon) ?? undefined : undefined
  return sizeText(region,rect).fontSize
}

export function coordinateValue(value: number): number {
  return Number(value.toFixed(2))
}

export function normalizedAngle(degrees: number): number {
  let angle = degrees % 360
  if (angle > 180) angle -= 360
  if (angle < -180) angle += 360
  return Math.abs(angle) < .001 ? 0 : angle
}

export function polygonRotation(polygon: number[][]): number {
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index]
    const next = polygon[(index + 1) % polygon.length]
    const dx = next[0] - current[0]
    const dy = next[1] - current[1]
    if (Math.hypot(dx, dy) > .001) return normalizedAngle(Math.atan2(dy, dx) * 180 / Math.PI)
  }
  return 0
}

export function rotatePolygon(polygon: number[][], degrees: number): number[][] {
  const center = polygon.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0])
    .map(total => total / polygon.length)
  const radians = degrees * Math.PI / 180
  const cosine = Math.cos(radians)
  const sine = Math.sin(radians)
  return polygon.map(point => {
    const x = point[0] - center[0]
    const y = point[1] - center[1]
    return [center[0] + x * cosine - y * sine, center[1] + x * sine + y * cosine]
  })
}

export function polygonBbox(polygon: number[][]): number[] {
  const xs = polygon.map(point => point[0])
  const ys = polygon.map(point => point[1])
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  return [minX, minY, Math.max(...xs) - minX, Math.max(...ys) - minY]
}

export function AutoResizeTextarea({value, onChange, placeholder, className = ''}: {value: string, onChange: (value: string) => void, placeholder: string, className?: string}) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = textarea.current
    if (!element) return
    const resize = () => {
      element.style.height = 'auto'
      element.style.height = `${Math.min(220,Math.max(64, element.scrollHeight))}px`
      element.style.overflowY = element.scrollHeight > 220 ? 'auto' : 'hidden'
    }
    resize()
    if (typeof ResizeObserver === 'undefined') return
    let width = element.getBoundingClientRect().width
    const observer = new ResizeObserver(entries => {
      const nextWidth = entries[0]?.contentRect.width
      if (nextWidth !== undefined && nextWidth !== width) {width = nextWidth; resize()}
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [value])
  return <textarea ref={textarea} className={cn(textareaClass, 'min-h-[64px] text-[13px] leading-relaxed', className)} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder}/>
}

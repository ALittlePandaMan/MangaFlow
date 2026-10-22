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

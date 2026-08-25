import {Check, ChevronDown, Minus, Plus} from 'lucide-react'
import {CSSProperties, KeyboardEvent, RefObject, useEffect, useId, useLayoutEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {cn, scrollbarClass} from '../ui'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

interface SelectControlProps {
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  disabled?: boolean
  name?: string
  placeholder?: string
  ariaLabel?: string
  compact?: boolean
}

export function SelectControl({value, options, onChange, disabled = false, name, placeholder = '请选择', ariaLabel, compact = false}: SelectControlProps) {
  const [open, setOpen] = useState(false)
  const selectedIndex = options.findIndex(option => option.value === value)
  const [activeIndex, setActiveIndex] = useState(Math.max(0, selectedIndex))
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const position = useFloatingPosition(open, triggerRef, Math.min(320, options.length * 44 + 8))

  useEffect(() => {
    if (!open) return
    setActiveIndex(Math.max(0, selectedIndex))
    return listenForOutsidePointer(triggerRef, popupRef, () => setOpen(false))
  }, [open, selectedIndex])

  const move = (direction: 1 | -1) => {
    if (!options.length) return
    let next = activeIndex
    for (let count = 0; count < options.length; count += 1) {
      next = (next + direction + options.length) % options.length
      if (!options[next].disabled) break
    }
    setActiveIndex(next)
  }
  const choose = (index: number) => {
    const option = options[index]
    if (!option || option.disabled) return
    onChange(option.value)
    setOpen(false)
    triggerRef.current?.focus()
  }
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) setOpen(true)
      else move(event.key === 'ArrowDown' ? 1 : -1)
    } else if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault(); choose(activeIndex)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault(); setOpen(true)
    } else if (event.key === 'Escape') {
      event.preventDefault(); setOpen(false)
    } else if (event.key === 'Home' && open) {
      event.preventDefault(); setActiveIndex(0)
    } else if (event.key === 'End' && open) {
      event.preventDefault(); setActiveIndex(Math.max(0, options.length - 1))
    } else if (event.key === 'Tab') setOpen(false)
  }
  const selected = options[selectedIndex]
  return <div className={cn('relative w-full min-w-0', disabled && 'opacity-50')}>
    {name && <input type="hidden" name={name} value={value}/>}
    <button ref={triggerRef} type="button" className={cn('flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border bg-canvas text-left text-ink outline-none transition duration-150 hover:border-line-strong hover:bg-hover focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed', compact ? 'h-[34px] px-2 text-[11px]' : 'h-[38px] px-3 text-xs', open ? 'border-accent shadow-[0_0_0_3px_rgb(16_211_163/.1)]' : 'border-line')} disabled={disabled} aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(current => !current)} onKeyDown={onKeyDown}>
      <span className={cn('min-w-0 truncate', !selected && 'text-disabled')}>{selected?.label || placeholder}</span><ChevronDown className={cn('shrink-0 text-muted transition-transform duration-150', open && 'rotate-180 text-accent')} size={15}/>
    </button>
    {open && position && createPortal(<div ref={popupRef} id={menuId} className={cn('flex animate-[control-popover-in_.14s_cubic-bezier(.2,.8,.2,1)] flex-col gap-1 overflow-auto rounded-xl border border-line-strong bg-popover p-1 text-xs text-secondary shadow-dialog backdrop-blur-xl', scrollbarClass)} role="listbox" aria-label={ariaLabel} style={position}>
      {options.map((option, index) => <button type="button" role="option" aria-selected={option.value === value} disabled={option.disabled} className={cn('flex min-h-10 w-full shrink-0 cursor-pointer items-center justify-between gap-2 rounded-lg border-0 bg-transparent px-3 py-0 text-left text-xs font-medium text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-35', index === activeIndex && 'bg-hover text-ink', option.value === value && '!bg-accent/10 !text-accent-soft-ink font-semibold')} key={option.value} onMouseDown={event => {event.preventDefault(); choose(index)}} onMouseEnter={() => setActiveIndex(index)}>
        <span>{option.label}</span>{option.value === value && <Check size={14}/>}
      </button>)}
    </div>, document.body)}
  </div>
}

interface NumberControlProps {
  value?: number
  defaultValue?: number | string
  onChange?: (value: number) => void
  name?: string
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  placeholder?: string
  ariaLabel?: string
  compact?: boolean
  mixed?: boolean
  onStep?: (direction: 1 | -1) => void
}

export function NumberControl({value, defaultValue = '', onChange, name, min, max, step = 1, disabled = false, placeholder, ariaLabel, compact = false, mixed = false, onStep}: NumberControlProps) {
  const [draft, setDraft] = useState(mixed ? '' : String(value ?? defaultValue))
  const [focused, setFocused] = useState(false)
  useEffect(() => {
    if (focused) return
    if (mixed) setDraft('')
    else if (value !== undefined) setDraft(String(value))
  }, [focused, mixed, value])

  const normalize = (candidate: number) => {
    let next = candidate
    if (min !== undefined) next = Math.max(min, next)
    if (max !== undefined) next = Math.min(max, next)
    const decimals = Math.max(0, String(step).split('.')[1]?.length || 0)
    return Number(next.toFixed(decimals))
  }
  const commit = (candidate: number) => {
    if (!Number.isFinite(candidate)) return
    const next = normalize(candidate)
    setDraft(String(next))
    onChange?.(next)
  }
  const changeDraft = (next: string) => {
    setDraft(next)
    const parsed = Number(next)
    if (next.trim() && Number.isFinite(parsed)) onChange?.(parsed)
  }
  const stepBy = (direction: 1 | -1) => {
    if (mixed && onStep) {
      onStep(direction)
      return
    }
    const parsed = Number(draft)
    const fallback = value ?? (min !== undefined ? min : 0)
    commit((Number.isFinite(parsed) ? parsed : fallback) + direction * step)
  }
  const numeric = Number(draft)
  return <div className={cn('grid min-w-0 overflow-hidden rounded-lg border border-line bg-canvas transition hover:border-line-strong hover:bg-hover focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/15', compact ? 'h-[34px] grid-cols-[minmax(0,1fr)_24px]' : 'h-[38px] grid-cols-[minmax(0,1fr)_30px]', disabled && 'opacity-50')}>
    <input className={cn('min-w-0 border-0 bg-transparent font-mono font-medium text-ink outline-none placeholder:text-disabled disabled:cursor-not-allowed', compact ? 'h-8 px-2 text-[10px]' : 'h-9 px-3 text-[11px]')} type="text" inputMode="decimal" name={name} value={draft} disabled={disabled} placeholder={placeholder} aria-label={ariaLabel} onFocus={() => setFocused(true)} onChange={event => changeDraft(event.target.value)} onKeyDown={event => {if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {event.preventDefault(); stepBy(event.key === 'ArrowUp' ? 1 : -1)}}} onBlur={() => {setFocused(false); if (draft.trim() && Number.isFinite(Number(draft))) commit(Number(draft)); else if (value !== undefined) setDraft(String(value))}}/>
    <span className="grid grid-rows-2 border-l border-line">
      <button className={cn('grid min-h-0 cursor-pointer place-items-center border-0 border-b border-line bg-surface p-0 text-muted transition-colors hover:bg-hover hover:text-accent disabled:cursor-not-allowed disabled:opacity-30', compact ? 'h-4' : 'h-[18px]')} type="button" tabIndex={-1} aria-label={`${ariaLabel || '数值'}增大`} disabled={disabled || (max !== undefined && Number.isFinite(numeric) && numeric >= max)} onMouseDown={event => event.preventDefault()} onClick={() => stepBy(1)}><Plus size={11}/></button>
      <button className={cn('grid min-h-0 cursor-pointer place-items-center border-0 bg-surface p-0 text-muted transition-colors hover:bg-hover hover:text-accent disabled:cursor-not-allowed disabled:opacity-30', compact ? 'h-4' : 'h-[18px]')} type="button" tabIndex={-1} aria-label={`${ariaLabel || '数值'}减小`} disabled={disabled || (min !== undefined && Number.isFinite(numeric) && numeric <= min)} onMouseDown={event => event.preventDefault()} onClick={() => stepBy(-1)}><Minus size={11}/></button>
    </span>
  </div>
}

const COLOR_PRESETS = ['#111111', '#ffffff', '#6f6f68', '#ff6258', '#ffc947', '#00c99d', '#3a9cff', '#8d6cff', '#f06ec7', '#7a4b2b']

interface ColorControlProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  ariaLabel?: string
  compact?: boolean
}

export function ColorControl({value, onChange, disabled = false, ariaLabel, compact = false}: ColorControlProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const position = useFloatingPosition(open, triggerRef, 224, 218)
  useEffect(() => setDraft(value), [value])
  useEffect(() => open ? listenForOutsidePointer(triggerRef, popupRef, () => setOpen(false)) : undefined, [open])
  const applyDraft = () => {
    const normalized = draft.trim().startsWith('#') ? draft.trim() : `#${draft.trim()}`
    if (/^#[0-9a-f]{6}$/i.test(normalized)) onChange(normalized.toLowerCase())
    else setDraft(value)
  }
  return <div className="relative w-full min-w-0">
    <button ref={triggerRef} type="button" className={cn('flex w-full cursor-pointer items-center justify-between rounded-lg border bg-canvas text-ink outline-none transition hover:bg-hover focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-50', compact ? 'h-[34px] px-2 text-[11px]' : 'h-[38px] px-3 text-xs', open ? 'border-accent' : 'border-line hover:border-line-strong')} disabled={disabled} aria-label={`${ariaLabel || '颜色'}，当前 ${value.toUpperCase()}`} title={`${ariaLabel || '颜色'}：${value.toUpperCase()}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(current => !current)}><span aria-hidden="true" className={cn('shrink-0 rounded border-2 border-white/20 shadow-[0_0_0_1px_rgb(0_0_0/.5)]', compact ? 'size-6' : 'size-[26px]')} style={{background:value}}/><ChevronDown aria-hidden="true" className={cn('shrink-0 text-muted transition-transform', open && 'rotate-180 text-accent')} size={13}/></button>
    {open && position && createPortal(<div ref={popupRef} className="animate-[control-popover-in_.14s_cubic-bezier(.2,.8,.2,1)] rounded-xl border border-line-strong bg-popover p-3 text-secondary shadow-dialog backdrop-blur-xl" role="dialog" aria-label={ariaLabel} style={position} onKeyDown={event => {if (event.key === 'Escape') {event.preventDefault(); setOpen(false); triggerRef.current?.focus()}}}>
      <div className="flex items-center gap-3 border-b border-line-subtle pb-3"><span className="size-[34px] rounded-lg border-2 border-white/15" style={{background:value}}/><div className="flex min-w-0 flex-col gap-1"><small className="text-[9px] text-muted">当前颜色</small><strong className="font-mono text-[11px] font-medium text-ink">{value.toUpperCase()}</strong></div></div>
      <div className="grid grid-cols-5 gap-2 py-3">{COLOR_PRESETS.map(color => <button type="button" key={color} className={cn('grid size-[30px] min-h-[30px] cursor-pointer place-items-center rounded-lg border border-white/10 outline-none transition hover:scale-105 focus-visible:ring-2 focus-visible:ring-accent', color.toLowerCase() === value.toLowerCase() && 'ring-2 ring-accent ring-offset-2 ring-offset-panel')} aria-label={color} title={color} style={{background:color}} onClick={() => {onChange(color); setDraft(color)}}>{color.toLowerCase() === value.toLowerCase() && <Check className="rounded-full bg-black/55 p-0.5 text-white" size={16}/>}</button>)}</div>
      <label className="block text-[9px] text-muted">HEX<div className="mt-1 flex h-8 items-center overflow-hidden rounded-lg border border-line bg-canvas focus-within:border-accent"><span className="pl-3 font-mono text-xs text-muted">#</span><input className="h-[30px] min-w-0 flex-1 border-0 bg-transparent px-1.5 font-mono text-[10px] uppercase text-ink outline-none" value={draft.replace(/^#/, '')} maxLength={6} spellCheck={false} onChange={event => setDraft(`#${event.target.value.replace(/[^0-9a-f]/gi, '')}`)} onBlur={applyDraft} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); applyDraft()}}}/></div></label>
    </div>, document.body)}
  </div>
}

interface RangeControlProps {
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  ariaLabel?: string
}

export function RangeControl({value, min, max, step = 1, onChange, ariaLabel}: RangeControlProps) {
  const progress = ((value - min) / Math.max(0.0001, max - min)) * 100
  return <input className="mt-2 h-[22px] w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-progress]:h-1 [&::-moz-range-progress]:rounded-full [&::-moz-range-progress]:bg-accent [&::-moz-range-thumb]:size-3.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-[3px] [&::-moz-range-thumb]:border-accent-strong [&::-moz-range-thumb]:bg-accent-soft-ink [&::-moz-range-thumb]:shadow-[0_1px_3px_rgb(0_0_0/.18)] [&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-line [&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:[background:linear-gradient(90deg,var(--color-accent)_0_var(--range-progress),var(--color-line)_var(--range-progress)_100%)] [&::-webkit-slider-thumb]:mt-[-5px] [&::-webkit-slider-thumb]:size-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-[3px] [&::-webkit-slider-thumb]:border-accent-strong [&::-webkit-slider-thumb]:bg-accent-soft-ink [&::-webkit-slider-thumb]:shadow-[0_1px_3px_rgb(0_0_0/.18)] hover:[&::-webkit-slider-thumb]:scale-[1.13] hover:[&::-webkit-slider-thumb]:shadow-[0_0_0_4px_rgb(16_211_163/.12),0_1px_3px_rgb(0_0_0/.18)]" type="range" min={min} max={max} step={step} value={value} aria-label={ariaLabel} style={{'--range-progress': `${progress}%`} as CSSProperties} onChange={event => onChange(Number(event.target.value))}/>
}

function useFloatingPosition(open: boolean, triggerRef: RefObject<HTMLElement | null>, expectedHeight: number, preferredWidth?: number): CSSProperties | null {
  const [position, setPosition] = useState<CSSProperties | null>(null)
  useLayoutEffect(() => {
    if (!open) {setPosition(null); return}
    const update = () => {
      const element = triggerRef.current
      if (!element) return
      const rect = element.getBoundingClientRect()
      const width = Math.max(rect.width, preferredWidth || rect.width)
      const below = window.innerHeight - rect.bottom - 8
      const above = rect.top - 8
      const flip = below < Math.min(expectedHeight, 170) && above > below
      const maxHeight = Math.max(96, Math.min(expectedHeight, flip ? above - 6 : below - 6))
      const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8)
      setPosition({position:'fixed', zIndex:260, left, width, maxHeight, ...(flip ? {bottom:window.innerHeight - rect.top + 6} : {top:rect.bottom + 6})})
    }
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true)}
  }, [expectedHeight, open, preferredWidth, triggerRef])
  return position
}

function listenForOutsidePointer(triggerRef: RefObject<HTMLElement | null>, popupRef: RefObject<HTMLElement | null>, close: () => void) {
  const listener = (event: PointerEvent) => {
    const target = event.target as Node
    if (!triggerRef.current?.contains(target) && !popupRef.current?.contains(target)) close()
  }
  document.addEventListener('pointerdown', listener)
  return () => document.removeEventListener('pointerdown', listener)
}

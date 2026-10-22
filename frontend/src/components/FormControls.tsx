import {t, useLocale} from '../i18n'
import {Check, ChevronDown, Minus, Plus} from 'lucide-react'
import {CSSProperties, KeyboardEvent, RefObject, useEffect, useId, useLayoutEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {cn, scrollbarClass} from '../ui'

interface SelectOption {
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

export function SelectControl({value, options, onChange, disabled = false, name, placeholder = t("请选择"), ariaLabel, compact = false}: SelectControlProps) {
  useLocale()
  const [open, setOpen] = useState(false)
  const selectedIndex = options.findIndex(option => option.value === value)
  const [activeIndex, setActiveIndex] = useState(Math.max(0, selectedIndex))
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  // Let the list size naturally, including gaps, borders and wrapped labels.
  // Only the menu limit and available viewport space should make it scroll.
  const position = useFloatingPosition(open, triggerRef, 320)
  const positioned = position !== null

  useEffect(() => {
    if (!open) return
    setActiveIndex(Math.max(0, selectedIndex))
    return listenForOutsidePointer(triggerRef, popupRef, () => setOpen(false))
  }, [open, selectedIndex])

  useLayoutEffect(() => {
    if (!open || !positioned) return
    const popup = popupRef.current
    const option = popup?.querySelector<HTMLElement>(`[data-option-index="${activeIndex}"]`)
    if (!popup || !option) return
    // Scroll only the menu. Scrolling its fixed portal must not move the form.
    // Position updates on wheel/scroll deliberately do not retrigger this.
    const top = option.offsetTop
    const bottom = top + option.offsetHeight
    const scrollTop = popup.scrollTop
    const visibleBottom = scrollTop + popup.clientHeight
    if (top < scrollTop) popup.scrollTop = top
    else if (bottom > visibleBottom) popup.scrollTop = bottom - popup.clientHeight
  }, [activeIndex, open, positioned, options.length])

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
    } else if (event.key === 'Escape' && open) {
      event.preventDefault(); event.stopPropagation(); setOpen(false)
    } else if (event.key === 'Home' && open) {
      event.preventDefault(); setActiveIndex(0)
    } else if (event.key === 'End' && open) {
      event.preventDefault(); setActiveIndex(Math.max(0, options.length - 1))
    } else if (event.key === 'Tab') setOpen(false)
  }
  const selected = options[selectedIndex]
  return <div className={cn('relative w-full min-w-0', disabled && 'opacity-50')}>
    {name && <input type="hidden" name={name} value={value}/>}
    <button ref={triggerRef} type="button" className={cn('flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border bg-surface text-left text-ink outline-none motion-control hover:border-line-strong hover:bg-hover focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed', compact ? 'h-[34px] px-2 text-[13px]' : 'h-[38px] px-3 text-sm', open ? 'border-accent ring-3 ring-accent/15' : 'border-line')} disabled={disabled} aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(current => !current)} onKeyDown={onKeyDown}>
      <span className={cn('min-w-0 truncate', !selected && 'text-disabled')} title={selected?.label || placeholder}>{selected?.label || placeholder}</span><ChevronDown className={cn('shrink-0 text-muted motion-chevron', open && 'rotate-180 text-accent')} size={15}/>
    </button>
    {open && position && createPortal(<div ref={popupRef} id={menuId} data-modal-owner={triggerRef.current?.closest('[aria-modal="true"]')?.id} className={cn('flex motion-popover flex-col gap-1 overflow-auto overscroll-contain rounded-xl border border-line-strong bg-popover p-1 text-secondary shadow-dialog backdrop-blur-xl', scrollbarClass)} role="listbox" aria-label={ariaLabel} style={position}>
      {options.map((option, index) => <button type="button" role="option" data-option-index={index} aria-selected={option.value === value} disabled={option.disabled} className={cn('flex min-h-11 w-full shrink-0 cursor-pointer items-center justify-between gap-2 rounded-lg border-0 bg-transparent px-3 py-2 text-left font-medium leading-normal text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-35', index === activeIndex && 'bg-hover text-ink', option.value === value && '!bg-accent-soft !text-accent-soft-ink font-semibold')} style={{fontSize:'inherit',borderRadius:'var(--control-radius)',paddingInline:'var(--control-padding-x)'}} key={option.value} onMouseDown={event => event.preventDefault()} onClick={() => choose(index)} onMouseEnter={() => setActiveIndex(index)}>
        <span className="min-w-0 break-words">{option.label}</span>{option.value === value && <Check className="shrink-0" size={16}/>}
      </button>)}
    </div>, document.body)}
  </div>
}

interface NumberControlProps {
  value?: number
  defaultValue?: number | string
  onChange?: (value: number) => void
  onCommit?: (value: number) => void
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
  commitOnBlur?: boolean
}

export function NumberControl({value, defaultValue = '', onChange, onCommit, name, min, max, step = 1, disabled = false, placeholder, ariaLabel, compact = false, mixed = false, onStep, commitOnBlur = false}: NumberControlProps) {
  useLocale()
  const [draft, setDraft] = useState(mixed ? '' : String(value ?? defaultValue))
  const [focused, setFocused] = useState(false)
  const dirty = useRef(false)
  const controlRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (focused) return
    if (mixed) setDraft('')
    else if (value !== undefined) setDraft(String(value))
  }, [focused, mixed, value])

  useEffect(() => {
    if (!commitOnBlur || !focused) return
    // Canvas pointer handlers can change selection and unmount this input
    // before the browser sends blur. Finish the edit in the capture phase,
    // while its original selection and callback are still available.
    const finishBeforePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !controlRef.current?.contains(event.target)) inputRef.current?.blur()
    }
    document.addEventListener('pointerdown', finishBeforePointer, true)
    return () => document.removeEventListener('pointerdown', finishBeforePointer, true)
  }, [commitOnBlur, focused])

  const normalize = (candidate: number) => {
    let next = candidate
    if (min !== undefined) next = Math.max(min, next)
    if (max !== undefined) next = Math.min(max, next)
    const decimals = Math.max(0, String(step).split('.')[1]?.length || 0)
    return Number(next.toFixed(decimals))
  }
  const commit = (candidate: number) => {
    if (disabled || !Number.isFinite(candidate)) return
    const next = normalize(candidate)
    dirty.current = false
    setDraft(String(next))
    if (mixed || next !== value) onChange?.(next)
    // Explicit intent can matter even when the number stays the same, e.g.
    // keeping text upright at 0 instead of accepting a detected angle later.
    onCommit?.(next)
  }
  const changeDraft = (next: string) => {
    dirty.current = true
    setDraft(next)
    const parsed = Number(next)
    // Drafts such as "0" while entering "0.9" must never reach an API whose
    // minimum is 0.5. Inspector edits commit one complete value per interaction.
    if (!commitOnBlur && next.trim() && Number.isFinite(parsed)
      && (min === undefined || parsed >= min) && (max === undefined || parsed <= max)) onChange?.(parsed)
  }
  const finishDraft = () => {
    if (!dirty.current) return
    if (draft.trim() && Number.isFinite(Number(draft))) commit(Number(draft))
    else {dirty.current = false; setDraft(mixed ? '' : String(value ?? defaultValue))}
  }
  const stepBy = (direction: 1 | -1) => {
    if (mixed && onStep && !draft.trim()) {
      dirty.current = false
      onStep(direction)
      return
    }
    const parsed = draft.trim() ? Number(draft) : NaN
    const fallback = value ?? (min !== undefined ? min : 0)
    commit((Number.isFinite(parsed) ? parsed : fallback) + direction * step)
  }
  const numeric = draft.trim() ? Number(draft) : NaN
  return <div ref={controlRef} className={cn('number-control grid min-w-0 overflow-hidden rounded-lg border border-line bg-surface motion-control hover:border-line-strong hover:bg-hover focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/15', compact ? 'h-[34px] grid-cols-[minmax(0,1fr)_24px]' : 'h-[38px] grid-cols-[minmax(0,1fr)_30px]', disabled && 'opacity-50')}>
    <input ref={inputRef} className={cn('min-w-0 border-0 bg-transparent font-mono font-medium text-ink outline-none placeholder:text-disabled disabled:cursor-not-allowed', compact ? 'h-8 px-2 text-[13px]' : 'h-9 px-3 text-sm')} type="text" inputMode="decimal" name={name} value={draft} disabled={disabled} placeholder={placeholder} aria-label={ariaLabel} onFocus={() => setFocused(true)} onChange={event => changeDraft(event.target.value)} onKeyDown={event => {
      if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {event.preventDefault(); stepBy(event.key === 'ArrowUp' ? 1 : -1)}
      else if (commitOnBlur && event.key === 'Enter') {event.preventDefault(); finishDraft()}
      else if (commitOnBlur && event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); dirty.current = false; setDraft(mixed ? '' : String(value ?? defaultValue))}
    }} onBlur={() => {setFocused(false); finishDraft()}}/>
    <span className="grid grid-rows-2 border-l border-line">
      <button className={cn('grid min-h-0 cursor-pointer place-items-center border-0 border-b border-line bg-surface p-0 text-muted transition-colors hover:bg-hover hover:text-accent disabled:cursor-not-allowed disabled:opacity-30', compact ? 'h-4' : 'h-[18px]')} type="button" tabIndex={-1} aria-label={t("{{v0}}增大", {v0: ariaLabel || t("数值")})} disabled={disabled || (max !== undefined && Number.isFinite(numeric) && numeric >= max)} onMouseDown={event => event.preventDefault()} onClick={() => stepBy(1)}><Plus size={11}/></button>
      <button className={cn('grid min-h-0 cursor-pointer place-items-center border-0 bg-surface p-0 text-muted transition-colors hover:bg-hover hover:text-accent disabled:cursor-not-allowed disabled:opacity-30', compact ? 'h-4' : 'h-[18px]')} type="button" tabIndex={-1} aria-label={t("{{v0}}减小", {v0: ariaLabel || t("数值")})} disabled={disabled || (min !== undefined && Number.isFinite(numeric) && numeric <= min)} onMouseDown={event => event.preventDefault()} onClick={() => stepBy(-1)}><Minus size={11}/></button>
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
  useLocale()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const position = useFloatingPosition(open, triggerRef, 248, 218)
  useEffect(() => setDraft(value), [value])
  useEffect(() => open ? listenForOutsidePointer(triggerRef, popupRef, () => setOpen(false)) : undefined, [open])
  const applyDraft = () => {
    const normalized = draft.trim().startsWith('#') ? draft.trim() : `#${draft.trim()}`
    if (/^#[0-9a-f]{6}$/i.test(normalized)) onChange(normalized.toLowerCase())
    else setDraft(value)
  }
  return <div className="relative w-full min-w-0">
    <button ref={triggerRef} type="button" className={cn('flex w-full cursor-pointer items-center justify-between rounded-lg border bg-surface text-ink outline-none motion-control hover:bg-hover focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-50', compact ? 'h-[34px] px-2 text-[11px]' : 'h-[38px] px-3 text-xs', open ? 'border-accent' : 'border-line hover:border-line-strong')} disabled={disabled} aria-label={t("{{v0}}，当前 {{v1}}", {v0: ariaLabel || t("颜色"), v1: value.toUpperCase()})} title={`${ariaLabel || t("颜色")}：${value.toUpperCase()}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(current => !current)}><span aria-hidden="true" className={cn('shrink-0 rounded border border-line-strong', compact ? 'size-6' : 'size-[26px]')} style={{background:value}}/><ChevronDown aria-hidden="true" className={cn('shrink-0 text-muted motion-chevron', open && 'rotate-180 text-accent')} size={13}/></button>
    {open && position && createPortal(<div ref={popupRef} data-modal-owner={triggerRef.current?.closest('[aria-modal="true"]')?.id} className={cn('motion-popover overflow-y-auto overscroll-contain rounded-xl border border-line-strong bg-popover p-3 text-secondary shadow-dialog backdrop-blur-xl', scrollbarClass)} role="dialog" aria-label={ariaLabel} style={position} onKeyDown={event => {if (event.key === 'Escape') {event.preventDefault(); setOpen(false); triggerRef.current?.focus()}}}>
      <div className="flex items-center gap-3 border-b border-line-subtle pb-3"><span className="size-[34px] rounded-lg border border-line-strong" style={{background:value}}/><div className="flex min-w-0 flex-col gap-1"><small className="text-[13px] text-muted">{t("当前颜色")}</small><strong className="font-mono text-sm font-medium text-ink">{value.toUpperCase()}</strong></div></div>
      <div className="grid grid-cols-5 gap-2 py-3">{COLOR_PRESETS.map(color => <button type="button" key={color} className={cn('grid size-[30px] min-h-[30px] cursor-pointer place-items-center rounded-lg border border-line-strong outline-none motion-swatch focus-visible:ring-2 focus-visible:ring-accent', color.toLowerCase() === value.toLowerCase() && 'ring-2 ring-accent ring-offset-2 ring-offset-panel')} aria-label={color} title={color} style={{background:color}} onClick={() => {onChange(color); setDraft(color)}}>{color.toLowerCase() === value.toLowerCase() && <Check className="rounded-full bg-black/55 p-0.5 text-white" size={16}/>}</button>)}</div>
      <label className="block text-[13px] text-muted">HEX<div className="mt-1 flex h-8 items-center overflow-hidden rounded-lg border border-line bg-surface focus-within:border-accent"><span className="pl-3 font-mono text-sm text-muted">#</span><input className="h-[30px] min-w-0 flex-1 border-0 bg-transparent px-1.5 font-mono text-sm uppercase text-ink outline-none" value={draft.replace(/^#/, '')} maxLength={6} spellCheck={false} onChange={event => setDraft(`#${event.target.value.replace(/[^0-9a-f]/gi, '')}`)} onBlur={applyDraft} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); applyDraft()}}}/></div></label>
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
  useLocale()
  const progress = ((value - min) / Math.max(0.0001, max - min)) * 100
  return <input className="motion-range mt-2 h-[22px] w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-progress]:h-1 [&::-moz-range-progress]:rounded-full [&::-moz-range-progress]:bg-accent [&::-moz-range-thumb]:size-3.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-[3px] [&::-moz-range-thumb]:border-accent-strong [&::-moz-range-thumb]:bg-panel [&::-moz-range-thumb]:shadow-[0_1px_3px_rgb(0_0_0/.18)] [&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-line [&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:[background:linear-gradient(90deg,var(--color-accent)_0_var(--range-progress),var(--color-line)_var(--range-progress)_100%)] [&::-webkit-slider-thumb]:mt-[-5px] [&::-webkit-slider-thumb]:size-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-[3px] [&::-webkit-slider-thumb]:border-accent-strong [&::-webkit-slider-thumb]:bg-panel [&::-webkit-slider-thumb]:shadow-[0_1px_3px_rgb(0_0_0/.18)]" type="range" min={min} max={max} step={step} value={value} aria-label={ariaLabel} style={{'--range-progress': `${progress}%`} as CSSProperties} onChange={event => onChange(Number(event.target.value))}/>
}

function useFloatingPosition(open: boolean, triggerRef: RefObject<HTMLElement | null>, expectedHeight: number, preferredWidth?: number): CSSProperties | null {
  const [position, setPosition] = useState<CSSProperties | null>(null)
  useLayoutEffect(() => {
    if (!open) {setPosition(null); return}
    const update = () => {
      const element = triggerRef.current
      if (!element) return
      const rect = element.getBoundingClientRect()
      const width = Math.min(window.innerWidth - 16, Math.max(rect.width, preferredWidth || rect.width))
      const below = window.innerHeight - rect.bottom - 8
      const above = rect.top - 8
      const flip = below < Math.min(expectedHeight, 170) && above > below
      const maxHeight = Math.max(96, Math.min(expectedHeight, flip ? above - 6 : below - 6))
      const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8)
      // Popovers live under body rather than the field's scoped styles.
      // Carry scoped control tokens across that boundary without changing
      // the compact controls used by the editor and other pages.
      const style = window.getComputedStyle(element)
      const fontSize = style.fontSize || '14px'
      const controlTokens = {
        '--control-radius': style.getPropertyValue('--control-radius').trim() || '8px',
        '--control-padding-x': style.getPropertyValue('--control-padding-x').trim() || '12px',
      }
      setPosition({position:'fixed', zIndex:260, left, width, maxHeight, fontSize, ...controlTokens, ...(flip ? {bottom:window.innerHeight - rect.top + 6} : {top:rect.bottom + 6})})
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

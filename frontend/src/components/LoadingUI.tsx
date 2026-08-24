import {useEffect, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {cn, pageClass} from '../ui'

export function useMinimumLoadingTime(active: boolean, minimumMs = 500) {
  const [visible, setVisible] = useState(active)
  const startedAt = useRef(active ? Date.now() : 0)
  useEffect(() => {
    if (active) {
      startedAt.current = Date.now()
      setVisible(true)
      return
    }
    if (!visible) return
    const remaining = Math.max(0, minimumMs - (Date.now() - startedAt.current))
    const timer = window.setTimeout(() => setVisible(false), remaining)
    return () => window.clearTimeout(timer)
  }, [active, minimumMs, visible])
  return visible
}

export function useDelayedMinimumLoadingTime(active: boolean, delayMs = 180, minimumMs = 240) {
  const [visible, setVisible] = useState(false)
  const shownAt = useRef(0)
  useEffect(() => {
    if (active) {
      if (visible) return
      const timer = window.setTimeout(() => {
        shownAt.current = Date.now()
        setVisible(true)
      }, delayMs)
      return () => window.clearTimeout(timer)
    }
    if (!visible) return
    const remaining = Math.max(0, minimumMs - (Date.now() - shownAt.current))
    const timer = window.setTimeout(() => setVisible(false), remaining)
    return () => window.clearTimeout(timer)
  }, [active, delayMs, minimumMs, visible])
  return visible
}

export function ActivitySpinner({label = '正在处理', size = 20, className}: {label?: string, size?: number, className?: string}) {
  return <span className={cn('inline-flex shrink-0 items-center justify-center', className)} role="status" aria-label={label} style={{width:size, height:size}}><i className="loading-spinner block size-full rounded-full"/><span className="sr-only">{label}</span></span>
}

export function ButtonLoading({label, compact = false}: {label: string, compact?: boolean}) {
  return <span className="inline-flex items-center justify-center gap-1.5" role="status" aria-label={label}><span className="inline-flex h-3 items-center gap-[3px]" aria-hidden="true"><i className="loading-dot size-1 rounded-full bg-current"/><i className="loading-dot size-1 rounded-full bg-current [animation-delay:.14s]"/><i className="loading-dot size-1 rounded-full bg-current [animation-delay:.28s]"/></span>{compact ? <span className="sr-only">{label}</span> : <span>{label}</span>}</span>
}

export function ProgressBar({value, label, detail, className}: {value: number, label?: string, detail?: string, className?: string}) {
  const progress = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0))
  const percent = Math.round(progress * 100)
  return <div className={cn('min-w-0', className)} role="progressbar" aria-label={label || '任务进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
    {(label || detail) && <div className="mb-2 flex items-end justify-between gap-3"><span className="min-w-0 truncate text-[10px] text-secondary">{label}</span><span className="shrink-0 font-mono text-[10px] text-accent">{detail || `${percent}%`}</span></div>}
    <div className="relative h-1.5 overflow-hidden rounded-full bg-raised"><span className="absolute inset-y-0 left-0 rounded-full bg-accent shadow-[0_0_10px_rgb(16_211_163/.35)] transition-[width] duration-300 ease-out" style={{width:`${percent}%`}}/><span className="loading-progress-glint absolute inset-y-0 w-16 bg-gradient-to-r from-transparent via-white/30 to-transparent" style={{left:`max(-4rem,calc(${percent}% - 4rem))`}}/></div>
  </div>
}

export function IndeterminateProgress({label, className}: {label?: string, className?: string}) {
  return <div className={cn('min-w-0', className)} role="status" aria-label={label || '正在处理'}>{label && <span className="mb-2 block text-[10px] text-secondary">{label}</span>}<div className="h-1.5 overflow-hidden rounded-full bg-raised"><span className="loading-progress-indeterminate block h-full w-2/5 rounded-full bg-accent shadow-[0_0_10px_rgb(16_211_163/.3)]"/></div></div>
}

export function CircularProgress({value, size = 42, label, className}: {value: number, size?: number, label?: string, className?: string}) {
  const progress = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0))
  const percent = Math.round(progress * 100)
  const stroke = Math.max(3, Math.round(size * .085))
  const radius = (size - stroke) / 2
  const circumference = radius * 2 * Math.PI
  return <span className={cn('relative inline-grid shrink-0 place-items-center', className)} role="progressbar" aria-label={label || '任务进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} style={{width:size, height:size}}><svg className="-rotate-90" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true"><circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--color-raised)" strokeWidth={stroke}/><circle className="transition-[stroke-dashoffset] duration-300 ease-out" cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--color-accent)" strokeLinecap="round" strokeWidth={stroke} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - progress)}/></svg><strong className="absolute font-mono text-[9px] font-medium text-ink">{percent}</strong></span>
}

export function SkeletonBlock({className}: {className?: string}) {
  return <span aria-hidden="true" className={cn('loading-shimmer relative block overflow-hidden rounded-md bg-raised/75', className)}/>
}

export function PageLoader({label = '正在打开 MangaFlow', detail = '正在准备工作区与核心资源'}: {label?: string, detail?: string}) {
  return <div className="relative grid h-full min-h-[520px] place-items-center overflow-hidden bg-app" role="status" aria-live="polite" aria-label={label}>
    <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_50%_38%,rgb(16_211_163/.16),transparent_30%),linear-gradient(var(--workspace-grid-line)_1px,transparent_1px),linear-gradient(90deg,var(--workspace-grid-line)_1px,transparent_1px)] [background-size:auto,36px_36px,36px_36px]"/>
    <div className="relative flex w-[min(360px,calc(100%-48px))] flex-col items-center text-center"><span className="loading-page-mark grid size-14 place-items-center rounded-2xl bg-accent text-2xl font-extrabold text-accent-ink shadow-[0_0_36px_rgb(16_211_163/.2)]">漫</span><strong className="mt-5 text-sm tracking-[.2px] text-ink">{label}</strong><small className="mt-2 text-[10px] leading-relaxed text-muted">{detail}</small><IndeterminateProgress className="mt-5 w-full"/></div>
  </div>
}

export function ProjectsSkeleton() {
  return <section className={cn(pageClass, 'loading-skeleton-page')} aria-busy="true" aria-label="正在读取项目"><div className="mb-8 flex items-end justify-between"><div className="w-[420px] max-w-[55vw]"><SkeletonBlock className="h-2 w-28"/><SkeletonBlock className="mt-4 h-9 w-64"/><SkeletonBlock className="mt-3 h-3 w-full"/></div><SkeletonBlock className="h-11 w-28"/></div><div className="grid grid-cols-[repeat(auto-fill,minmax(310px,1fr))] gap-5">{[0,1,2].map(item => <div className="overflow-hidden rounded-xl border border-line-subtle bg-panel" key={item}><SkeletonBlock className="h-[150px] rounded-none"/><div className="p-5"><SkeletonBlock className="h-2.5 w-36"/><SkeletonBlock className="mt-4 h-6 w-3/5"/><SkeletonBlock className="mt-3 h-3 w-full"/><SkeletonBlock className="mt-2 h-3 w-4/5"/><div className="mt-5 flex gap-2"><SkeletonBlock className="h-9 w-28"/><SkeletonBlock className="ml-auto size-9"/><SkeletonBlock className="size-9"/></div></div></div>)}</div></section>
}

export function SettingsSkeleton() {
  return <section className={cn(pageClass, 'loading-skeleton-page')} aria-busy="true" aria-label="正在读取设置"><div className="mb-8 flex items-end justify-between"><div><SkeletonBlock className="h-2 w-36"/><SkeletonBlock className="mt-4 h-9 w-24"/><SkeletonBlock className="mt-3 h-3 w-80"/></div><SkeletonBlock className="h-10 w-36"/></div><SkeletonBlock className="mb-4 h-5 w-20"/><div className="grid grid-cols-3 gap-3 max-[1100px]:grid-cols-2">{[0,1,2,3,4,5].map(item => <div className="grid min-h-[164px] grid-cols-[42px_1fr] gap-3 rounded-xl border border-line-subtle bg-panel p-4" key={item}><SkeletonBlock className="size-[38px] rounded-lg"/><div><SkeletonBlock className="h-2 w-20"/><SkeletonBlock className="mt-3 h-4 w-32"/><SkeletonBlock className="mt-3 h-2.5 w-24"/><SkeletonBlock className="mt-5 h-6 w-28"/></div></div>)}</div></section>
}

export function ReviewSkeleton() {
  return <section className={pageClass} aria-busy="true" aria-label="正在执行质量检查"><SkeletonBlock className="h-4 w-24"/><div className="mb-8 mt-8"><SkeletonBlock className="h-2 w-32"/><SkeletonBlock className="mt-4 h-9 w-56"/><SkeletonBlock className="mt-3 h-3 w-96"/></div><div className="grid grid-cols-2 gap-4">{[0,1,2,3].map(item => <div className="overflow-hidden rounded-xl border border-line bg-panel" key={item}><div className="flex justify-between bg-surface p-4"><SkeletonBlock className="h-2.5 w-20"/><SkeletonBlock className="h-2.5 w-12"/></div>{[0,1,2].map(row => <div className="flex gap-3 border-t border-line-subtle p-4" key={row}><SkeletonBlock className="size-5 rounded-full"/><div className="flex-1"><SkeletonBlock className="h-2.5 w-2/5"/><SkeletonBlock className="mt-2 h-2.5 w-4/5"/></div></div>)}</div>)}</div></section>
}

export function EditorSkeleton({canvasControls}: {canvasControls?: ReactNode}) {
  return <div className="loading-skeleton-page grid min-h-0 grid-cols-[208px_minmax(0,1fr)_336px] max-[1200px]:grid-cols-[184px_minmax(0,1fr)_312px]" aria-busy="true" aria-label="正在打开工作台"><aside className="border-r border-line-subtle bg-panel p-3"><SkeletonBlock className="h-3 w-16"/>{[0,1,2].map(item => <div className="mt-4" key={item}><SkeletonBlock className="aspect-[3/4] w-full"/><SkeletonBlock className="mt-2 h-2.5 w-4/5"/><SkeletonBlock className="mt-2 h-2 w-2/5"/></div>)}</aside><main className="relative grid place-items-center bg-[linear-gradient(45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(-45deg,var(--color-panel)_25%,transparent_25%),linear-gradient(45deg,transparent_75%,var(--color-panel)_75%),linear-gradient(-45deg,transparent_75%,var(--color-panel)_75%)] [background-position:0_0,0_10px,10px_-10px,-10px_0] [background-size:20px_20px]">{canvasControls}<div className="w-[min(420px,60%)]"><SkeletonBlock className="aspect-[3/4] w-full rounded-none"/><div className="mt-4 flex justify-center gap-2"><SkeletonBlock className="h-2 w-16"/><SkeletonBlock className="h-2 w-10"/></div></div></main><aside className="border-l border-line-subtle bg-panel p-5"><SkeletonBlock className="h-4 w-24"/>{[0,1,2,3,4,5].map(item => <div className="mt-5" key={item}><SkeletonBlock className="h-2.5 w-20"/><SkeletonBlock className="mt-2 h-10 w-full"/></div>)}</aside></div>
}

export function BlockingLoader({label, detail = '任务完成前暂时不能进行其他操作', progress}: {label: string, detail?: string, progress?: number | null}) {
  const known = typeof progress === 'number' && progress >= 0
  return <div className="fixed inset-0 z-[75] flex cursor-wait items-center justify-center bg-canvas/55 backdrop-blur-[3px]" role="status" aria-live="assertive" aria-label={label}><div className="w-[min(390px,calc(100vw-40px))] rounded-2xl bg-surface p-5 text-ink shadow-dialog"><div className="flex items-center gap-4">{known ? <CircularProgress value={progress} size={48} label={label}/> : <span className="grid size-12 shrink-0 place-items-center rounded-xl bg-accent/10"><ActivitySpinner className="text-accent" size={23} label={label}/></span>}<span className="min-w-0 flex-1"><strong className="block truncate text-xs">{label}</strong><small className="mt-1.5 block text-[10px] leading-relaxed text-muted">{detail}</small></span></div>{known ? <ProgressBar className="mt-4" value={progress} label="整体进度"/> : <IndeterminateProgress className="mt-4"/>}</div></div>
}

export function InlineLoading({label, children}: {label: string, children?: ReactNode}) {
  return <span className="inline-flex items-center gap-2 text-[10px] text-muted"><ActivitySpinner size={14} label={label}/><span>{children || label}</span></span>
}

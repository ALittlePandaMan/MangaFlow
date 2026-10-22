import {AlertTriangle, CheckCircle2, Info, X} from 'lucide-react'
import {createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {t, useLocale} from '../i18n'
import {cn, compactIconButtonClass} from '../ui'

type AlertTone = 'info' | 'success' | 'warning' | 'danger'
interface AlertOptions {
  title?: string
  message: string
  tone?: AlertTone
}
interface AlertMessage extends AlertOptions {
  id: number
  tone: AlertTone
}
interface GlobalAlertApi {
  alert: (input: string | AlertOptions) => number | null
  dismiss: (id: number) => void
}

const GlobalAlertContext = createContext<GlobalAlertApi | null>(null)

export function GlobalAlertProvider({children}: {children: ReactNode}) {
  useLocale()
  const [messages, setMessages] = useState<AlertMessage[]>([])
  const [hidden, setHidden] = useState(() => document.visibilityState === 'hidden')
  const top = useAlertPosition(messages.length > 0)
  const visible = messages.length > 0
  useEffect(() => {
    if (!visible) return
    const update = () => setHidden(document.visibilityState === 'hidden')
    update()
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [visible])
  const nextId = useRef(0)
  const dismiss = useCallback((id: number) => {
    setMessages(current => current.some(item => item.id === id) ? current.filter(item => item.id !== id) : current)
  }, [])
  const alert = useCallback((input: string | AlertOptions) => {
    const options = typeof input === 'string' ? {message:input} : input
    if (!options.message.trim()) return null
    const next = {...options, id:++nextId.current, tone:options.tone || 'info'}
    // Repeated clicks refresh one notification rather than filling the screen.
    setMessages(current => [...current.filter(item => item.message !== next.message || item.tone !== next.tone), next].slice(-3))
    return next.id
  }, [])
  const api = useMemo(() => ({alert, dismiss}), [alert, dismiss])
  return <GlobalAlertContext.Provider value={api}>
    {children}
    {messages.length > 0 && createPortal(<div style={{top,maxHeight:`calc(100dvh - ${top + 12}px)`}} className="global-alerts pointer-events-none fixed inset-x-0 z-[400] flex flex-col items-center gap-2 overflow-y-auto overscroll-contain px-4">
      {messages.map(item => <AlertCard key={item.id} item={item} onDismiss={dismiss} paused={hidden}/>)}
    </div>, document.body)}
  </GlobalAlertContext.Provider>
}

function useAlertPosition(visible: boolean) {
  const [top, setTop] = useState(76)
  useLayoutEffect(() => {
    if (!visible) return
    let observed: Element[] = []
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => measure())
    const measure = () => {
      const header = document.querySelector('.app-header')
      const headerBounds = header?.getBoundingClientRect()
      // Keep notifications just below the main navigation on every page.
      setTop((headerBounds?.height ? headerBounds.bottom : 64) + 12)
      const targets = header ? [header] : []
      if (targets.length !== observed.length || targets.some((item, index) => item !== observed[index])) {
        resize?.disconnect()
        targets.forEach(item => resize?.observe(item))
        observed = targets
      }
    }
    measure()
    // Only watch route roots, not the canvas/region subtree on every edit.
    const routes = document.querySelector('.app-shell > main')
    const navigation = routes ? new MutationObserver(measure) : null
    if (routes) navigation?.observe(routes, {childList:true})
    window.addEventListener('resize', measure)
    return () => {resize?.disconnect(); navigation?.disconnect(); window.removeEventListener('resize', measure)}
  }, [visible])
  return top
}

function AlertCard({item, onDismiss, paused}: {item:AlertMessage, onDismiss:(id:number) => void, paused:boolean}) {
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const duration = item.tone === 'danger' || item.tone === 'warning' || item.message.length > 100 ? 6000 : 3000
  useEffect(() => {
    if (hovered || focused || paused) return
    const timer = window.setTimeout(() => onDismiss(item.id), duration)
    return () => window.clearTimeout(timer)
  }, [duration, focused, hovered, paused, item.id, onDismiss])
  const Icon = item.tone === 'success' ? CheckCircle2 : item.tone === 'info' ? Info : AlertTriangle
  return <div
    className="global-alert pointer-events-auto flex min-h-10 w-max max-w-[min(640px,100%)] shrink-0 items-center gap-2 rounded-[10px] border border-line bg-panel py-1 pl-3 pr-1.5 text-sm text-ink shadow-soft motion-safe:animate-[toast-in_.16s_ease-out]"
    role={item.tone === 'danger' || item.tone === 'warning' ? 'alert' : 'status'}
    aria-label={item.title}
    aria-atomic="true"
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
    onFocusCapture={() => setFocused(true)} onBlurCapture={event => {if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false)}}
  >
    <span aria-hidden="true" className="inline-flex h-8 w-[18px] shrink-0 items-center justify-center self-center leading-none">
      <Icon size={18} className={cn('block shrink-0', item.tone === 'success' ? 'text-success' : item.tone === 'danger' ? 'text-danger' : item.tone === 'warning' ? 'text-warning' : 'text-accent')}/>
    </span>
    <span className="max-h-[30dvh] min-w-0 overflow-y-auto whitespace-pre-wrap break-words py-0.5 leading-6 [overflow-wrap:anywhere]">{item.message}</span>
    <button type="button" className={cn(compactIconButtonClass, 'shrink-0')} aria-label={t('关闭提示')} onClick={() => onDismiss(item.id)}><X aria-hidden="true" size={16}/></button>
  </div>
}

export function useGlobalAlert(): GlobalAlertApi {
  const context = useContext(GlobalAlertContext)
  if (!context) throw new Error('useGlobalAlert must be used inside GlobalAlertProvider')
  return context
}

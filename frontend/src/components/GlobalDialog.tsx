import {AlertTriangle, CheckCircle2, Info, X} from 'lucide-react'
import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {buttonClass, cn, iconButtonClass, primaryButtonClass, scrollbarClass} from '../ui'

export type DialogTone = 'info' | 'success' | 'warning' | 'danger'
export type ContentDialogSize = 'small' | 'medium' | 'large' | 'xlarge'

export interface DialogOptions {
  title?: string
  message: string
  tone?: DialogTone
  confirmLabel?: string
  cancelLabel?: string
}

export interface ContentDialogControls {
  close: () => void
}

export interface ContentDialogOptions {
  title: string
  description?: string
  tone?: DialogTone
  size?: ContentDialogSize
  dismissible?: boolean
  content: ReactNode | ((controls: ContentDialogControls) => ReactNode)
}

type DialogInput = string | DialogOptions
type PromptDialogMode = 'alert' | 'confirm'
type PromptDialogRequest = {
  id: number
  mode: PromptDialogMode
  options: Required<DialogOptions>
  resolve: (accepted: boolean) => void
}
type ContentDialogRequest = {
  id: number
  mode: 'content'
  options: {
    title: string
    description?: string
    tone: DialogTone
    size: ContentDialogSize
    dismissible: boolean
    content: ContentDialogOptions['content']
  }
  resolve: (accepted: boolean) => void
}
type DialogRequest = PromptDialogRequest | ContentDialogRequest

interface GlobalDialogApi {
  isOpen: boolean
  alert: (input: DialogInput) => Promise<void>
  confirm: (input: DialogInput) => Promise<boolean>
  openDialog: (options: ContentDialogOptions) => () => void
  closeDialog: () => void
}

const GlobalDialogContext = createContext<GlobalDialogApi | null>(null)
let nextDialogId = 1

const normalizeOptions = (mode: PromptDialogMode, input: DialogInput): Required<DialogOptions> => {
  const options = typeof input === 'string' ? {message: input} : input
  return {
    title: options.title || (mode === 'alert' ? '提示' : '请确认'),
    message: options.message,
    tone: options.tone || (mode === 'alert' ? 'info' : 'warning'),
    confirmLabel: options.confirmLabel || (mode === 'alert' ? '知道了' : '确认'),
    cancelLabel: options.cancelLabel || '取消',
  }
}

export function GlobalDialogProvider({children}: {children: ReactNode}) {
  const [active, setActive] = useState<DialogRequest | null>(null)
  const activeRef = useRef<DialogRequest | null>(null)
  const queueRef = useRef<DialogRequest[]>([])
  const dialogRef = useRef<HTMLDivElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  const enqueue = useCallback((request: DialogRequest) => {
    if (activeRef.current) {
      queueRef.current.push(request)
      return
    }
    activeRef.current = request
    setActive(request)
  }, [])

  const close = useCallback((accepted: boolean, requestId: number) => {
    const current = activeRef.current
    if (!current || current.id !== requestId) return
    const next = queueRef.current.shift() || null
    activeRef.current = next
    setActive(next)
    current.resolve(accepted)
  }, [])

  const alert = useCallback((input: DialogInput) => new Promise<void>(resolve => {
    enqueue({id:nextDialogId++, mode:'alert', options:normalizeOptions('alert', input), resolve:() => resolve()})
  }), [enqueue])

  const confirm = useCallback((input: DialogInput) => new Promise<boolean>(resolve => {
    enqueue({id:nextDialogId++, mode:'confirm', options:normalizeOptions('confirm', input), resolve})
  }), [enqueue])

  const openDialog = useCallback((options: ContentDialogOptions) => {
    const id = nextDialogId++
    enqueue({
      id,
      mode:'content',
      options:{title:options.title, description:options.description, tone:options.tone || 'info', size:options.size || 'medium', dismissible:options.dismissible ?? true, content:options.content},
      resolve:() => undefined,
    })
    return () => close(false, id)
  }, [close, enqueue])

  const closeDialog = useCallback(() => {
    const current = activeRef.current
    if (current) close(false, current.id)
  }, [close])

  useEffect(() => {
    if (!active) return
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusFrame = requestAnimationFrame(() => {
      const contentTarget = active.mode === 'content'
        ? dialogRef.current?.querySelector<HTMLElement>('[autofocus]') || dialogRef.current?.querySelector<HTMLElement>('[data-dialog-body] input:not([type="hidden"]):not(:disabled), [data-dialog-body] textarea:not(:disabled), [data-dialog-body] button:not(:disabled)')
        : null
      const target = contentTarget || (active.mode === 'confirm' ? cancelRef.current : confirmRef.current) || dialogRef.current?.querySelector<HTMLElement>('[data-dialog-close]')
      target?.focus()
    })
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape') {
        if (active.mode === 'content' && !active.options.dismissible) return
        event.preventDefault()
        close(false, active.id)
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const controls = [...dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not([type="hidden"]):not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])')]
      if (!controls.length) return
      const first = controls[0]
      const last = controls.at(-1)!
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      cancelAnimationFrame(focusFrame)
      document.removeEventListener('keydown', handleKeyDown)
      previouslyFocused?.focus()
    }
  }, [active, close])

  const api = useMemo<GlobalDialogApi>(() => ({isOpen:!!active, alert, confirm, openDialog, closeDialog}), [active, alert, closeDialog, confirm, openDialog])

  return <GlobalDialogContext.Provider value={api}>
    {children}
    {active && createPortal(<div className="fixed inset-0 z-[200] grid animate-[dialog-backdrop-in_.16s_ease-out] place-items-center bg-[rgb(7_9_8/.76)] p-6 backdrop-blur-md" onMouseDown={event => {
      if (event.target === event.currentTarget && (active.mode !== 'content' || active.options.dismissible)) close(false, active.id)
    }}>
      <div ref={dialogRef} className={cn('relative max-h-[calc(100vh-48px)] animate-[dialog-card-in_.2s_cubic-bezier(.2,.8,.2,1)] overflow-hidden rounded-2xl bg-surface text-ink shadow-dialog', active.mode === 'content' ? cn('flex w-[min(620px,calc(100vw-48px))] flex-col', active.options.size === 'small' && 'w-[min(420px,calc(100vw-48px))]', active.options.size === 'large' && 'w-[min(760px,calc(100vw-48px))]', active.options.size === 'xlarge' && 'w-[min(960px,calc(100vw-48px))]') : 'w-[min(440px,calc(100vw-48px))]')} role={active.mode === 'alert' ? 'alertdialog' : 'dialog'} aria-modal="true" aria-labelledby="global-dialog-title" aria-describedby={active.mode === 'content' ? active.options.description ? 'global-dialog-description' : undefined : 'global-dialog-message'}>
        {(active.mode !== 'content' || active.options.dismissible) && <button data-dialog-close className={cn(iconButtonClass, 'absolute right-3 top-3 z-10 border-transparent bg-transparent text-muted hover:translate-y-0 hover:border-line hover:text-ink')} aria-label="关闭" onClick={() => close(false, active.id)}><X size={17}/></button>}
        {active.mode === 'content' ? <>
          <header className="shrink-0 border-b border-line-subtle bg-surface pb-4 pl-6 pr-14 pt-5"><div><span className={cn('mb-1 block font-mono text-[9px] font-medium leading-[normal] tracking-[2px]', toneTextClass(active.options.tone))}>MANGAFLOW · DIALOG</span><h2 className="m-0 text-xl leading-tight" id="global-dialog-title">{active.options.title}</h2>{active.options.description && <p className="mb-0 mt-2 text-[11px] leading-[1.55] text-muted" id="global-dialog-description">{active.options.description}</p>}</div></header>
          <div data-dialog-body className={cn('min-h-0 overflow-auto bg-surface', scrollbarClass)}>{typeof active.options.content === 'function' ? active.options.content({close:() => close(false, active.id)}) : active.options.content}</div>
        </> : <>
          <div className="grid grid-cols-[44px_1fr] items-center gap-4 pb-2 pl-6 pr-14 pt-6"><span className={cn('grid size-11 place-items-center rounded-xl', toneSoftClass(active.options.tone), toneTextClass(active.options.tone))}><DialogIcon tone={active.options.tone}/></span><div><span className={cn('mb-1 block font-mono text-[9px] font-medium leading-[normal] tracking-[2px]', toneTextClass(active.options.tone))}>MANGAFLOW</span><h2 className="m-0 text-xl leading-tight tracking-[-.25px]" id="global-dialog-title">{active.options.title}</h2></div></div>
          <p className="m-0 min-h-[42px] whitespace-pre-wrap pb-6 pl-[84px] pr-6 pt-3 text-xs leading-[1.7] text-secondary" id="global-dialog-message">{active.options.message}</p>
          <div className="flex justify-end gap-2 px-5 pb-5">{active.mode === 'confirm' && <button className={cn(buttonClass, 'min-w-[88px]')} ref={cancelRef} onClick={() => close(false, active.id)}>{active.options.cancelLabel}</button>}<button ref={confirmRef} className={cn(primaryButtonClass, 'min-w-[88px]', toneConfirmClass(active.options.tone))} onClick={() => close(true, active.id)}>{active.options.confirmLabel}</button></div>
        </>}
      </div>
    </div>, document.body)}
  </GlobalDialogContext.Provider>
}

function toneTextClass(tone: DialogTone): string {
  if (tone === 'success') return 'text-success'
  if (tone === 'warning') return 'text-warning'
  if (tone === 'danger') return 'text-danger'
  return 'text-accent'
}

function toneSoftClass(tone: DialogTone): string {
  if (tone === 'success') return 'bg-success/10'
  if (tone === 'warning') return 'bg-warning/10'
  if (tone === 'danger') return 'bg-danger/10'
  return 'bg-accent/10'
}

function toneConfirmClass(tone: DialogTone): string {
  if (tone === 'success') return '!border-success !bg-success hover:!border-success hover:!bg-success/85'
  if (tone === 'warning') return '!border-warning !bg-warning hover:!border-warning hover:!bg-warning/85'
  if (tone === 'danger') return '!border-danger !bg-danger !text-white hover:!border-danger hover:!bg-danger/85'
  return ''
}

export function useGlobalDialog(): GlobalDialogApi {
  const context = useContext(GlobalDialogContext)
  if (!context) throw new Error('useGlobalDialog must be used inside GlobalDialogProvider')
  return context
}

function DialogIcon({tone}: {tone: DialogTone}) {
  if (tone === 'success') return <CheckCircle2 size={21}/>
  if (tone === 'info') return <Info size={21}/>
  return <AlertTriangle size={21}/>
}

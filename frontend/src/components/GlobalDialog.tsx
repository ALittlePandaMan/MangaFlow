import {t, useLocale} from '../i18n'
import {AlertTriangle, CheckCircle2, Info, X} from 'lucide-react'
import {createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {buttonClass, cn, ghostIconButtonClass, primaryButtonClass, scrollbarClass} from '../ui'
import {GlobalAlertProvider} from './GlobalAlert'
import {useModalFocus} from '../hooks/useModalFocus'

type DialogTone = 'info' | 'success' | 'warning' | 'danger'
type ContentDialogSize = 'small' | 'medium' | 'large' | 'xlarge'

export interface DialogOptions {
  title?: string
  message: string
  tone?: DialogTone
  confirmLabel?: string
  cancelLabel?: string
}

interface ContentDialogControls {
  close: () => void
}

interface ContentDialogOptions {
  title: string
  className?: string
  description?: string
  tone?: DialogTone
  size?: ContentDialogSize
  dismissible?: boolean
  content: ReactNode | ((controls: ContentDialogControls) => ReactNode)
}

type DialogInput = string | DialogOptions
type PromptDialogRequest = {
  id: number
  mode: 'confirm'
  options: Required<DialogOptions>
  resolve: (accepted: boolean) => void
}
type ContentDialogRequest = {
  id: number
  mode: 'content'
  options: {
    title: string
    className?: string
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
  confirm: (input: DialogInput) => Promise<boolean>
  openDialog: (options: ContentDialogOptions) => () => void
}

const GlobalDialogContext = createContext<GlobalDialogApi | null>(null)

const normalizeOptions = (input: DialogInput): Required<DialogOptions> => {
  const options = typeof input === 'string' ? {message: input} : input
  return {
    title: options.title || t("请确认"),
    message: options.message,
    tone: options.tone || 'warning',
    confirmLabel: options.confirmLabel || t("确认"),
    cancelLabel: options.cancelLabel || t("取消"),
  }
}

export function GlobalDialogProvider({children}: {children: ReactNode}) {
  return <GlobalAlertProvider><ModalDialogProvider>{children}</ModalDialogProvider></GlobalAlertProvider>
}

function ModalDialogProvider({children}: {children: ReactNode}) {
  useLocale()
  const [active, setActive] = useState<DialogRequest | null>(null)
  const activeRef = useRef<DialogRequest | null>(null)
  const queueRef = useRef<DialogRequest[]>([])
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const dialogId = useId()
  const nextId = useRef(0)
  const mounted = useRef(true)

  const enqueue = useCallback((request: DialogRequest) => {
    if (!mounted.current) {request.resolve(false); return}
    if (activeRef.current) {
      queueRef.current.push(request)
      return
    }
    activeRef.current = request
    setActive(request)
  }, [])

  const close = useCallback((accepted: boolean, requestId: number) => {
    const current = activeRef.current
    if (!current || current.id !== requestId) {
      const index = queueRef.current.findIndex(item => item.id === requestId)
      if (index >= 0) queueRef.current.splice(index, 1)[0].resolve(false)
      return
    }
    const next = queueRef.current.shift() || null
    activeRef.current = next
    setActive(next)
    current.resolve(accepted)
  }, [])

  const confirm = useCallback((input: DialogInput) => new Promise<boolean>(resolve => {
    enqueue({id:++nextId.current, mode:'confirm', options:normalizeOptions(input), resolve})
  }), [enqueue])

  const openDialog = useCallback((options: ContentDialogOptions) => {
    const id = ++nextId.current
    enqueue({
      id,
      mode:'content',
      options:{title:options.title, className:options.className, description:options.description, tone:options.tone || 'info', size:options.size || 'medium', dismissible:options.dismissible ?? true, content:options.content},
      resolve:() => undefined,
    })
    return () => close(false, id)
  }, [close, enqueue])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      // StrictMode immediately remounts effects; only dispose a real unmount.
      queueMicrotask(() => {
        if (mounted.current) return
        activeRef.current?.resolve(false)
        queueRef.current.forEach(request => request.resolve(false))
        activeRef.current = null
        queueRef.current = []
      })
    }
  }, [])

  useModalFocus(dialogRef, {enabled:!!active, focusKey:active?.id,
    initialFocus:() => active?.mode === 'content'
      ? dialogRef.current?.querySelector<HTMLElement>('[data-dialog-body] input:not([type="hidden"]):not(:disabled), [data-dialog-body] textarea:not(:disabled), [data-dialog-body] select:not(:disabled), [data-dialog-body] button:not(:disabled)')
      : cancelRef.current,
    onEscape:() => {if (active && (active.mode === 'confirm' || active.options.dismissible)) close(false, active.id)},
  })

  const api = useMemo<GlobalDialogApi>(() => ({isOpen:!!active, confirm, openDialog}), [active, confirm, openDialog])

  return <GlobalDialogContext.Provider value={api}>
    {children}
    {active && createPortal(<div data-dialog-overlay className="fixed inset-0 z-[200] grid motion-backdrop place-items-center bg-overlay p-6 backdrop-blur-md" onMouseDown={event => {
      if (event.target === event.currentTarget && (active.mode !== 'content' || active.options.dismissible)) close(false, active.id)
    }}>
      <div ref={dialogRef} id={dialogId} tabIndex={-1} className={cn('relative max-h-[calc(100dvh-48px)] motion-dialog overflow-hidden rounded-2xl bg-surface text-ink shadow-dialog', active.mode === 'content' ? cn(active.options.className, 'flex w-[min(620px,calc(100vw-48px))] flex-col', active.options.size === 'small' && 'w-[min(420px,calc(100vw-48px))]', active.options.size === 'large' && 'w-[min(760px,calc(100vw-48px))]', active.options.size === 'xlarge' && 'w-[min(960px,calc(100vw-48px))]') : 'flex min-h-0 w-[min(440px,calc(100vw-48px))] flex-col')} role="dialog" aria-modal="true" aria-labelledby="global-dialog-title" aria-describedby={active.mode === 'content' ? active.options.description ? 'global-dialog-description' : undefined : 'global-dialog-message'}>
        {(active.mode !== 'content' || active.options.dismissible) && <button data-dialog-close className={cn(ghostIconButtonClass, 'absolute right-3 top-3 z-10')} aria-label={t("关闭")} onClick={() => close(false, active.id)}><X aria-hidden="true" size={17}/></button>}
        {active.mode === 'content' ? <>
          <header className="shrink-0 border-b border-line-subtle bg-surface pb-4 pl-6 pr-14 pt-5"><div><span data-dialog-eyebrow className={cn('mb-1 block font-mono text-[9px] font-medium leading-[normal] tracking-[2px]', toneTextClass(active.options.tone))}>MANGAFLOW · DIALOG</span><h2 className="m-0 break-words text-xl leading-tight [overflow-wrap:anywhere]" id="global-dialog-title">{t(active.options.title)}</h2>{active.options.description && <p className="mb-0 mt-2 break-words text-[13px] leading-relaxed text-muted [overflow-wrap:anywhere]" id="global-dialog-description">{t(active.options.description)}</p>}</div></header>
          <div data-dialog-body className={cn('min-h-0 overflow-auto overscroll-contain bg-surface', scrollbarClass)}>{typeof active.options.content === 'function' ? active.options.content({close:() => close(false, active.id)}) : active.options.content}</div>
        </> : <>
          <div className="grid shrink-0 grid-cols-[44px_minmax(0,1fr)] items-center gap-4 pb-2 pl-6 pr-14 pt-6"><span className={cn('grid size-11 place-items-center rounded-xl', toneSoftClass(active.options.tone), toneTextClass(active.options.tone))}><DialogIcon tone={active.options.tone}/></span><div><span className={cn('mb-1 block font-mono text-[9px] font-medium leading-[normal] tracking-[2px]', toneTextClass(active.options.tone))}>MANGAFLOW</span><h2 className="m-0 text-xl leading-tight tracking-[-.25px] [overflow-wrap:anywhere]" id="global-dialog-title">{active.options.title}</h2></div></div>
          <p className="m-0 min-h-0 flex-1 overflow-auto overscroll-contain whitespace-pre-wrap [overflow-wrap:anywhere] pb-6 pl-[84px] pr-6 pt-3 text-sm leading-[1.7] text-secondary" id="global-dialog-message">{active.options.message}</p>
          <div className="flex shrink-0 flex-wrap justify-end gap-2 px-5 pb-5"><button type="button" className={cn(buttonClass, 'min-w-[88px]')} ref={cancelRef} onClick={() => close(false, active.id)}>{active.options.cancelLabel}</button><button type="button" className={cn(primaryButtonClass, 'min-w-[88px]', toneConfirmClass(active.options.tone))} onClick={() => close(true, active.id)}>{active.options.confirmLabel}</button></div>
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
  if (tone === 'success') return '!border-success/40 !bg-success/10 !text-success-soft-ink hover:!border-success hover:!bg-success/20'
  if (tone === 'warning') return '!border-warning/40 !bg-warning/10 !text-warning hover:!border-warning hover:!bg-warning/20'
  if (tone === 'danger') return '!border-danger/40 !bg-danger/10 !text-danger-soft-ink hover:!border-danger hover:!bg-danger/20'
  return ''
}

export function useGlobalDialog(): GlobalDialogApi {
  const context = useContext(GlobalDialogContext)
  if (!context) throw new Error('useGlobalDialog must be used inside GlobalDialogProvider')
  return context
}

function DialogIcon({tone}: {tone: DialogTone}) {
  if (tone === 'success') return <CheckCircle2 aria-hidden="true" size={21}/>
  if (tone === 'info') return <Info aria-hidden="true" size={21}/>
  return <AlertTriangle aria-hidden="true" size={21}/>
}

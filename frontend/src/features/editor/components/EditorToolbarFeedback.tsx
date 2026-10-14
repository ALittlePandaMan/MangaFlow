import {cn} from '../../../ui'

export function EditorToolbarFeedback({error, notice, onDismiss}: {error: string, notice: string, onDismiss: () => void}) {
  const message = error || notice
  if (!message) return null
  const isError = Boolean(error)
  return <div
    className={cn(
      'pointer-events-auto flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg border py-0 pl-3 pr-1.5 text-[12px] shadow-soft backdrop-blur-xl',
      isError ? 'border-danger/20 bg-danger/20 text-danger-soft-ink' : 'border-success/20 bg-success/15 text-success-soft-ink',
    )}
    role={isError ? 'alert' : 'status'}
    title={message}
  >
    <span className="min-w-0 truncate leading-none">{message}</span>
    <button className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-base text-current opacity-70 hover:bg-ink/10 hover:opacity-100" type="button" aria-label="关闭提示" onClick={onDismiss}>×</button>
  </div>
}

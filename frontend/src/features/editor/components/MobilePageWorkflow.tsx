import {RotateCcw, type LucideIcon} from 'lucide-react'
import {ActivitySpinner} from '../../../components/LoadingUI'
import {cn} from '../../../ui'
import type {PageWorkflowStage} from '../pageWorkflow'

interface WorkflowAction {
  stage: PageWorkflowStage
  label: string
  title: string
  icon: LucideIcon
  disabled: boolean
}

interface Props {
  actions: WorkflowAction[]
  recommended: PageWorkflowStage | null
  disabled: boolean
  running: PageWorkflowStage | null
  resetting: boolean
  resetDisabled: boolean
  onRun: (stage: PageWorkflowStage) => void
  onReset: () => void
}

const shortLabels: Record<PageWorkflowStage, string> = {
  ocr: 'OCR', translation: '翻译', inpainting: '修复', rendering: '排版',
}

const actionClass = 'flex h-[52px] min-w-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border px-1 text-xs font-medium leading-none outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40'

// Mounted only below 640px. Desktop/tablet keep their existing workflow buttons.
export function MobilePageWorkflow({actions, recommended, disabled, running, resetting, resetDisabled, onRun, onReset}: Props) {
  const busy = disabled || !!running || resetting
  return <div className="mobile-page-workflow grid min-w-0 grid-cols-5 gap-1.5" role="group" aria-label="当前页处理流程">
    {actions.map(({stage, label, title, icon: Icon, disabled: unavailable}) => <button
      key={stage}
      type="button"
      aria-label={label}
      aria-busy={running === stage || undefined}
      title={title}
      disabled={busy || unavailable}
      className={cn(actionClass, stage === recommended
        ? 'border-accent-line bg-accent-soft text-accent-soft-ink enabled:hover:bg-accent-soft-hover enabled:active:bg-accent-soft-hover'
        : 'border-line-subtle bg-surface text-secondary enabled:hover:bg-hover enabled:active:bg-hover')}
      onClick={() => onRun(stage)}
    >
      {running === stage ? <ActivitySpinner size={18} label={`${shortLabels[stage]}处理中…`}/> : <Icon aria-hidden="true" size={18}/>}
      <span>{shortLabels[stage]}</span>
    </button>)}
    <button
      type="button"
      aria-label="重置当前页"
      aria-busy={resetting || undefined}
      title="重置当前页，清除处理结果前会再次确认"
      disabled={busy || resetDisabled}
      className={cn(actionClass, 'border-line-subtle bg-surface text-danger-soft-ink enabled:hover:bg-danger/10 enabled:active:bg-danger/10')}
      onClick={onReset}
    >
      {resetting ? <ActivitySpinner size={18} label="恢复中…"/> : <RotateCcw aria-hidden="true" size={18}/>}
      <span>重置</span>
    </button>
  </div>
}

import {buttonClass} from '../../../ui'
import type {ImagePage} from '../../../types'

export function ClassificationStatus({summary, busy, onRetry}: {summary:ImagePage['text_classification']; busy:boolean; onRetry:() => void}) {
  if (summary?.status !== 'unavailable') return null
  return <div role="alert" className="absolute bottom-4 left-1/2 z-20 flex max-w-[calc(100%-2rem)] -translate-x-1/2 items-center gap-3 rounded-xl border border-danger/20 bg-canvas px-4 py-3 text-sm shadow-panel">
    <span>文字分类未执行，已保留所有候选。请检查模型安装后重试。</span>
    <button type="button" className={`${buttonClass} shrink-0`} disabled={busy} onClick={onRetry}>重试分类</button>
  </div>
}

import type {ImagePage, ProcessingTask, TextRegion} from '../../../types'
import type {PageWorkflowStage} from '../pageWorkflow'

export function blurActiveControl() {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

export function pageTaskBusyLabel(task: ProcessingTask | null, requestedStage: PageWorkflowStage | null): string {
  if (task?.current_stage === 'translation' && task.message.startsWith('翻译前自动识别')) return task.message
  if (!task?.current_stage) return ({
    ocr: '正在重新 OCR 当前页…',
    translation: '正在重新翻译当前页…',
    inpainting: '正在重新修复当前页…',
    rendering: '正在重新排版当前页…',
  } as Record<PageWorkflowStage, string>)[requestedStage || 'ocr']
  if (task.current_stage === 'detection') return '正在检测文字区域…'
  if (task.current_stage === 'ocr') {
    if (task.message.startsWith('Loading OCR model')) return '检测完成，正在加载 OCR 模型…'
    const progress = task.message.match(/OCR\s+(\d+)\/(\d+)/)
    const scheduling = task.message.includes(' · ') ? ` · ${task.message.split(' · ').slice(1).join(' · ')}` : ''
    return progress ? `正在 OCR 识别（${progress[1]}/${progress[2]}）${scheduling}…` : '检测完成，正在 OCR 识别…'
  }
  return ({
    translation: '正在翻译已确认区域…',
    mask: '正在生成文字 Mask…',
    inpainting: '正在清除文字并修复背景…',
    rendering: '正在生成译文排版…',
  } as Record<string, string>)[task.current_stage] || '正在处理当前页…'
}

export function pageWorkflowRank(page: ImagePage, regions: TextRegion[]): number {
  const ranks: Record<string, number> = {
    UPLOADED: 0, DETECTING: 0, DETECTED: 0, OCR_RUNNING: 0,
    OCR_DONE: 1, TRANSLATING: 1,
    TRANSLATED: 2, MASK_GENERATING: 2, INPAINTING: 2,
    INPAINTED: 3, LAYOUTING: 3, RENDERING: 3,
    COMPLETED: 4, NEEDS_REVIEW: 4,
  }
  if (page.status !== 'FAILED') return ranks[page.status] ?? 0
  if (page.rendered_url || page.text_layer_url) return 4
  if (page.clean_url) return 3
  const recognized = regions.filter(region => region.source_text.trim())
  if (recognized.length && recognized.every(region => region.translated_text.trim())) return 2
  if (recognized.length) return 1
  return 0
}

export function pageNeedsOcr(page: ImagePage): boolean {
  return !page.ocr_exempt && ['UPLOADED', 'DETECTED', 'FAILED'].includes(page.status)
}

export function pageState(page: ImagePage): {className: string, label: string, emphasized?: boolean} {
  if (page.ocr_exempt) return {className: 'text-success-soft-ink', label: '已翻译', emphasized: true}
  const status = page.status === 'NEEDS_REVIEW' ? 'COMPLETED' : page.status
  const labels: Record<string, string> = {
    UPLOADED: '未识别', DETECTING: '检测中', DETECTED: '待识别', OCR_RUNNING: '识别中', OCR_DONE: '待确认',
    TRANSLATING: '翻译中', TRANSLATED: '已翻译', MASK_GENERATING: '生成 Mask', INPAINTING: '修复中',
    INPAINTED: '已修复', LAYOUTING: '排版中', RENDERING: '渲染中', COMPLETED: '已完成', FAILED: '失败',
  }
  const translated = status === 'TRANSLATED'
  const className = status === 'FAILED' ? 'text-danger' : status === 'COMPLETED' ? 'text-accent' : translated ? 'text-success-soft-ink' : 'text-muted'
  return {className, label: labels[status] || status, emphasized: translated}
}

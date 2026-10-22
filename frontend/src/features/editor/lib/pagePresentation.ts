import {t} from '../../../i18n'
import type {ImagePage, ProcessingTask} from '../../../types'
import type {PageWorkflowStage} from '../pageWorkflow'

export function blurActiveControl() {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

export function pageTaskBusyLabel(task: ProcessingTask | null, requestedStage: PageWorkflowStage | null): string {
  if (task?.waiting_reason === 'previous_page_translation') return t("等待前页翻译，不占用处理名额…")
  if (task?.waiting_reason === 'previous_page_paused') return t("前页已暂停，请先恢复前页任务…")
  if (task?.active_stages && task.active_stages.length > 1) return t("正在并行处理：{{v0}}…", {v0: task.active_stages.map(stageLabel).join('、')})
  if (task?.current_stage === 'translation' && task.message.startsWith('翻译前自动识别')) return task.message
  if (!task?.current_stage) return ({
    ocr: t("正在重新检测当前页并执行 OCR…"),
    translation: t("正在重新翻译当前页…"),
    inpainting: t("正在重新修复当前页…"),
  } as Record<PageWorkflowStage, string>)[requestedStage || 'ocr']
  if (task.current_stage === 'detection') return t("正在检测文字区域…")
  if (task.current_stage === 'ocr') {
    if (task.message.startsWith('Loading OCR model')) return t("检测完成，正在加载 OCR 模型…")
    const progress = task.message.match(/OCR\s+(\d+)\/(\d+)/)
    const scheduling = task.message.includes(' · ') ? ` · ${task.message.split(' · ').slice(1).join(' · ')}` : ''
    return progress ? t("正在 OCR 识别（{{v0}}/{{v1}}）{{v2}}…", {v0: progress[1], v1: progress[2], v2: scheduling}) : t("检测完成，正在 OCR 识别…")
  }
  return ({
    translation: t("正在翻译已确认区域…"),
    mask: t("正在生成文字 Mask…"),
    inpainting: t("正在清除文字并修复背景…"),
    rendering: t("正在生成译文排版…"),
  } as Record<string, string>)[task.current_stage] || t("正在处理当前页…")
}

export function stageLabel(stage: string): string {
  return ({detection:t("检测"),ocr:'OCR',translation:t("翻译"),mask:t("蒙版"),inpainting:t("修复"),rendering:t("排版")} as Record<string, string>)[stage] || stage
}

export function pageNeedsOcr(page: ImagePage): boolean {
  return !page.ocr_exempt && ['UPLOADED', 'DETECTED', 'FAILED'].includes(page.status)
}

export function pageState(page: ImagePage): {className: string, label: string, emphasized?: boolean} {
  if (page.ocr_exempt) return {className: 'text-success-soft-ink', label: t("已翻译"), emphasized: true}
  const status = page.status === 'NEEDS_REVIEW' ? 'COMPLETED' : page.status
  const labels: Record<string, string> = {
    UPLOADED: t("未识别"), DETECTING: t("检测中"), DETECTED: t("待识别"), OCR_RUNNING: t("识别中"), OCR_DONE: t("待确认"),
    TRANSLATING: t("翻译中"), TRANSLATED: t("已翻译"), MASK_GENERATING: t("生成 Mask"), INPAINTING: t("修复中"),
    INPAINTED: t("已修复"), LAYOUTING: t("排版中"), RENDERING: t("渲染中"), COMPLETED: t("已完成"), FAILED: t("失败"),
  }
  const translated = status === 'TRANSLATED'
  const className = status === 'FAILED' ? 'text-danger' : status === 'COMPLETED' ? 'text-accent' : translated ? 'text-success-soft-ink' : 'text-muted'
  return {className, label: labels[status] || status, emphasized: translated}
}

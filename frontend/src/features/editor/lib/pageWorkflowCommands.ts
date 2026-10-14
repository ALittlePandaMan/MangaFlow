import type {ImagePage, ProcessingTask, TextRegion, ViewMode} from '../../../types'
import type {DialogOptions} from '../../../components/GlobalDialog'
import type {TaskUpdateHandler} from '../hooks/useTaskMonitor'
import {api} from '../../../services/api'
import {useEditorStore} from '../store'
import {buildPageWorkflowRequest, canRunPageWorkflowStage, lockedLegacyOcrRegionCount, pageOcrGroupingUnavailable, pageOcrNeedsDetection, pageWorkflowTargetView, type PageWorkflowStage} from '../pageWorkflow'
import {classificationNeedsDetection} from './textClassification'
import {blurActiveControl, pageWorkflowRank} from './pagePresentation'

export interface PageWorkflowContext {
  getPage: () => ImagePage | null
  getRegions: () => TextRegion[]
  currentPageId: () => string | null | undefined
  isBusy: () => boolean
  confirmDialog: (options: DialogOptions) => Promise<boolean>
  setView: (view: ViewMode) => void
  setNotice: (value:string) => void
  setError: (value:string) => void
  setSchedulingProcess: (value:boolean) => void
  setRunningWorkflowStage: (value:PageWorkflowStage | null) => void
  setRunningPageTask: (value:ProcessingTask | null) => void
  flushPendingRegions: () => Promise<void>
  refreshAfterTask: (id:string, onUpdate?:TaskUpdateHandler) => Promise<{task:ProcessingTask; regions:TextRegion[]} | null>
  syncTaskProgress: TaskUpdateHandler
}

export function createPageWorkflowCommands(ctx:PageWorkflowContext) {
  const {confirmDialog,setView,setNotice,setError,setSchedulingProcess,setRunningWorkflowStage,setRunningPageTask,flushPendingRegions,refreshAfterTask,syncTaskProgress}=ctx
  const processPageStage = async (stage: PageWorkflowStage) => {
    const page=ctx.getPage()
    if (!page || ctx.isBusy()) return
    // Capture the originating view before the workflow switches to clean view.
    const repairGeometry = useEditorStore.getState().view === 'translated' ? 'translated' : 'source'
    const rank = pageWorkflowRank(page, ctx.getRegions())
    if (!canRunPageWorkflowStage(stage, rank)) return
    const currentRegions = ctx.getRegions()
    const lockedLegacyCount = stage === 'ocr' ? lockedLegacyOcrRegionCount(currentRegions) : 0
    if (lockedLegacyCount > 0) {
      setNotice('')
      setError(`当前页有 ${lockedLegacyCount} 个锁定的旧版自动区域。请先解锁这些区域，再重新 OCR，系统才能安全地按气泡合并。`)
      return
    }
    let classificationChanged = false
    if (stage === 'ocr') {
      // Only a whole-page OCR action checks this policy. Dragging, typing and
      // region-only OCR never fetch settings or re-run classification.
      try {
        const models = await api.models.list()
        const detection = models?.configured.find(item => item.kind === 'detection' && item.enabled && item.is_default)
        classificationChanged = detection?.provider === 'paddleocr' && classificationNeedsDetection(page.text_classification, detection.config)
      } catch {
        // A settings read failure must not prevent ordinary OCR.
      }
      if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    }
    const startsWithDetection = stage === 'ocr' && (pageOcrNeedsDetection(currentRegions) || classificationChanged)
    if (startsWithDetection && currentRegions.length > 0 && !await confirmDialog({
      title: classificationChanged ? '重新检测并应用文字分类？' : '重新检测并合并旧文字区域？',
      message: classificationChanged ? '当前页尚未使用最新的文字分类设置。重新检测会应用拟声词过滤或恢复被过滤的候选，并替换未锁定的自动区域；其原文、译文、样式会重建，净图与译图也会失效。手工区域和锁定区域保留。' : '这页包含尚未经过气泡归属的旧版自动检测区域。系统会替换未锁定的自动区域，并重新执行气泡合并与 OCR；这些区域已有的原文、译文、样式和编辑历史会被重建，当前净图与译图也会失效。手工区域和锁定区域不会删除，但锁定的旧区域不会参与合并。',
      tone: 'info',
      confirmLabel: '重新检测并 OCR',
    })) return
    if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    const targetView = pageWorkflowTargetView(stage)
    if (targetView) setView(targetView)
    const request = buildPageWorkflowRequest(stage, startsWithDetection)
    if (stage === 'inpainting') request.options = {...request.options, repair_geometry: repairGeometry}
    const startingNotices: Record<PageWorkflowStage, string> = {
      ocr: startsWithDetection ? '正在重新检测文字区域、合并同气泡文本并执行 OCR…' : '正在重新 OCR 当前页的文字区域…',
      translation: '正在重新翻译当前页…',
      inpainting: '正在重新生成文字 Mask、修复背景并自动重新排版…',
      rendering: '正在根据当前译文重新排版…',
    }
    const completedNotices: Record<PageWorkflowStage, string> = {
      ocr: startsWithDetection ? '重新检测与 OCR 已完成，请核对合并后的文字区域和原文；确认无误后再执行重新翻译。' : 'OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。',
      translation: '当前页翻译已完成。',
      inpainting: '背景修复与重新排版已完成。',
      rendering: '当前页重新排版已完成。',
    }
    blurActiveControl()
    setSchedulingProcess(true)
    setRunningWorkflowStage(stage)
    setRunningPageTask(null)
    setError('')
    setNotice(startingNotices[stage])
    try {
      await flushPendingRegions()
      const task = await api.images.process(page.id, request)
      setRunningPageTask(task)
      const result = await refreshAfterTask(task.id, syncTaskProgress)
      if (result?.task.status === 'COMPLETED') {
        if (targetView) setView(targetView)
        if (stage === 'ocr' && startsWithDetection && pageOcrGroupingUnavailable(result.regions)) {
          setNotice('')
          setError('本次 OCR 已完成，但气泡合并未执行：气泡检测模型不可用或运行失败，文字区域仍保持分开。请检查文字检测模型后重新 OCR。')
        } else {
          setNotice(completedNotices[stage])
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSchedulingProcess(false)
      setRunningWorkflowStage(null)
      setRunningPageTask(null)
    }
  }

  const retryClassification = async () => {
    const page=ctx.getPage()
    if (!page || ctx.isBusy()) return
    const pageId = page.id
    try {
      const models = await api.models.list()
      const detection = models.configured.find(item => item.kind === 'detection' && item.provider === 'paddleocr' && item.enabled && item.is_default)
      if (!detection) throw new Error('请先在设置中启用 Paddle 文字检测')
      await api.models.retryClassification(detection.id)
      if (ctx.currentPageId() === pageId && !ctx.isBusy()) await processPageStage('ocr')
    } catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
  }

  return {processPageStage,retryClassification}
}

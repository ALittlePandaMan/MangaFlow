import type {ImagePage, ProcessingTask, TextRegion, ViewMode} from '../../../types'
import type {DialogOptions} from '../../../components/GlobalDialog'
import type {TaskUpdateHandler} from '../hooks/useTaskMonitor'
import {api} from '../../../services/api'
import {useEditorStore} from '../store'
import {buildPageWorkflowRequest, canRunPageWorkflowStage, pageOcrNeedsDetection, pageWorkflowTargetView, unifiedLayoutNeedsDetection, type PageWorkflowStage} from '../pageWorkflow'
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
    let unifiedLayout = false, layoutChanged = false
    if (stage === 'ocr') {
      // Only a whole-page OCR action checks this policy. Dragging, typing and
      // region-only OCR never fetch settings or re-run classification.
      try {
        const models = await api.models.list()
        const detection = models?.configured.find(item => item.kind === 'detection' && item.enabled && item.is_default)
        unifiedLayout = detection?.provider === 'rfdetr'
        layoutChanged = !!detection && unifiedLayout && unifiedLayoutNeedsDetection(currentRegions,detection.config)
      } catch {
        // A settings read failure must not prevent ordinary OCR.
      }
      if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    }
    const startsWithDetection = stage === 'ocr' && (pageOcrNeedsDetection(currentRegions) || layoutChanged)
    if (startsWithDetection && currentRegions.length > 0 && !await confirmDialog({
      title: unifiedLayout ? '使用 RF-DETR 重新检测？' : '重新检测文字区域？',
      message: '将重新检测文字区域。未锁定的自动框及其原文、译文、样式会重建，净图与译图会失效。手工区域和锁定区域保留。RF-DETR 会关联文字、气泡和分镜，不强制合并同气泡文字。',
      tone: 'info',
      confirmLabel: '重新检测并 OCR',
    })) return
    if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    const targetView = pageWorkflowTargetView(stage)
    if (targetView) setView(targetView)
    const request = buildPageWorkflowRequest(stage, startsWithDetection)
    if (stage === 'inpainting') request.options = {...request.options, repair_geometry: repairGeometry}
    const startingNotices: Record<PageWorkflowStage, string> = {
      ocr: startsWithDetection ? unifiedLayout ? '正在使用 RF-DETR 检测文字、关联气泡并执行 OCR…' : '正在重新检测文字区域并执行 OCR…' : '正在重新 OCR 当前页的文字区域…',
      translation: '正在重新翻译当前页…',
      inpainting: '正在重新生成文字 Mask、修复背景并自动重新排版…',
      rendering: '正在根据当前译文重新排版…',
    }
    const completedNotices: Record<PageWorkflowStage, string> = {
      ocr: startsWithDetection ? unifiedLayout ? 'RF-DETR 检测与 OCR 已完成，文字框保持独立；请核对区域和原文后再翻译。' : '重新检测与 OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。' : 'OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。',
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
        setNotice(completedNotices[stage])
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
      const detection = models.configured.find(item => item.kind === 'detection' && item.provider === 'rfdetr' && item.enabled && item.is_default)
      if (!detection) throw new Error('请先在设置中启用 RF-DETR 版面检测模型')
      await api.models.retryClassification(detection.id)
      if (ctx.currentPageId() === pageId && !ctx.isBusy()) await processPageStage('ocr')
    } catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
  }

  return {processPageStage,retryClassification}
}

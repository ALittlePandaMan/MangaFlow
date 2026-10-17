import type {ImagePage, ProcessingTask, TextRegion, ViewMode} from '../../../types'
import type {DialogOptions} from '../../../components/GlobalDialog'
import type {TaskUpdateHandler} from '../hooks/useTaskMonitor'
import {api} from '../../../services/api'
import {useEditorStore} from '../store'
import {buildPageWorkflowRequest, canRunPageWorkflowStage, pageWorkflowTargetView, type PageWorkflowStage} from '../pageWorkflow'
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
    if (stage === 'ocr' && currentRegions.length > 0 && !await confirmDialog({
      title: '重新检测？',
      message: '将按当前检测设置重新检测文字区域，再执行 OCR。未锁定的自动框及其原文、译文、样式会重建，净图与译图会失效。手工区域和锁定区域保留。',
      tone: 'info',
      confirmLabel: '重新检测',
    })) return
    if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    const targetView = pageWorkflowTargetView(stage)
    if (targetView) setView(targetView)
    const request = buildPageWorkflowRequest(stage)
    if (stage === 'inpainting') request.options = {...request.options, repair_geometry: repairGeometry}
    const startingNotices: Record<PageWorkflowStage, string> = {
      ocr: '正在重新检测文字区域并执行 OCR…',
      translation: '正在重新翻译当前页…',
      inpainting: '正在重新生成文字 Mask、修复背景并自动重新排版…',
      rendering: '正在根据当前译文重新排版…',
    }
    const completedNotices: Record<PageWorkflowStage, string> = {
      ocr: '重新检测与 OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。',
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

import type {ImagePage, ProcessingTask, TextRegion, ViewMode} from '../../../types'
import type {DialogOptions} from '../../../components/GlobalDialog'
import type {TaskUpdateHandler} from '../hooks/useTaskMonitor'
import {api} from '../../../services/api'
import {useEditorStore} from '../store'
import {buildPageWorkflowRequest, canRunPageWorkflowStage, pageWorkflowTargetView, type PageWorkflowStage} from '../pageWorkflow'
import {blurActiveControl} from './pagePresentation'

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
  onStart?: () => void
}

export function createPageWorkflowCommands(ctx:PageWorkflowContext) {
  const {confirmDialog,setView,setNotice,setError,setSchedulingProcess,setRunningWorkflowStage,setRunningPageTask,flushPendingRegions,refreshAfterTask,syncTaskProgress}=ctx
  const processPageStage = async (stage: PageWorkflowStage) => {
    const page=ctx.getPage()
    if (!page || ctx.isBusy()) return
    // Capture the originating view before the workflow switches to clean view.
    const repairGeometry = useEditorStore.getState().view === 'translated' ? 'translated' : 'source'
    const currentRegions = ctx.getRegions()
    if (!canRunPageWorkflowStage(stage, currentRegions.length)) return
    if (stage === 'ocr' && currentRegions.length > 0 && !await confirmDialog({
      title: '重新检测？',
      message: '将重新检测整张图片并执行 OCR。所有现有文本框（包括手工创建、调整过和锁定的框）及其原文、译文、样式和蒙版都会替换，净图与译图会失效。',
      tone: 'info',
      confirmLabel: '重新检测',
    })) return
    if (ctx.currentPageId() !== page.id || ctx.isBusy()) return
    ctx.onStart?.()
    const targetView = pageWorkflowTargetView(stage)
    if (targetView) setView(targetView)
    const request = buildPageWorkflowRequest(stage)
    if (stage === 'inpainting') request.options = {...request.options, repair_geometry: repairGeometry}
    const startingNotices: Record<PageWorkflowStage, string> = {
      ocr: '正在重新检测文字区域并执行 OCR…',
      translation: '正在重新翻译当前页…',
      inpainting: '正在重新生成文字 Mask、修复背景并自动重新排版…',
    }
    const completedNotices: Record<PageWorkflowStage, string> = {
      ocr: '重新检测与 OCR 已完成，请核对文字区域和原文；确认无误后再执行重新翻译。',
      translation: '当前页翻译已完成。',
      inpainting: '背景修复与重新排版已完成。',
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
      if (result?.task.status === 'COMPLETED' && ctx.currentPageId() === page.id) {
        // The user can browse other views while inference runs. Do not reset
        // their newer view choice when the background task completes.
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

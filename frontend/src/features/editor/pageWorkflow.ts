import type {ViewMode} from '../../types'

export type PageWorkflowStage = 'ocr' | 'translation' | 'inpainting'

export function canRunPageWorkflowStage(stage: PageWorkflowStage, regionCount: number): boolean {
  if (stage === 'ocr') return true
  // Actual frames authorize these workflows, not an old page-status snapshot.
  // Translation already OCRs empty originals; repair only needs frame geometry.
  return (stage === 'translation' || stage === 'inpainting') && regionCount > 0
}

type PageWorkflowRequest = {
  start_stage: string
  end_stage: string
  force: boolean
  options: Record<string, unknown>
}

export function pageWorkflowTargetView(stage: PageWorkflowStage): ViewMode | null {
  if (stage === 'translation') return 'translated'
  if (stage === 'inpainting') return 'clean'
  return null
}

export function buildPageWorkflowRequest(stage: PageWorkflowStage): PageWorkflowRequest {
  if (stage === 'ocr') return {
    start_stage: 'detection',
    end_stage: 'ocr',
    force: true,
    options: {crop_padding: 4},
  }
  if (stage === 'translation') return {
    start_stage: 'translation',
    end_stage: 'translation',
    force: true,
    options: {},
  }
  if (stage === 'inpainting') return {
    start_stage: 'mask',
    end_stage: 'rendering',
    force: true,
    options: {rebuild_clean: true},
  }
  throw new Error(`不支持的页面处理操作：${stage}`)
}

import type {ViewMode} from '../../types'

export type PageWorkflowStage = 'ocr' | 'translation' | 'inpainting' | 'rendering'

const PAGE_WORKFLOW_REQUIRED_RANK: Record<PageWorkflowStage, number> = {
  ocr: 0,
  translation: 1,
  // Background repair only needs OCR geometry, so it can run independently
  // from translation.
  inpainting: 1,
  rendering: 3,
}

export function canRunPageWorkflowStage(stage: PageWorkflowStage, workflowRank: number): boolean {
  return workflowRank >= PAGE_WORKFLOW_REQUIRED_RANK[stage]
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
  return {
    start_stage: 'rendering',
    end_stage: 'rendering',
    force: true,
    options: {},
  }
}

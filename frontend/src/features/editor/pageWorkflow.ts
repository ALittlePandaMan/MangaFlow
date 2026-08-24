import type {ViewMode} from '../../types'

export type PageWorkflowStage = 'ocr' | 'translation' | 'inpainting' | 'rendering'

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

export function buildPageWorkflowRequest(stage: PageWorkflowStage, startsWithDetection: boolean): PageWorkflowRequest {
  if (stage === 'ocr') return {
    start_stage: startsWithDetection ? 'detection' : 'ocr',
    end_stage: 'ocr',
    force: !startsWithDetection,
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

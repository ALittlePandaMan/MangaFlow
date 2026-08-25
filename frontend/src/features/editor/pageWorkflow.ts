import type {TextRegion, ViewMode} from '../../types'

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

type OcrRegionLayout = Pick<TextRegion, 'layout_data'>
type OcrDetectionRegion = OcrRegionLayout & Pick<TextRegion, 'locked'>

export function pageOcrNeedsDetection(regions: OcrDetectionRegion[]): boolean {
  if (regions.length === 0) return true
  return regions.some(region => !region.locked && isLegacyAutomaticRegion(region))
}

export function lockedLegacyOcrRegionCount(regions: OcrDetectionRegion[]): number {
  return regions.filter(region => region.locked && isLegacyAutomaticRegion(region)).length
}

export function pageOcrGroupingUnavailable(regions: OcrRegionLayout[]): boolean {
  return regions.some(region => balloonAssignment(region)?.status === 'unavailable')
}

function isLegacyAutomaticRegion(region: OcrDetectionRegion): boolean {
  if (region.layout_data.manual === true) return false
  const assignment = balloonAssignment(region)
  const detection = region.layout_data.detection
  if (!detection || typeof detection !== 'object' || Array.isArray(detection)) return false
  if (!assignment) return true
  return assignment.status === 'unavailable'
}

function balloonAssignment(region: OcrRegionLayout): Record<string, unknown> | null {
  const detection = region.layout_data.detection
  if (!detection || typeof detection !== 'object' || Array.isArray(detection)) return null
  const assignment = (detection as Record<string, unknown>).balloon_assignment
  if (!assignment || typeof assignment !== 'object' || Array.isArray(assignment)) return null
  return assignment as Record<string, unknown>
}

export function buildPageWorkflowRequest(stage: PageWorkflowStage, startsWithDetection = false): PageWorkflowRequest {
  if (stage === 'ocr') return {
    start_stage: startsWithDetection ? 'detection' : 'ocr',
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

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
// Match backend/services/detection/layout.py; migration is user-confirmed.
const UNIFIED_LAYOUT_VERSION = 2

export function pageOcrNeedsDetection(regions: OcrDetectionRegion[]): boolean {
  if (regions.length === 0) return true
  return regions.some(region => !region.locked && isLegacyAutomaticRegion(region))
}

function isLegacyAutomaticRegion(region: OcrDetectionRegion): boolean {
  if (region.layout_data.manual === true) return false
  const detection = region.layout_data.detection
  if (!detection || typeof detection !== 'object' || Array.isArray(detection)) return false
  if ((detection as Record<string, unknown>).detector === 'rfdetr') return false
  // OpenCV is a deliberately chosen fallback, not an unfinished migration.
  return (detection as Record<string, unknown>).detector !== 'opencv-fallback'
}

export function unifiedLayoutNeedsDetection(regions:OcrDetectionRegion[], config:Record<string,unknown>):boolean {
  const defaults={text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9}
  return regions.some(region => {
    if(region.locked || region.layout_data.manual === true) return false
    const detection=region.layout_data.detection as Record<string,unknown> | undefined
    if(!detection) return false // Legacy API-created/manual frames have no detection provenance.
    if(detection.detector!=='rfdetr' || detection.layout_version!==UNIFIED_LAYOUT_VERSION) return true
    const settings=detection.layout_settings as Record<string,unknown> | undefined
    return Object.entries(defaults).some(([name,fallback]) => Number(settings?.[name])!==Number(config[name] ?? fallback))
  })
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

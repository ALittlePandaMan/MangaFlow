import {describe, expect, it} from 'vitest'

import {buildPageWorkflowRequest, canRunPageWorkflowStage, lockedLegacyOcrRegionCount, pageOcrGroupingUnavailable, pageOcrNeedsDetection, pageWorkflowTargetView} from './pageWorkflow'

describe('page workflow behavior', () => {
  it('allows translation and background repair immediately after OCR', () => {
    expect(canRunPageWorkflowStage('translation', 1)).toBe(true)
    expect(canRunPageWorkflowStage('inpainting', 1)).toBe(true)
    expect(canRunPageWorkflowStage('rendering', 1)).toBe(false)
    expect(canRunPageWorkflowStage('inpainting', 0)).toBe(false)
  })

  it('opens the result view for translation and inpainting', () => {
    expect(pageWorkflowTargetView('translation')).toBe('translated')
    expect(pageWorkflowTargetView('inpainting')).toBe('clean')
    expect(pageWorkflowTargetView('ocr')).toBeNull()
    expect(pageWorkflowTargetView('rendering')).toBeNull()
  })

  it('renders once after rebuilding the clean image', () => {
    expect(buildPageWorkflowRequest('inpainting')).toEqual({
      start_stage: 'mask',
      end_stage: 'rendering',
      force: true,
      options: {rebuild_clean: true},
    })
  })

  it('redetects before OCR when a page needs legacy bubble grouping', () => {
    expect(buildPageWorkflowRequest('ocr', true)).toEqual({
      start_stage: 'detection',
      end_stage: 'ocr',
      force: true,
      options: {crop_padding: 4},
    })
  })

  it('keeps OCR-only reruns after regions have bubble assignment metadata', () => {
    expect(buildPageWorkflowRequest('ocr')).toEqual({
      start_stage: 'ocr',
      end_stage: 'ocr',
      force: true,
      options: {crop_padding: 4},
    })
  })

  it('detects empty and legacy pages but preserves manual, locked, and migrated regions', () => {
    expect(pageOcrNeedsDetection([])).toBe(true)
    expect(pageOcrNeedsDetection([{locked:false, layout_data:{detection:{}}}])).toBe(true)
    expect(pageOcrNeedsDetection([{locked:false, layout_data:{manual:true}}])).toBe(false)
    expect(pageOcrNeedsDetection([{locked:true, layout_data:{detection:{}}}])).toBe(false)
    expect(pageOcrNeedsDetection([{
      locked:false,
      layout_data:{detection:{balloon_assignment:{status:'outside', bubble_id:null}}},
    }])).toBe(false)
    expect(pageOcrNeedsDetection([{
      locked:false,
      layout_data:{detection:{balloon_assignment:{status:'unavailable', bubble_id:null}}},
    }])).toBe(true)
    expect(pageOcrNeedsDetection([{
      locked:false,
      layout_data:{detection:{balloon_assignment:{status:'disabled', bubble_id:null}}},
    }])).toBe(false)
    expect(lockedLegacyOcrRegionCount([{locked:true, layout_data:{detection:{}}}])).toBe(1)
    expect(lockedLegacyOcrRegionCount([{locked:true, layout_data:{manual:true}}])).toBe(0)
  })

  it('only reports bubble grouping unavailable from the refreshed assignment metadata', () => {
    expect(pageOcrGroupingUnavailable([{
      layout_data:{detection:{balloon_assignment:{status:'unavailable', reason:'model_unavailable'}}},
    }])).toBe(true)
    expect(pageOcrGroupingUnavailable([{
      layout_data:{detection:{balloon_assignment:{status:'assigned', bubble_id:'bubble-1'}}},
    }])).toBe(false)
    expect(pageOcrGroupingUnavailable([{layout_data:{detection:{}}}])).toBe(false)
    expect(pageOcrGroupingUnavailable([{layout_data:{manual:true}}])).toBe(false)
  })
})

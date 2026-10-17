import {describe, expect, it} from 'vitest'

import {buildPageWorkflowRequest, canRunPageWorkflowStage, pageWorkflowTargetView} from './pageWorkflow'

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

  it('always starts whole-page OCR with detection', () => {
    expect(buildPageWorkflowRequest('ocr')).toEqual({
      start_stage: 'detection',
      end_stage: 'ocr',
      force: true,
      options: {crop_padding: 4},
    })
  })

})

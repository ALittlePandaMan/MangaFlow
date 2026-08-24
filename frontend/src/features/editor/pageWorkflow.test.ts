import {describe, expect, it} from 'vitest'

import {buildPageWorkflowRequest, pageWorkflowTargetView} from './pageWorkflow'

describe('page workflow behavior', () => {
  it('opens the result view for translation and inpainting', () => {
    expect(pageWorkflowTargetView('translation')).toBe('translated')
    expect(pageWorkflowTargetView('inpainting')).toBe('clean')
    expect(pageWorkflowTargetView('ocr')).toBeNull()
    expect(pageWorkflowTargetView('rendering')).toBeNull()
  })

  it('renders once after rebuilding the clean image', () => {
    expect(buildPageWorkflowRequest('inpainting', false)).toEqual({
      start_stage: 'mask',
      end_stage: 'rendering',
      force: true,
      options: {rebuild_clean: true},
    })
  })
})

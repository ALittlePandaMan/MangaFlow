import {describe, expect, it} from 'vitest'

import {buildPageWorkflowRequest, canRunPageWorkflowStage, pageOcrNeedsDetection, pageWorkflowTargetView, unifiedLayoutNeedsDetection} from './pageWorkflow'

describe('page workflow behavior', () => {
  it('offers an explicit RF-DETR migration once, not destructive redetection on every OCR', () => {
    const config={text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9}
    const current={locked:false,layout_data:{detection:{detector:'rfdetr',layout_version:2,layout_settings:config,balloon_assignment:{status:'unassigned'}}}}
    expect(pageOcrNeedsDetection([current])).toBe(false)
    expect(unifiedLayoutNeedsDetection([current],config)).toBe(false)
    expect(unifiedLayoutNeedsDetection([{...current,layout_data:{detection:{...current.layout_data.detection,layout_version:1}}}],config)).toBe(true)
    expect(unifiedLayoutNeedsDetection([current],{...config,text_threshold:0.3})).toBe(true)
    expect(unifiedLayoutNeedsDetection([{locked:false,layout_data:{detection:{balloon_grouping_version:3}}}],config)).toBe(true)
    expect(unifiedLayoutNeedsDetection([{...current,locked:true}],{text_threshold:0.3})).toBe(false)
    expect(unifiedLayoutNeedsDetection([{...current,layout_data:{manual:true}}],config)).toBe(false)
  })
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

  it('redetects before OCR when a page needs migration to unified layout', () => {
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
      layout_data:{detection:{balloon_grouping_version:3, balloon_assignment:{status:'outside', bubble_id:null}}},
    }])).toBe(true)
    expect(pageOcrNeedsDetection([{
      locked:false,
      layout_data:{detection:{balloon_assignment:{status:'unavailable', bubble_id:null}}},
    }])).toBe(true)
    expect(pageOcrNeedsDetection([{
      locked:false,
      layout_data:{detection:{detector:'opencv-fallback', balloon_assignment:{status:'disabled', bubble_id:null}}},
    }])).toBe(false)
  })

  it('offers user-confirmed redetection for old assigned and missed balloons', () => {
    for (const status of ['assigned', 'outside', 'ambiguous']) {
      const old = {locked:false, layout_data:{detection:{balloon_assignment:{status}}}}
      expect(pageOcrNeedsDetection([old])).toBe(true)
      expect(pageOcrNeedsDetection([{...old, layout_data:{detection:{
        balloon_grouping_version:1, balloon_assignment:{status},
      }}}])).toBe(true)
      expect(pageOcrNeedsDetection([{...old, layout_data:{detection:{
        balloon_grouping_version:2, balloon_assignment:{status},
      }}}])).toBe(true)
      expect(pageOcrNeedsDetection([{...old, layout_data:{detection:{
        balloon_grouping_version:3, balloon_assignment:{status},
      }}}])).toBe(true)
      expect(pageOcrNeedsDetection([{...old, locked:true}])).toBe(false)
        expect(pageOcrNeedsDetection([{...old, layout_data:{...old.layout_data, manual:true}}])).toBe(false)
    }
  })


})

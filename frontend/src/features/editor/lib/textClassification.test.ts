import {describe, expect, it} from 'vitest'
import {classificationNeedsDetection} from './textClassification'

const summary = {version: 1, status: 'completed', excluded_count: 3, policy: {enabled: true, filter_sfx: true, min_sfx_confidence: 0.6}}

describe('classification policy changes', () => {
  it('does not repeat detection when the saved policy is current', () => {
    expect(classificationNeedsDetection(summary, {})).toBe(false)
  })
  it('upgrades existing pages once when classification is enabled', () => {
    expect(classificationNeedsDetection(undefined, {})).toBe(true)
    expect(classificationNeedsDetection(undefined, {text_classification: false})).toBe(false)
  })
  it('redetects to restore SFX when filtering is disabled', () => {
    expect(classificationNeedsDetection(summary, {text_classification: {filter_sfx: false}})).toBe(true)
    expect(classificationNeedsDetection(summary, {text_classification: false})).toBe(true)
  })
  it('applies a changed confidence threshold', () => {
    expect(classificationNeedsDetection(summary, {text_classification: {min_sfx_confidence: 0.8}})).toBe(true)
  })
})

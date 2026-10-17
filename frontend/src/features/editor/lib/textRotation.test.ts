import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {effectiveTextRotation} from './textRotation'

const fixture=(patch: Partial<TextRegion>={}) => ({rotation:-28,perspective_warp:true,
  translated_polygon:[[0,0],[100,0],[100,50],[0,50]],
  layout_data:{text_rotation:{version:1,source:'detector',role:'free_text',angle:-28}},...patch} as TextRegion)

describe('independent automatic and user rotations', () => {
  it('does not apply a detector angle twice in perspective and restores it in standard mode', () => {
    const region=fixture(), before=structuredClone(region)
    expect(effectiveTextRotation(region)).toBe(0)
    expect(effectiveTextRotation({...region,perspective_warp:false})).toBe(-28)
    expect(region).toEqual(before)
  })
  it.each([0,-28,17])('preserves a user-authored %s degree rotation', rotation => {
    expect(effectiveTextRotation(fixture({rotation,layout_data:{text_rotation:{source:'user',angle:rotation}}}))).toBe(rotation)
  })
  it('does not infer automatic provenance from a tilted box or stale metadata', () => {
    expect(effectiveTextRotation(fixture({layout_data:{}}))).toBe(-28)
    expect(effectiveTextRotation(fixture({rotation:12}))).toBe(12)
    expect(effectiveTextRotation(fixture({translated_polygon:[[0,0],[1,1],[2,2]]}))).toBe(-28)
  })
})

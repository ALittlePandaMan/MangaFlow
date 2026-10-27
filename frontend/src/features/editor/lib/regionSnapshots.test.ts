import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {preserveLocalRegionGeometry, reconcileRegionSnapshot} from './regionSnapshots'

const before = {
  id:'sample',bbox:[20,20,320,260],polygon:[[20,20],[340,20],[340,280],[20,280]],
  translated_bbox:[20,20,320,260],translated_polygon:[[20,20],[340,20],[340,280],[20,280]],
  rotation:0,orientation:'vertical',perspective_warp:false,locked:false,
  layout_data:{typography_state:{target:'auto',orientation:'auto',target_orientation:'vertical',angle:0,
    target_bbox:[20,20,320,260],target_polygon:[[20,20],[340,20],[340,280],[20,280]]}},
} as unknown as TextRegion
const after = {...before,rotation:12,orientation:'horizontal',translated_bbox:[55,70,235,120],
  translated_polygon:[[75,70],[290,110],[270,190],[55,150]],
  layout_data:{typography_state:{target:'auto',orientation:'auto',target_orientation:'horizontal',angle:12,
    target_bbox:[55,70,235,120],target_polygon:[[75,70],[290,110],[270,190],[55,150]]},
    translation_geometry_sync:{direction:'automatic_typography',pending:false,revision:1,
      previous_target_bbox:before.translated_bbox,previous_target_polygon:before.translated_polygon,
      previous_rotation:before.rotation,previous_orientation:before.orientation,
      source_bbox:before.bbox,source_polygon:before.polygon}}} as TextRegion

describe('published OCR geometry',()=>{
  it('retries deferred geometry after another region changes during the request',()=>{
    const deferred = reconcileRegionSnapshot([after],[before])[0]
    expect(deferred.rotation).toBe(0)
    expect(deferred.layout_data).not.toHaveProperty('translation_geometry_sync')
    expect(deferred.layout_data.typography_state).toEqual(before.layout_data.typography_state)
    const refreshed = preserveLocalRegionGeometry([after],[deferred])[0]
    expect(refreshed.rotation).toBe(12)
    expect(refreshed.translated_polygon).toEqual(after.translated_polygon)
    expect(refreshed.orientation).toBe('horizontal')
    expect(refreshed.layout_data.translation_geometry_sync).toEqual(after.layout_data.translation_geometry_sync)
  })

  const later = {...after,rotation:13,
    layout_data:{...after.layout_data,
      typography_state:{...after.layout_data.typography_state as object,angle:13},
      translation_geometry_sync:{...after.layout_data.translation_geometry_sync as object,
        revision:2, previous_rotation:after.rotation, previous_orientation:after.orientation,
        previous_target_bbox:after.translated_bbox, previous_target_polygon:after.translated_polygon}}} as TextRegion

  it('adopts a newer automatic generation after missing an intermediate one',()=>{
    expect(preserveLocalRegionGeometry([later],[before])[0].rotation).toBe(13)
    expect(preserveLocalRegionGeometry([later],[after])[0].rotation).toBe(13)
  })

  it.each([
    {rotation:7}, {orientation:'horizontal'}, {translated_bbox:[1,2,30,40]},
    {translated_polygon:[[21,20],[340,20],[340,280],[20,280]]}, {locked:true}, {perspective_warp:true},
    {layout_data:{...before.layout_data,text_rotation:{source:'user',angle:0}}},
    {layout_data:{...before.layout_data,typography_state:{...before.layout_data.typography_state as object,target:'manual'}}},
  ])('keeps local/manual values across missed generations: %j',patch=>{
    const current = {...before,...patch} as TextRegion
    const merged = preserveLocalRegionGeometry([later],[current])[0]
    for (const key of ['rotation','orientation','translated_bbox','translated_polygon','perspective_warp'] as const) {
      expect(merged[key]).toEqual(current[key])
    }
    expect(merged.layout_data.typography_state).toEqual(current.layout_data.typography_state)
  })

  it('accepts manual provenance even when explicitly setting the same angle',()=>{
    const manual = {...before,layout_data:{...before.layout_data,
      typography_state:{...before.layout_data.typography_state as object,target:'manual'},
      text_rotation:{source:'user',angle:0},translation_geometry_sync:{direction:'manual_translation',revision:0,pending:false}}} as TextRegion
    const merged = preserveLocalRegionGeometry([manual],[before])[0]
    expect(merged.layout_data.text_rotation).toEqual(manual.layout_data.text_rotation)
    expect(merged.layout_data.typography_state).toEqual(manual.layout_data.typography_state)
  })
})

import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {hasNewRepairGeometry, hasNewSourceRepairGeometry} from './regionGeometry'
import {preserveLocalRegionGeometry} from './regionSnapshots'

const current = {
  bbox:[10,20,80,100], polygon:[[10,20],[90,20],[90,120],[10,120]],
  layout_data:{translation_geometry_sync:{pending:true,revision:1}},
} as unknown as TextRegion
const synced = {...current, layout_data:{translation_geometry_sync:{
  pending:false,revision:2,source_bbox:current.bbox,source_polygon:current.polygon,
}}}

describe('repair geometry synchronization', () => {
  it('accepts a newer completed synchronization for the current source geometry', () => {
    expect(hasNewRepairGeometry(synced,current)).toBe(true)
  })
  it('does not treat ordinary repair refreshes as geometry changes', () => {
    expect(hasNewRepairGeometry(synced,synced)).toBe(false)
    expect(hasNewRepairGeometry({...synced,layout_data:{}},current)).toBe(false)
  })
  it('rejects incomplete synchronization and older generations', () => {
    expect(hasNewRepairGeometry({...synced,layout_data:{translation_geometry_sync:{...synced.layout_data.translation_geometry_sync,pending:true}}},current)).toBe(false)
    expect(hasNewRepairGeometry(current,synced)).toBe(false)
  })
  it('rejects a response for source geometry that was edited again', () => {
    expect(hasNewRepairGeometry(synced,{...current,bbox:[10,20,80,90]})).toBe(false)
    expect(hasNewRepairGeometry({...synced,polygon:[[10,20],[90,20],[90,110],[10,110]]},current)).toBe(false)
  })

  const target = {...current, translated_bbox:[25,30,60,90],translated_polygon:[[25,30],[85,30],[85,120],[25,120]]}
  const reverse = {...target, bbox:target.translated_bbox,polygon:target.translated_polygon,
    layout_data:{translation_geometry_sync:{direction:'translated_to_source',pending:false,revision:2,
      previous_source_bbox:target.bbox,previous_source_polygon:target.polygon,
      translated_bbox:target.translated_bbox,translated_polygon:target.translated_polygon,
      source_bbox:target.translated_bbox,source_polygon:target.translated_polygon}}}

  it('accepts reverse repair geometry without treating it as a target reset', () => {
    expect(hasNewSourceRepairGeometry(reverse,target)).toBe(true)
    expect(hasNewRepairGeometry(reverse,target)).toBe(false)
    expect(hasNewSourceRepairGeometry(reverse,reverse)).toBe(false)
    expect(hasNewSourceRepairGeometry(synced,current)).toBe(false)
  })
  it('rejects reverse sync after another source or target edit', () => {
    expect(hasNewSourceRepairGeometry(reverse,{...target,bbox:[10,20,80,90]})).toBe(false)
    expect(hasNewSourceRepairGeometry(reverse,{...target,polygon:[[10,20],[90,20],[90,110],[10,110]]})).toBe(false)
    expect(hasNewSourceRepairGeometry(reverse,{...target,translated_bbox:[25,30,70,90]})).toBe(false)
    expect(hasNewSourceRepairGeometry(reverse,{...target,translated_polygon:current.polygon})).toBe(false)
    expect(hasNewSourceRepairGeometry({...reverse,polygon:current.polygon},target)).toBe(false)
  })
  it('rejects pending or old reverse sync responses', () => {
    const state = reverse.layout_data.translation_geometry_sync
    expect(hasNewSourceRepairGeometry({...reverse,layout_data:{translation_geometry_sync:{...state,pending:true}}},target)).toBe(false)
    expect(hasNewSourceRepairGeometry({...reverse,layout_data:{translation_geometry_sync:{...state,revision:1}}},target)).toBe(false)
  })
  it('adopts OCR typography only when source and previous target still match', () => {
    const before={...target,rotation:0,perspective_warp:false,orientation:'vertical'} as TextRegion
    const incoming={...before,rotation:12,orientation:'horizontal',translated_polygon:[[20,20],[100,35],[90,125],[10,110]],
      layout_data:{translation_geometry_sync:{direction:'automatic_typography',pending:false,revision:2,
        source_bbox:before.bbox,source_polygon:before.polygon,
        previous_target_bbox:before.translated_bbox,previous_target_polygon:before.translated_polygon,
        previous_rotation:before.rotation,previous_orientation:before.orientation}}} as TextRegion
    expect(hasNewRepairGeometry(incoming,before)).toBe(true)
    expect(preserveLocalRegionGeometry([incoming],[before])[0].rotation).toBe(12)
    for (const patch of [{translated_bbox:[1,2,3,4]},{rotation:7},{orientation:'horizontal' as const},{perspective_warp:true}]) {
      const edited={...before,...patch}
      expect(hasNewRepairGeometry(incoming,edited)).toBe(false)
      const merged=preserveLocalRegionGeometry([incoming],[edited])[0]
      expect(merged.rotation).toBe(edited.rotation)
      expect(merged.translated_polygon).toEqual(edited.translated_polygon)
      expect(merged.orientation).toBe(edited.orientation)
    }
  })
})

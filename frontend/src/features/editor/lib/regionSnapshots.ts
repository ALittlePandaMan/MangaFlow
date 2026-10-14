import type {TextRegion} from '../../../types'
import {hasNewRepairGeometry, hasNewSourceRepairGeometry} from './regionGeometry'

export function preserveLocalRegionGeometry(nextRegions: TextRegion[], currentRegions: TextRegion[], allowRepairSync = true) {
  if (!currentRegions.length) return nextRegions
  const currentById = new Map(currentRegions.map(region => [region.id, region]))
  return nextRegions.map(region => {
    const current = currentById.get(region.id)
    if (!current) return region
    const synchronized = allowRepairSync && hasNewRepairGeometry(region, current)
    const sourceSynchronized = allowRepairSync && hasNewSourceRepairGeometry(region, current)
    return {
      ...region,
      bbox: [...(sourceSynchronized ? region : current).bbox],
      polygon: (sourceSynchronized ? region : current).polygon.map(point => [...point]),
      translated_bbox: [...(synchronized ? region : current).translated_bbox],
      translated_polygon: (synchronized ? region : current).translated_polygon.map(point => [...point]),
      rotation: (synchronized ? region : current).rotation,
      perspective_warp: (synchronized ? region : current).perspective_warp,
    }
  })
}

/**
 * A same-page response can have been requested before a local create/delete
 * finished. In that case the current ID set is newer than the response: merge
 * fresh server fields into matching regions without resurrecting or dropping
 * regions from the newer local list.
 */
export function reconcileRegionSnapshot(nextRegions: TextRegion[], currentRegions: TextRegion[]) {
  const nextById = new Map(nextRegions.map(region => [region.id, region]))
  return currentRegions.map(current => {
    const next = nextById.get(current.id)
    return next ? preserveLocalRegionGeometry([next], [current], false)[0] : current
  })
}

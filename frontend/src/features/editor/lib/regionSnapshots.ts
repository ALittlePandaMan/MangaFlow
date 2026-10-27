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
    const incomingSync = region.layout_data.translation_geometry_sync as Record<string,unknown> | undefined
    const currentSync = current.layout_data.translation_geometry_sync as Record<string,unknown> | undefined
    const autoSync = incomingSync?.direction === 'automatic_typography'
    // A deferred response must not acknowledge a geometry generation that was
    // not applied. Its target baseline is needed to safely retry the refresh.
    const layout = {...region.layout_data}
    const currentRevision = typeof currentSync?.revision === 'number' ? currentSync.revision : 0
    if (!synchronized && !sourceSynchronized && typeof incomingSync?.revision === 'number' && incomingSync.revision !== currentRevision) {
      for (const key of ['translation_geometry_sync', 'typography_state', 'typography_analysis', 'text_rotation']) {
        if (Object.hasOwn(current.layout_data, key)) layout[key] = current.layout_data[key]
        else delete layout[key]
      }
    }
    return {
      ...region,
      layout_data: layout,
      bbox: [...(sourceSynchronized ? region : current).bbox],
      polygon: (sourceSynchronized ? region : current).polygon.map(point => [...point]),
      translated_bbox: [...(synchronized ? region : current).translated_bbox],
      translated_polygon: (synchronized ? region : current).translated_polygon.map(point => [...point]),
      rotation: (synchronized ? region : current).rotation,
      perspective_warp: (synchronized ? region : current).perspective_warp,
      orientation: autoSync && !synchronized ? current.orientation : region.orientation,
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

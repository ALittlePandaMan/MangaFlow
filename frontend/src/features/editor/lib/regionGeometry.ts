import type {TextRegion} from '../../../types'

/** A missed automatic generation is safe only while the published baseline is intact. */
function hasUneditedAutomaticTarget(region: TextRegion): boolean {
  const state = region.layout_data.typography_state as Record<string, unknown> | undefined
  const evidence = region.layout_data.typography_analysis as Record<string, unknown> | undefined
  const orientation = state?.target_orientation ?? (evidence?.orientation_confident ? evidence.orientation : undefined)
  return state?.target === 'auto' && state.orientation === 'auto' && orientation === region.orientation &&
    state.angle === region.rotation &&
    JSON.stringify(state.target_bbox) === JSON.stringify(region.translated_bbox) &&
    JSON.stringify(state.target_polygon) === JSON.stringify(region.translated_polygon)
}

/** Adopt published repair/OCR geometry only while its source inputs still match. */
export function hasNewRepairGeometry(next: TextRegion, current: TextRegion): boolean {
  const incoming = next.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const previous = current.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const revision = incoming?.revision
  const previousRevision = typeof previous?.revision === 'number' ? previous.revision : 0
  if (incoming?.direction === 'automatic_typography') {
    const state = current.layout_data.typography_state as Record<string, unknown> | undefined
    const rotation = current.layout_data.text_rotation as Record<string, unknown> | undefined
    if (current.perspective_warp || current.locked || state?.target === 'manual' || rotation?.source === 'user') return false
    const previousTargetMatches = JSON.stringify(incoming.previous_target_bbox) === JSON.stringify(current.translated_bbox) &&
       JSON.stringify(incoming.previous_target_polygon) === JSON.stringify(current.translated_polygon) &&
       incoming.previous_rotation === current.rotation && incoming.previous_orientation === current.orientation
    if (!previousTargetMatches && !hasUneditedAutomaticTarget(current)) return false
  }
  return incoming?.direction !== 'translated_to_source' &&
    incoming?.pending === false && typeof revision === 'number' && revision > previousRevision &&
    JSON.stringify(incoming.source_bbox) === JSON.stringify(current.bbox) &&
    JSON.stringify(incoming.source_polygon) === JSON.stringify(current.polygon) &&
    JSON.stringify(next.bbox) === JSON.stringify(current.bbox) &&
    JSON.stringify(next.polygon) === JSON.stringify(current.polygon)
}

/** Adopt a repaired target frame without overwriting newer edits or its style. */
export function hasNewSourceRepairGeometry(next: TextRegion, current: TextRegion): boolean {
  const incoming = next.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const previous = current.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const revision = incoming?.revision
  const previousRevision = typeof previous?.revision === 'number' ? previous.revision : 0
  return incoming?.direction === 'translated_to_source' && incoming.pending === false &&
    typeof revision === 'number' && revision > previousRevision &&
    JSON.stringify(incoming.previous_source_bbox) === JSON.stringify(current.bbox) &&
    JSON.stringify(incoming.previous_source_polygon) === JSON.stringify(current.polygon) &&
    JSON.stringify(incoming.translated_bbox) === JSON.stringify(current.translated_bbox) &&
    JSON.stringify(incoming.translated_polygon) === JSON.stringify(current.translated_polygon) &&
    JSON.stringify(next.bbox) === JSON.stringify(current.translated_bbox) &&
    JSON.stringify(next.polygon) === JSON.stringify(current.translated_polygon)
}

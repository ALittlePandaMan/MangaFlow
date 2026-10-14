import type {TextRegion} from '../../../types'

/** Only a newer successful repair may replace the local translated frame. */
export function hasNewRepairGeometry(next: TextRegion, current: TextRegion): boolean {
  const incoming = next.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const previous = current.layout_data.translation_geometry_sync as Record<string, unknown> | undefined
  const revision = incoming?.revision
  const previousRevision = typeof previous?.revision === 'number' ? previous.revision : 0
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

import type {TextRegion} from '../../../types'
import {isPerspectiveQuad} from './perspectiveText'

/** The warp supplies an automatic detector angle, not an extra user rotation. */
export function effectiveTextRotation(region: TextRegion): number {
  const provenance = region.layout_data?.text_rotation as {version?: unknown; source?: unknown; role?: unknown; angle?: unknown} | undefined
  const automatic = provenance?.source === 'detector' && provenance.role === 'free_text' ||
    provenance?.version === 2 && (provenance.source === 'detector' || provenance.source === 'segmentation')
  if (region.perspective_warp && isPerspectiveQuad(region.translated_polygon) &&
      automatic && typeof provenance?.angle === 'number' && Number.isFinite(provenance.angle) &&
      Math.abs(provenance.angle-region.rotation) <= .01) return 0
  return region.rotation
}

import type {TextRegion} from '../../../types'

// Legacy exports stored a smaller bubble-inscribed text frame here. The
// visible target geometry is now authoritative, including for old projects.
export function shapePreviewRegion(region: TextRegion): TextRegion {
  return region
}

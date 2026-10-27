import type {TextRegion} from '../../../types'
import {useTextStyleStore} from '../../settings/textStyle'

export type RegionChange = {id: string, patch: Partial<TextRegion>}

function geometryOf(region: TextRegion, translated: boolean) {
  const bbox = translated && region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
  const polygon = translated && region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
  return {bbox, polygon: polygon.length >= 3 ? polygon : [[bbox[0], bbox[1]], [bbox[0]+bbox[2], bbox[1]], [bbox[0]+bbox[2], bbox[1]+bbox[3]], [bbox[0], bbox[1]+bbox[3]]]}
}

export function moveRegions(regions: TextRegion[], translated: boolean, dx: number, dy: number): RegionChange[] {
  return regions.filter(region => !region.locked).map(region => {
    const {bbox, polygon} = geometryOf(region, translated)
    const movedBox = [bbox[0]+dx, bbox[1]+dy, bbox[2], bbox[3]]
    const movedPolygon = polygon.map(([x,y]) => [x+dx,y+dy])
    return {id:region.id, patch:translated ? {translated_bbox:movedBox,translated_polygon:movedPolygon} : {bbox:movedBox,polygon:movedPolygon}}
  })
}

/** Clamp the whole selection once, never individual boxes (which breaks spacing). */
export function constrainGroupMove(regions: TextRegion[], translated: boolean, dx: number, dy: number, width: number, height: number) {
  const boxes = regions.filter(region => !region.locked).map(region => geometryOf(region, translated).bbox)
  if (!boxes.length) return {x:0,y:0}
  const left = Math.min(...boxes.map(box => box[0])), top = Math.min(...boxes.map(box => box[1]))
  const right = Math.max(...boxes.map(box => box[0]+box[2])), bottom = Math.max(...boxes.map(box => box[1]+box[3]))
  // Legacy out-of-page selections must still be able to move toward the page.
  return {x:Math.max(Math.min(0,-left),Math.min(Math.max(0,width-right),dx)),
    y:Math.max(Math.min(0,-top),Math.min(Math.max(0,height-bottom),dy))}
}

export function draftRegion(imageId: string, polygon: number[][], bbox: number[], regions: TextRegion[]): TextRegion {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2,'0')).join('')
  const id = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
  const readingOrder = regions.reduce((maximum,region) => Math.max(maximum,region.reading_order),0)+1
  const keys = new Set(regions.map(region => region.region_key))
  let keyNumber = regions.length+1
  while (keys.has(`R${String(keyNumber).padStart(3,'0')}`)) keyNumber++
  const {orientation, ...style} = useTextStyleStore.getState().defaults
  return {id,image_id:imageId,region_key:`R${String(keyNumber).padStart(3,'0')}`,polygon,bbox,
    translated_polygon:polygon.map(point => [...point]),translated_bbox:[...bbox],mask_url:null,source_text:'',translated_text:'',confidence:0,auto_orientation:orientation === 'auto',
    orientation:orientation === 'auto' ? (bbox[3] > bbox[2]*1.2 ? 'vertical' : 'horizontal') : orientation,reading_order:readingOrder,panel_id:null,bubble_id:null,
    region_type:'background_complex',...style,rotation:0,perspective_warp:false,opacity:1,locked:false,visible:true,
    needs_review:false,review_reasons:[],layout_warning:false,layout_data:{manual:true},updated_at:new Date().toISOString()}
}

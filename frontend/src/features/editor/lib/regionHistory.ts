import type {ImagePage,TextRegion} from '../../../types'

export interface RegionHistoryPreview {
  pageId:string
  ids:string[]
  before:TextRegion[]
  after:TextRegion[]
  sequence:number
  beforePage?:ImagePage
  afterPage?:ImagePage
}

/** Apply only this command's affected IDs, preserving unrelated live edits. */
export function previewRegionHistory(current:TextRegion[], step:RegionHistoryPreview, direction:'undo'|'redo') {
  const ids=new Set(step.ids)
  const replacements=new Map((direction==='undo'?step.before:step.after).map(region=>[region.id,region]))
  const next=current.flatMap(region=>{
    if (!ids.has(region.id)) return [region]
    const replacement=replacements.get(region.id)
    replacements.delete(region.id)
    return replacement?[replacement]:[]
  })
  next.push(...replacements.values())
  return next.sort((a,b)=>a.reading_order-b.reading_order)
}

export function invalidateHistoryPreview(page:ImagePage):ImagePage {
  return {...page,rendered_url:null,text_layer_url:null,text_layer_region_ids:null}
}

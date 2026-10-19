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

export function invalidateHistoryPreview(page:ImagePage):ImagePage {
  return {...page,rendered_url:null,text_layer_url:null,text_layer_region_ids:null}
}
